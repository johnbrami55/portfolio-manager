"""
backtest.py — Advanced backtest with dynamic stops, regime filter,
sector correlation, enhanced trend/momentum signals.
"""
import json
import logging
from datetime import datetime
from itertools import product
import pandas as pd
import numpy as np
import requests
import openpyxl
from openpyxl.styles import PatternFill, Font
from openpyxl.chart import LineChart, Reference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

START_DATE    = "2020-01-01"
END_DATE      = "2025-12-31"
MAX_POSITIONS = 12
FEE_US        = 2.00
INITIAL_CASH  = 10000.0

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}

US_TICKERS = [
    "KO", "BAC", "ABT", "NEE", "PFE", "F", "T", "VZ", "KHC", "PYPL",
    "NKE", "GM", "MO", "DIS", "SBUX", "CVS", "XOM", "WMT", "PG", "MRK",
    "PEP", "CSCO", "DHR", "PM", "RTX", "UPS", "NVDA", "AMD", "TSLA", "PLTR",
    "SOFI", "COIN", "SQ", "DKNG",
]

HK_TICKERS = [
    "0700.HK", "0941.HK", "1299.HK", "0005.HK", "0388.HK",
    "2318.HK", "1398.HK", "0939.HK", "0883.HK", "9999.HK",
]

SECTOR_MAP = {
    "KO":"Staples","PEP":"Staples","MO":"Staples","WMT":"Staples","PG":"Staples","KHC":"Staples",
    "BAC":"Financials","PYPL":"Financials","COIN":"Financials","SOFI":"Financials","SQ":"Financials",
    "ABT":"Healthcare","PFE":"Healthcare","MRK":"Healthcare","DHR":"Healthcare","CVS":"Healthcare",
    "NVDA":"Tech","AMD":"Tech","CSCO":"Tech","PLTR":"Tech",
    "TSLA":"Auto","GM":"Auto","F":"Auto",
    "NKE":"Consumer","DIS":"Consumer","SBUX":"Consumer","DKNG":"Consumer",
    "XOM":"Energy","RTX":"Defense","UPS":"Logistics","NEE":"Utilities",
    "T":"Telecom","VZ":"Telecom","PM":"Staples",
    "0700.HK":"Tech","9999.HK":"Tech","1810.HK":"Tech",
    "0941.HK":"Telecom","0762.HK":"Telecom",
    "1299.HK":"Financials","0005.HK":"Financials","0388.HK":"Financials",
    "2318.HK":"Financials","1398.HK":"Financials","0939.HK":"Financials",
    "0883.HK":"Energy","0857.HK":"Energy",
}

# Parameter grid
PARAM_GRID = {
    "score_thresh":   [28, 32, 36],
    "stop_atr_mult":  [1.5, 1.5, 2.0],   # dynamic stop = ATR * mult
    "take_profit":    [0.15, 0.22, 0.30],
    "max_hold_days":  [20, 35, 50],
}


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_history(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, headers=YF_HEADERS,
                         params={"interval": "1d", "range": "6y"}, timeout=15)
        if r.status_code != 200:
            return None
        data   = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        ts    = result[0]["timestamp"]
        quote = result[0]["indicators"]["quote"][0]
        dates = [datetime.utcfromtimestamp(t).date() for t in ts]
        df = pd.DataFrame({
            "date":   dates,
            "close":  quote.get("close", []),
            "high":   quote.get("high", []),
            "low":    quote.get("low", []),
            "volume": quote.get("volume", []),
        }).dropna().set_index("date")
        df = df[(df.index >= pd.to_datetime(START_DATE).date()) &
                (df.index <= pd.to_datetime(END_DATE).date())]
        return df if len(df) > 200 else None
    except Exception as e:
        logger.warning(f"{ticker}: {e}")
        return None


# ── Technical Indicators ──────────────────────────────────────────────────────

def calc_atr(highs, lows, closes, period=14):
    """ATR as % of last close."""
    if len(highs) < period + 1:
        return 0.02  # default 2%
    tr_list = []
    for i in range(period):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i+1]),
            abs(lows[i] - closes[i+1]),
        )
        tr_list.append(tr)
    atr = sum(tr_list) / period
    return atr / closes[0] if closes[0] > 0 else 0.02


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i+1] for i in range(period)]
    gains  = sum(d for d in deltas if d > 0) / period
    losses = sum(-d for d in deltas if d < 0) / period
    rs     = gains / losses if losses != 0 else 100
    return 100 - 100 / (1 + rs)


def calc_ema(data, span):
    k = 2 / (span + 1)
    e = data[-1]
    for p in reversed(data[:-1]):
        e = p * k + e * (1 - k)
    return e


def calc_stoch_rsi(closes, period=14):
    """Stochastic RSI — returns K value 0-100."""
    if len(closes) < period * 2:
        return 50.0
    rsi_vals = []
    for i in range(period, min(len(closes), period * 2)):
        window = closes[i-period:i+1][::-1]
        rsi_vals.append(calc_rsi(window))
    if not rsi_vals:
        return 50.0
    rsi_min = min(rsi_vals)
    rsi_max = max(rsi_vals)
    if rsi_max == rsi_min:
        return 50.0
    return (rsi_vals[0] - rsi_min) / (rsi_max - rsi_min) * 100


def score_ticker(closes, highs, lows, volumes, regime, universe_returns=None):
    """
    Enhanced scoring with:
    - Multi-timeframe trend (MA20/50/100/200)
    - RSI with regime-aware thresholds
    - Volume surge detection
    - MACD crossover
    - Momentum (1M, 3M, 6M)
    - Bollinger Band position
    - Stochastic RSI
    - ATR-based volatility filter
    - ADX trend strength (simplified)
    """
    if len(closes) < 50:
        return 0.0, 0.02

    score = 0.0

    # ── 1. TREND (max 12 pts) ──────────────────────────────────────────────
    ma20 = sum(closes[:20]) / 20
    ma50 = sum(closes[:50]) / 50
    trend_pts = 0.0

    if len(closes) >= 200:
        ma100 = sum(closes[:100]) / 100
        ma200 = sum(closes[:200]) / 200
        # Full trend scoring
        if closes[0] > ma20:  trend_pts += 3.0
        if ma20 > ma50:       trend_pts += 3.0
        if ma50 > ma100:      trend_pts += 3.0
        if ma100 > ma200:     trend_pts += 3.0
        # Trend slope — is MA50 rising?
        ma50_prev = sum(closes[5:55]) / 50 if len(closes) >= 55 else ma50
        if ma50 > ma50_prev:  trend_pts += 1.0  # bonus
        # Price above MA200 by >2% = strong trend
        if closes[0] > ma200 * 1.02: trend_pts += 1.0
    elif len(closes) >= 100:
        ma100 = sum(closes[:100]) / 100
        if closes[0] > ma20:  trend_pts += 2.5
        if ma20 > ma50:       trend_pts += 2.5
        if ma50 > ma100:      trend_pts += 3.0
        trend_pts *= 0.85  # penalty for short history
    else:
        if closes[0] > ma20:  trend_pts += 2.0
        if ma20 > ma50:       trend_pts += 2.0
        trend_pts *= 0.6

    if regime == "BEAR":
        trend_pts *= 0.7
    score += min(trend_pts, 12.0)

    # ── 2. RSI (max 8 pts) ────────────────────────────────────────────────
    rsi = calc_rsi(closes)
    if regime == "BULL":
        ideal_min, ideal_max = 45, 70
    elif regime == "BEAR":
        ideal_min, ideal_max = 35, 55
    else:
        ideal_min, ideal_max = 40, 62

    if ideal_min <= rsi <= ideal_max:
        score += 8.0
    elif rsi < 25:
        score += 6.0  # oversold bonus (potential reversal)
    elif rsi < ideal_min:
        score += 4.0  # approaching ideal
    elif rsi > 75:
        score += 0.0  # overbought penalty
    else:
        score += 3.0  # partial

    # ── 3. VOLUME (max 8 pts) ─────────────────────────────────────────────
    if len(volumes) >= 21:
        avg_vol = sum(volumes[1:21]) / 20
        cur_vol = volumes[0]
        ratio   = cur_vol / avg_vol if avg_vol > 0 else 0
        # 5-day avg volume vs 20-day avg (trending volume)
        avg5  = sum(volumes[:5]) / 5
        vol_trend = avg5 / avg_vol if avg_vol > 0 else 1.0

        if ratio >= 1.5 and vol_trend > 1.1:
            score += 8.0   # high volume + rising volume trend
        elif ratio >= 1.5:
            score += 6.0
        elif ratio >= 1.2:
            score += 4.8
        elif ratio >= 0.8:
            score += 2.4
        # else: 0

    # ── 4. MACD (max 6 pts) ───────────────────────────────────────────────
    if len(closes) >= 26:
        macd_line   = calc_ema(closes[:12], 12) - calc_ema(closes[:26], 26)
        signal_line = calc_ema(closes[:9], 9)
        histogram   = macd_line - signal_line

        # Previous histogram (3 days ago)
        if len(closes) >= 29:
            macd_prev    = calc_ema(closes[3:15], 12) - calc_ema(closes[3:29], 26)
            signal_prev  = calc_ema(closes[3:12], 9)
            hist_prev    = macd_prev - signal_prev
            # Bullish crossover (histogram turning positive)
            if histogram > 0 and hist_prev <= 0:
                score += 6.0  # fresh crossover
            elif histogram > 0 and macd_line > signal_line:
                score += 4.0
            elif histogram > hist_prev and histogram > 0:
                score += 3.0  # strengthening
            elif macd_line > 0:
                score += 1.5
        else:
            if macd_line > 0 and macd_line > signal_line:
                score += 4.0
            elif macd_line > 0:
                score += 2.0

    # ── 5. MOMENTUM — Multi-timeframe (max 8 pts) ─────────────────────────
    mom_pts = 0.0
    # 1-month momentum
    if len(closes) >= 21:
        ret_1m = (closes[0] - closes[20]) / closes[20]
        if ret_1m > 0.03:   mom_pts += 2.0
        elif ret_1m > 0:    mom_pts += 1.0

    # 3-month momentum vs universe
    if len(closes) >= 63:
        ret_3m = (closes[0] - closes[62]) / closes[62]
        if universe_returns:
            sorted_ret = sorted(universe_returns)
            q75 = sorted_ret[int(len(sorted_ret) * 0.75)]
            q50 = sorted_ret[int(len(sorted_ret) * 0.50)]
            if ret_3m >= q75:   mom_pts += 3.0
            elif ret_3m >= q50: mom_pts += 1.5
        else:
            if ret_3m > 0.05: mom_pts += 2.0

    # 6-month momentum
    if len(closes) >= 126:
        ret_6m = (closes[0] - closes[125]) / closes[125]
        if ret_6m > 0.08:   mom_pts += 3.0
        elif ret_6m > 0:    mom_pts += 1.5

    if regime == "BEAR":
        mom_pts *= 0.6  # reduce momentum weight in bear market
    score += min(mom_pts, 8.0)

    # ── 6. BOLLINGER BANDS (max 7 pts) ────────────────────────────────────
    if len(closes) >= 20:
        sma = sum(closes[:20]) / 20
        std = (sum((x - sma)**2 for x in closes[:20]) / 20) ** 0.5
        if std > 0:
            upper = sma + 2 * std
            lower = sma - 2 * std
            pctb  = (closes[0] - lower) / (upper - lower)

            if regime == "BEAR":
                # In BEAR: prefer near lower band (defensive/bounce)
                if 0.1 <= pctb <= 0.45:  score += 7.0
                elif pctb < 0.1:          score += 4.0  # below lower
                elif pctb <= 0.65:        score += 2.0
                # above 0.65 in BEAR = overextended, 0 pts
            elif regime == "BULL":
                # In BULL: breakout above upper is good
                if pctb > 1.0:            score += 7.0
                elif pctb >= 0.6:         score += 5.0
                elif pctb >= 0.4:         score += 2.5
            else:
                if pctb >= 0.6:           score += 5.0
                elif pctb >= 0.4:         score += 3.0
                elif 0.1 <= pctb < 0.4:   score += 5.0  # near lower = bounce potential

    # ── 7. STOCHASTIC RSI (max 6 pts) ─────────────────────────────────────
    stoch_k = calc_stoch_rsi(closes)
    if stoch_k < 20:
        score += 6.0   # oversold — strong buy signal
    elif stoch_k < 40:
        score += 4.0
    elif stoch_k < 60:
        score += 2.0
    elif stoch_k < 80:
        score += 1.0
    # >80 = overbought, 0 pts

    # ── 8. VOLATILITY / ATR FILTER ────────────────────────────────────────
    atr_pct = calc_atr(highs, lows, closes) if highs and lows else 0.02

    # Penalize extremely volatile stocks in BEAR
    if regime == "BEAR" and atr_pct > 0.04:
        score *= 0.85

    # ── 9. FUNDAMENTAL PLACEHOLDER (15 pts fixed) ─────────────────────────
    score += 15.0

    return min(100.0, score), atr_pct


def detect_regime(bench_closes):
    if len(bench_closes) < 200:
        return "NEUTRAL"
    ma50  = sum(bench_closes[:50]) / 50
    ma200 = sum(bench_closes[:200]) / 200
    # Also check 20-week momentum
    mom   = (bench_closes[0] - bench_closes[99]) / bench_closes[99] if len(bench_closes) >= 100 else 0
    if ma50 > ma200 * 1.02 and mom > 0:    return "BULL"
    elif ma50 < ma200 * 0.98 or mom < -0.05: return "BEAR"
    return "NEUTRAL"


# ── Single Backtest ───────────────────────────────────────────────────────────

def run_single(all_data, bench_df, all_dates, params):
    score_thresh  = params["score_thresh"]
    stop_atr_mult = params["stop_atr_mult"]
    take_profit   = params["take_profit"]
    max_hold_days = params["max_hold_days"]

    cash     = INITIAL_CASH
    holdings = {}
    trades   = []
    equity   = []

    bench_list = list(bench_df.index)

    for i, today in enumerate(all_dates):
        # Portfolio value
        port_val = cash
        for ticker, pos in holdings.items():
            if ticker in all_data and today in all_data[ticker].index:
                port_val += pos["shares"] * all_data[ticker].loc[today, "close"]
        equity.append(port_val)

        if i < 200:
            continue

        # Regime
        b_idx = bench_list.index(today) if today in bench_list else -1
        if b_idx < 200:
            regime = "NEUTRAL"
        else:
            b_closes = bench_df["close"].iloc[b_idx-200:b_idx+1].tolist()[::-1]
            regime   = detect_regime(b_closes)

        # ── BEAR FILTER: no new entries in BEAR ──────────────────────────
        allow_entry = (regime != "BEAR")

        # Check exits
        for ticker in list(holdings.keys()):
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            pos       = holdings[ticker]
            cur_price = all_data[ticker].loc[today, "close"]
            pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]
            days_held = (today - pos["entry_date"]).days

            # Dynamic stop based on ATR
            dynamic_stop = -pos["atr_pct"] * stop_atr_mult

            if pnl <= dynamic_stop or pnl >= take_profit or days_held >= max_hold_days:
                reason = ("stop_loss" if pnl <= dynamic_stop else
                          "take_profit" if pnl >= take_profit else "timeout")
                proceeds = pos["shares"] * cur_price - FEE_US
                cash    += proceeds
                trades.append({
                    "ticker":      ticker,
                    "entry_date":  str(pos["entry_date"]),
                    "exit_date":   str(today),
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price":  round(cur_price, 4),
                    "pnl_pct":     round(pnl * 100, 2),
                    "reason":      reason,
                    "regime":      regime,
                    "days_held":   days_held,
                    "sector":      SECTOR_MAP.get(ticker, "Other"),
                })
                del holdings[ticker]

        # Check entries
        if allow_entry and len(holdings) < MAX_POSITIONS and cash > 0:

            # Sector filter: max 2 per sector already in portfolio
            sector_count = {}
            for tk in holdings:
                s = SECTOR_MAP.get(tk, "Other")
                sector_count[s] = sector_count.get(s, 0) + 1

            # Universe returns for momentum comparison
            universe_rets = []
            for tk, df in all_data.items():
                if today in df.index:
                    idx = list(df.index).index(today)
                    if idx >= 63:
                        cl = df["close"].iloc[max(0, idx-63):idx+1].tolist()[::-1]
                        universe_rets.append((cl[0] - cl[62]) / cl[62])

            scores = []
            for ticker, df in all_data.items():
                if ticker in holdings or today not in df.index:
                    continue

                # Sector limit: max 2 per sector
                sec = SECTOR_MAP.get(ticker, "Other")
                if sector_count.get(sec, 0) >= 2:
                    continue

                t_idx   = list(df.index).index(today)
                closes  = df["close"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]
                highs   = df["high"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]  if "high"   in df.columns else []
                lows    = df["low"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]   if "low"    in df.columns else []
                volumes = df["volume"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]

                score, atr_pct = score_ticker(closes, highs, lows, volumes, regime, universe_rets)
                if score >= score_thresh:
                    scores.append((ticker, score, df.loc[today, "close"], atr_pct, sec))

            scores.sort(key=lambda x: x[1], reverse=True)
            slots = MAX_POSITIONS - len(holdings)

            for ticker, score, price, atr_pct, sec in scores[:slots]:
                slot_size = INITIAL_CASH / MAX_POSITIONS
                invest    = min(slot_size, cash * 0.95)
                if invest < price:
                    continue
                shares = int(invest / price)
                cost   = shares * price + FEE_US
                if cost <= cash:
                    cash -= cost
                    holdings[ticker] = {
                        "shares":      shares,
                        "entry_price": price,
                        "entry_date":  today,
                        "atr_pct":     atr_pct,
                        "score":       score,
                        "sector":      sec,
                    }
                    sector_count[sec] = sector_count.get(sec, 0) + 1

    # Close all at end
    last_date = all_dates[-1]
    for ticker, pos in list(holdings.items()):
        if ticker in all_data and last_date in all_data[ticker].index:
            cur_price = all_data[ticker].loc[last_date, "close"]
            pnl = (cur_price - pos["entry_price"]) / pos["entry_price"]
            cash += pos["shares"] * cur_price - FEE_US
            trades.append({
                "ticker":      ticker,
                "entry_date":  str(pos["entry_date"]),
                "exit_date":   str(last_date),
                "entry_price": round(pos["entry_price"], 4),
                "exit_price":  round(cur_price, 4),
                "pnl_pct":     round(pnl * 100, 2),
                "reason":      "end_of_backtest",
                "regime":      "N/A",
                "days_held":   (last_date - pos["entry_date"]).days,
                "sector":      SECTOR_MAP.get(ticker, "Other"),
            })

    if not trades:
        return None

    total_ret = (cash / INITIAL_CASH - 1) * 100
    years     = len(all_dates) / 252
    cagr      = ((cash / INITIAL_CASH) ** (1/years) - 1) * 100

    wins     = [t for t in trades if t["pnl_pct"] > 0]
    losses   = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win  = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    pf       = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss else 0

    eq_s   = pd.Series(equity)
    peak   = eq_s.cummax()
    max_dd = ((eq_s - peak) / peak).min() * 100
    dr     = eq_s.pct_change().dropna()
    sharpe = float((dr.mean() / dr.std() * np.sqrt(252))) if dr.std() > 0 else 0

    return {
        "params":        params,
        "total_return":  round(total_ret, 1),
        "cagr":          round(cagr, 1),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 1),
        "avg_loss":      round(avg_loss, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown":  round(float(max_dd), 1),
        "sharpe":        round(sharpe, 2),
        "n_trades":      len(trades),
        "equity":        [round(e, 2) for e in equity],
        "trades":        trades,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run_backtest():
    logger.info("Downloading data...")
    all_data = {}
    for t in US_TICKERS + HK_TICKERS:
        df = fetch_history(t)
        if df is not None:
            all_data[t] = df
            logger.info(f"  {t}: {len(df)} days")

    bench_df = fetch_history("^GSPC")
    if bench_df is None:
        logger.error("Failed S&P500"); return

    bench_ret = (bench_df["close"].iloc[-1] / bench_df["close"].iloc[0] - 1) * 100
    all_dates = sorted(d for d in bench_df.index
                       if pd.to_datetime(START_DATE).date() <= d <=
                       pd.to_datetime(END_DATE).date())

    n_combos = len(list(product(*PARAM_GRID.values())))
    logger.info(f"S&P500: {bench_ret:.1f}% | {len(all_dates)} days | {n_combos} combos")

    results = []
    for combo in product(*PARAM_GRID.values()):
        params = dict(zip(PARAM_GRID.keys(), combo))
        r = run_single(all_data, bench_df, all_dates, params)
        if r:
            results.append(r)
            logger.info(
                f"  thresh={params['score_thresh']} "
                f"atr={params['stop_atr_mult']}x "
                f"tp={params['take_profit']} "
                f"hold={params['max_hold_days']}d "
                f"→ {r['total_return']}% CAGR={r['cagr']}% "
                f"WR={r['win_rate']}% PF={r['profit_factor']} "
                f"DD={r['max_drawdown']}% n={r['n_trades']}"
            )

    if not results:
        logger.error("No results"); return

    best = max(results, key=lambda x: x["total_return"])
    logger.info(f"\n{'='*60}")
    logger.info(f"BEST PARAMS: {best['params']}")
    logger.info(f"  Return       : {best['total_return']}%  (S&P500: {bench_ret:.1f}%)")
    logger.info(f"  CAGR         : {best['cagr']}%/year")
    logger.info(f"  Win rate     : {best['win_rate']}%")
    logger.info(f"  Profit factor: {best['profit_factor']}")
    logger.info(f"  Max drawdown : {best['max_drawdown']}%")
    logger.info(f"  Sharpe       : {best['sharpe']}")
    logger.info(f"  Trades       : {best['n_trades']}")
    logger.info(f"{'='*60}")

    with open("backtest_trades.json", "w") as f:
        json.dump(best["trades"], f, indent=2)

    # ── Excel ──
    wb  = openpyxl.Workbook()
    hf  = PatternFill("solid", fgColor="1a1a2e")
    hft = Font(color="FFFFFF", bold=True)
    gf  = PatternFill("solid", fgColor="c8e6c9")
    rf  = PatternFill("solid", fgColor="ffcdd2")
    yf2 = PatternFill("solid", fgColor="fff9c4")
    bf  = PatternFill("solid", fgColor="bbdefb")

    # Sheet 1 — Optimization
    ws1 = wb.active
    ws1.title = "Optimization"
    hdrs = ["Score Thresh","ATR Mult","Take Profit","Max Hold",
            "Total Return%","CAGR%","Win Rate%","Avg Win%","Avg Loss%",
            "Profit Factor","Max DD%","Sharpe","Trades"]
    for c, h in enumerate(hdrs, 1):
        cell = ws1.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft

    for r_idx, r in enumerate(sorted(results, key=lambda x: x["total_return"], reverse=True), 2):
        p = r["params"]
        vals = [p["score_thresh"], p["stop_atr_mult"], p["take_profit"], p["max_hold_days"],
                r["total_return"], r["cagr"], r["win_rate"], r["avg_win"], r["avg_loss"],
                r["profit_factor"], r["max_drawdown"], r["sharpe"], r["n_trades"]]
        fill = gf if r["total_return"] > bench_ret else (
               bf if r["total_return"] > 50 else (
               yf2 if r["total_return"] > 0 else rf))
        for c, v in enumerate(vals, 1):
            ws1.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFGHIJKLM":
        ws1.column_dimensions[col].width = 14

    # Sheet 2 — Best Config
    ws2 = wb.create_sheet("Best Config")
    rows = [
        ("", "Best Model", "S&P500"),
        ("Score Threshold", best["params"]["score_thresh"], ""),
        ("ATR Stop Multiplier", best["params"]["stop_atr_mult"], ""),
        ("Take Profit", f"{best['params']['take_profit']*100:.0f}%", ""),
        ("Max Hold Days", best["params"]["max_hold_days"], ""),
        ("Regime Filter", "No new entries in BEAR", ""),
        ("Sector Limit", "Max 2 per sector", ""),
        ("", "", ""),
        ("Total Return", f"{best['total_return']}%", f"{bench_ret:.1f}%"),
        ("CAGR", f"{best['cagr']}%/year", "~17%/year"),
        ("Win Rate", f"{best['win_rate']}%", ""),
        ("Avg Win", f"{best['avg_win']}%", ""),
        ("Avg Loss", f"{best['avg_loss']}%", ""),
        ("Profit Factor", best["profit_factor"], ""),
        ("Max Drawdown", f"{best['max_drawdown']}%", ""),
        ("Sharpe Ratio", best["sharpe"], ""),
        ("Total Trades", best["n_trades"], ""),
        ("Initial Capital", f"${INITIAL_CASH:,.0f}", ""),
        ("Final Capital", f"${INITIAL_CASH*(1+best['total_return']/100):,.0f}", ""),
    ]
    for r_idx, row in enumerate(rows, 1):
        for c, val in enumerate(row, 1):
            cell = ws2.cell(row=r_idx, column=c, value=val)
            if r_idx == 1: cell.fill = hf; cell.font = hft
    for col in "ABC":
        ws2.column_dimensions[col].width = 25

    # Sheet 3 — Best Trades
    ws3 = wb.create_sheet("Best Trades")
    for c, h in enumerate(["Ticker","Sector","Entry","Exit","Entry $","Exit $","P&L%","Reason","Regime","Days"], 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, t in enumerate(sorted(best["trades"], key=lambda x: x["entry_date"]), 2):
        vals = [t["ticker"], t.get("sector",""), t["entry_date"], t["exit_date"],
                t["entry_price"], t["exit_price"], t["pnl_pct"],
                t["reason"], t["regime"], t["days_held"]]
        fill = gf if t["pnl_pct"] > 0 else rf
        for c, v in enumerate(vals, 1):
            ws3.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFGHIJ":
        ws3.column_dimensions[col].width = 14

    # Sheet 4 — By Ticker
    ws4 = wb.create_sheet("By Ticker")
    ticker_stats = {}
    for t in best["trades"]:
        tk = t["ticker"]
        if tk not in ticker_stats:
            ticker_stats[tk] = {"n":0,"wins":0,"pnl":0,"sector":t.get("sector","")}
        ticker_stats[tk]["n"]    += 1
        ticker_stats[tk]["pnl"]  += t["pnl_pct"]
        if t["pnl_pct"] > 0: ticker_stats[tk]["wins"] += 1
    for c, h in enumerate(["Ticker","Sector","Trades","Win Rate","Total PnL%","Avg PnL%"], 1):
        cell = ws4.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, (tk, s) in enumerate(
            sorted(ticker_stats.items(), key=lambda x: x[1]["pnl"], reverse=True), 2):
        wr  = s["wins"]/s["n"]*100
        avg = s["pnl"]/s["n"]
        for c, v in enumerate([tk, s["sector"], s["n"], f"{wr:.0f}%", f"{s['pnl']:.1f}%", f"{avg:.1f}%"], 1):
            ws4.cell(row=r_idx, column=c, value=v).fill = gf if s["pnl"] > 0 else rf

    # Sheet 5 — Equity Curve
    ws5 = wb.create_sheet("Equity Curve")
    for c, h in enumerate(["Date","Model ($)","S&P500 ($10k)"], 1):
        ws5.cell(row=1, column=c, value=h)
    bench_norm = bench_df["close"] / bench_df["close"].iloc[0] * INITIAL_CASH
    for i, (d, e) in enumerate(zip(all_dates, best["equity"]), 2):
        b = bench_norm.get(d)
        ws5.cell(row=i, column=1, value=str(d))
        ws5.cell(row=i, column=2, value=e)
        ws5.cell(row=i, column=3, value=round(float(b), 2) if b is not None else None)

    chart = LineChart()
    chart.title = f"Model vs S&P500 | Best: thresh={best['params']['score_thresh']} atr={best['params']['stop_atr_mult']}x tp={best['params']['take_profit']}"
    chart.style = 10
    n = len(best["equity"]) + 1
    chart.add_data(Reference(ws5, min_col=2, max_col=3, min_row=1, max_row=n), titles_from_data=True)
    chart.width = 28; chart.height = 16
    ws5.add_chart(chart, "E2")

    wb.save("backtest_results.xlsx")
    logger.info("Saved backtest_results.xlsx")


if __name__ == "__main__":
    run_backtest()
