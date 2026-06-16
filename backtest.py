"""
backtest.py — Advanced backtest with:
- Dynamic position sizing
- ATR dynamic stops
- Bear/sector filters
- Annual performance breakdown
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
    "KO":"Staples","PEP":"Staples","MO":"Staples","WMT":"Staples",
    "PG":"Staples","KHC":"Staples","PM":"Staples",
    "BAC":"Financials","PYPL":"Financials","COIN":"Financials",
    "SOFI":"Financials","SQ":"Financials",
    "ABT":"Healthcare","PFE":"Healthcare","MRK":"Healthcare",
    "DHR":"Healthcare","CVS":"Healthcare",
    "NVDA":"Tech","AMD":"Tech","CSCO":"Tech","PLTR":"Tech",
    "0700.HK":"Tech","9999.HK":"Tech",
    "TSLA":"Auto","GM":"Auto","F":"Auto",
    "NKE":"Consumer","DIS":"Consumer","SBUX":"Consumer","DKNG":"Consumer",
    "XOM":"Energy","0883.HK":"Energy",
    "RTX":"Defense","UPS":"Logistics",
    "NEE":"Utilities","T":"Telecom","VZ":"Telecom",
    "0941.HK":"Telecom","1299.HK":"Financials","0005.HK":"Financials",
    "0388.HK":"Financials","2318.HK":"Financials","1398.HK":"Financials",
    "0939.HK":"Financials",
}

PARAM_GRID = {
    "score_thresh":  [36],
    "stop_atr_mult": [2.0],
    "take_profit":   [0.22],
    "max_hold_days": [50],
}


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


def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return 0.02
    tr_list = [max(highs[i]-lows[i],
                   abs(highs[i]-closes[i+1]),
                   abs(lows[i]-closes[i+1])) for i in range(period)]
    return sum(tr_list) / period / closes[0] if closes[0] > 0 else 0.02


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i+1] for i in range(period)]
    gains  = sum(d for d in deltas if d > 0) / period
    losses = sum(-d for d in deltas if d < 0) / period
    rs     = gains / losses if losses != 0 else 100
    return 100 - 100 / (1 + rs)


def calc_ema(data, span):
    k = 2 / (span + 1); e = data[-1]
    for p in reversed(data[:-1]): e = p * k + e * (1 - k)
    return e


def calc_stoch_rsi(closes, period=14):
    if len(closes) < period * 2:
        return 50.0
    rsi_vals = [calc_rsi(closes[i:i+period+1]) for i in range(period)]
    rsi_min, rsi_max = min(rsi_vals), max(rsi_vals)
    if rsi_max == rsi_min:
        return 50.0
    return (rsi_vals[0] - rsi_min) / (rsi_max - rsi_min) * 100


def score_ticker(closes, highs, lows, volumes, regime, universe_returns=None):
    if len(closes) < 50:
        return 0.0, 0.02

    score = 0.0

    # Trend (max 12)
    ma20 = sum(closes[:20]) / 20
    ma50 = sum(closes[:50]) / 50
    tp = 0.0
    if len(closes) >= 200:
        ma100 = sum(closes[:100]) / 100
        ma200 = sum(closes[:200]) / 200
        if closes[0] > ma20:  tp += 3.0
        if ma20 > ma50:       tp += 3.0
        if ma50 > ma100:      tp += 3.0
        if ma100 > ma200:     tp += 3.0
        ma50_prev = sum(closes[5:55]) / 50 if len(closes) >= 55 else ma50
        if ma50 > ma50_prev:  tp += 1.0
        if closes[0] > ma200 * 1.02: tp += 1.0
    elif len(closes) >= 100:
        ma100 = sum(closes[:100]) / 100
        if closes[0] > ma20: tp += 2.5
        if ma20 > ma50:      tp += 2.5
        if ma50 > ma100:     tp += 3.0
        tp *= 0.85
    else:
        if closes[0] > ma20: tp += 2.0
        if ma20 > ma50:      tp += 2.0
        tp *= 0.6
    if regime == "BEAR": tp *= 0.7
    score += min(tp, 12.0)

    # RSI (max 8)
    rsi = calc_rsi(closes)
    ideal_min = 45 if regime == "BULL" else (35 if regime == "BEAR" else 40)
    ideal_max = 70 if regime == "BULL" else (55 if regime == "BEAR" else 62)
    if ideal_min <= rsi <= ideal_max: score += 8.0
    elif rsi < 25:                    score += 6.0
    elif rsi < ideal_min:             score += 4.0
    elif rsi > 75:                    score += 0.0
    else:                             score += 3.0

    # Volume (max 8)
    if len(volumes) >= 21:
        avg_vol   = sum(volumes[1:21]) / 20
        avg5      = sum(volumes[:5]) / 5
        ratio     = volumes[0] / avg_vol if avg_vol > 0 else 0
        vol_trend = avg5 / avg_vol if avg_vol > 0 else 1.0
        if ratio >= 1.5 and vol_trend > 1.1: score += 8.0
        elif ratio >= 1.5:                   score += 6.0
        elif ratio >= 1.2:                   score += 4.8
        elif ratio >= 0.8:                   score += 2.4

    # MACD (max 6)
    if len(closes) >= 29:
        macd   = calc_ema(closes[:12], 12) - calc_ema(closes[:26], 26)
        sig    = calc_ema(closes[:9], 9)
        hist   = macd - sig
        macd_p = calc_ema(closes[3:15], 12) - calc_ema(closes[3:29], 26)
        sig_p  = calc_ema(closes[3:12], 9)
        hist_p = macd_p - sig_p
        if hist > 0 and hist_p <= 0:     score += 6.0
        elif hist > 0 and macd > sig:    score += 4.0
        elif hist > hist_p and hist > 0: score += 3.0
        elif macd > 0:                   score += 1.5

    # Momentum multi-tf (max 8)
    mp = 0.0
    if len(closes) >= 21:
        r1m = (closes[0] - closes[20]) / closes[20]
        mp += 2.0 if r1m > 0.03 else (1.0 if r1m > 0 else 0.0)
    if len(closes) >= 63:
        r3m = (closes[0] - closes[62]) / closes[62]
        if universe_returns:
            sr  = sorted(universe_returns)
            q75 = sr[int(len(sr)*0.75)]
            q50 = sr[int(len(sr)*0.50)]
            mp += 3.0 if r3m >= q75 else (1.5 if r3m >= q50 else 0.0)
        else:
            mp += 2.0 if r3m > 0.05 else 0.0
    if len(closes) >= 126:
        r6m = (closes[0] - closes[125]) / closes[125]
        mp += 3.0 if r6m > 0.08 else (1.5 if r6m > 0 else 0.0)
    if regime == "BEAR": mp *= 0.6
    score += min(mp, 8.0)

    # Bollinger (max 7)
    sma = sum(closes[:20]) / 20
    std = (sum((x-sma)**2 for x in closes[:20]) / 20) ** 0.5
    if std > 0:
        pctb = (closes[0] - (sma - 2*std)) / (4*std)
        if regime == "BEAR":
            if 0.1 <= pctb <= 0.45:  score += 7.0
            elif pctb < 0.1:          score += 4.0
            elif pctb <= 0.65:        score += 2.0
        elif regime == "BULL":
            if pctb > 1.0:            score += 7.0
            elif pctb >= 0.6:         score += 5.0
            elif pctb >= 0.4:         score += 2.5
        else:
            if pctb >= 0.6:           score += 5.0
            elif pctb >= 0.4:         score += 3.0
            elif 0.1 <= pctb < 0.4:   score += 5.0

    # StochRSI (max 6)
    stoch_k = calc_stoch_rsi(closes)
    if stoch_k < 20:   score += 6.0
    elif stoch_k < 40: score += 4.0
    elif stoch_k < 60: score += 2.0
    elif stoch_k < 80: score += 1.0

    atr_pct = calc_atr(highs, lows, closes) if highs and lows else 0.02
    if regime == "BEAR" and atr_pct > 0.04:
        score *= 0.85

    score += 15.0
    return min(100.0, score), atr_pct


def detect_regime(bench_closes):
    if len(bench_closes) < 200:
        return "NEUTRAL"
    ma50  = sum(bench_closes[:50]) / 50
    ma200 = sum(bench_closes[:200]) / 200
    mom   = (bench_closes[0] - bench_closes[99]) / bench_closes[99] if len(bench_closes) >= 100 else 0
    if ma50 > ma200 * 1.02 and mom > 0:       return "BULL"
    elif ma50 < ma200 * 0.98 or mom < -0.05:  return "BEAR"
    return "NEUTRAL"


def position_size_factor(equity_curve, peak_equity):
    if not equity_curve or peak_equity <= 0:
        return 1.0
    dd = (equity_curve[-1] - peak_equity) / peak_equity
    if dd >= -0.08:   return 1.0
    elif dd >= -0.12: return 0.75
    elif dd >= -0.15: return 0.50
    else:             return 0.25


def calc_annual_perf(trades, all_dates, equity):
    years = {}
    for i, d in enumerate(all_dates):
        yr = d.year
        if yr not in years:
            years[yr] = {"start_eq": equity[i], "end_eq": equity[i], "trades": 0, "wins": 0}
        years[yr]["end_eq"] = equity[i]
    for t in trades:
        yr = int(t["entry_date"][:4])
        if yr in years:
            years[yr]["trades"] += 1
            if t["pnl_pct"] > 0:
                years[yr]["wins"] += 1
    results = []
    for yr in sorted(years.keys()):
        y   = years[yr]
        ret = (y["end_eq"] / y["start_eq"] - 1) * 100 if y["start_eq"] > 0 else 0
        wr  = y["wins"] / y["trades"] * 100 if y["trades"] > 0 else 0
        results.append({
            "year":     yr,
            "return":   round(ret, 1),
            "trades":   y["trades"],
            "win_rate": round(wr, 1),
        })
    return results


def run_single(all_data, bench_df, all_dates, params):
    score_thresh  = params["score_thresh"]
    stop_atr_mult = params["stop_atr_mult"]
    take_profit   = params["take_profit"]
    max_hold_days = params["max_hold_days"]

    cash     = INITIAL_CASH
    holdings = {}
    trades   = []
    equity   = []
    peak_eq  = INITIAL_CASH

    bench_list = list(bench_df.index)

    for i, today in enumerate(all_dates):
        port_val = cash
        for ticker, pos in holdings.items():
            if ticker in all_data and today in all_data[ticker].index:
                port_val += pos["shares"] * all_data[ticker].loc[today, "close"]
        equity.append(port_val)
        if port_val > peak_eq:
            peak_eq = port_val

        if i < 200:
            continue

        b_idx = bench_list.index(today) if today in bench_list else -1
        if b_idx < 200:
            regime = "NEUTRAL"
        else:
            b_closes = bench_df["close"].iloc[b_idx-200:b_idx+1].tolist()[::-1]
            regime   = detect_regime(b_closes)

        allow_entry = (regime != "BEAR")
        ps_factor   = position_size_factor(equity, peak_eq)

        # Check exits
        for ticker in list(holdings.keys()):
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            pos        = holdings[ticker]
            cur_price  = all_data[ticker].loc[today, "close"]
            pnl        = (cur_price - pos["entry_price"]) / pos["entry_price"]
            days_held  = (today - pos["entry_date"]).days
            fixed_stop = -pos["atr_pct"] * stop_atr_mult

            sell = False; reason = ""
            if pnl <= fixed_stop:
                sell = True; reason = "stop_loss"
            elif pnl >= take_profit:
                sell = True; reason = "take_profit"
            elif days_held >= max_hold_days:
                sell = True; reason = "timeout"

            if sell:
                cash += pos["shares"] * cur_price - FEE_US
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
                    "sector":      pos.get("sector", ""),
                })
                del holdings[ticker]

        # Check entries
        if allow_entry and len(holdings) < MAX_POSITIONS and cash > 0:
            sector_count = {}
            for tk in holdings:
                s = SECTOR_MAP.get(tk, "Other")
                sector_count[s] = sector_count.get(s, 0) + 1

            recent_losses = sum(1 for t in trades[-10:]
                                if t["pnl_pct"] < 0 and
                                (today - datetime.strptime(t["exit_date"], "%Y-%m-%d").date()).days <= 5)
            if recent_losses >= 3:
                continue

            universe_rets = []
            for tk, df in all_data.items():
                if today in df.index:
                    idx = list(df.index).index(today)
                    if idx >= 63:
                        cl = df["close"].iloc[max(0,idx-63):idx+1].tolist()[::-1]
                        universe_rets.append((cl[0]-cl[62])/cl[62])

            scores = []
            for ticker, df in all_data.items():
                if ticker in holdings or today not in df.index:
                    continue
                sec = SECTOR_MAP.get(ticker, "Other")
                if sector_count.get(sec, 0) >= 2:
                    continue
                t_idx   = list(df.index).index(today)
                closes  = df["close"].iloc[max(0,t_idx-250):t_idx+1].tolist()[::-1]
                highs   = df["high"].iloc[max(0,t_idx-250):t_idx+1].tolist()[::-1] if "high" in df.columns else []
                lows    = df["low"].iloc[max(0,t_idx-250):t_idx+1].tolist()[::-1]  if "low"  in df.columns else []
                volumes = df["volume"].iloc[max(0,t_idx-250):t_idx+1].tolist()[::-1]
                score, atr_pct = score_ticker(closes, highs, lows, volumes, regime, universe_rets)
                if score >= score_thresh:
                    scores.append((ticker, score, df.loc[today, "close"], atr_pct, sec))

            scores.sort(key=lambda x: x[1], reverse=True)
            slots = MAX_POSITIONS - len(holdings)
            for ticker, score, price, atr_pct, sec in scores[:slots]:
                slot_size = (INITIAL_CASH / MAX_POSITIONS) * ps_factor
                invest    = min(slot_size, cash * 0.95)
                if invest < price:
                    continue
                shares = int(invest / price)
                cost   = shares * price + FEE_US
                if cost <= cash and shares > 0:
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
                "sector":      pos.get("sector", ""),
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
    pf       = abs(avg_win*len(wins) / (avg_loss*len(losses))) if losses and avg_loss else 0

    eq_s   = pd.Series(equity)
    peak   = eq_s.cummax()
    max_dd = float(((eq_s - peak) / peak).min() * 100)
    dr     = eq_s.pct_change().dropna()
    sharpe = float((dr.mean() / dr.std() * np.sqrt(252))) if dr.std() > 0 else 0

    annual = calc_annual_perf(trades, all_dates, equity)

    return {
        "params":        params,
        "total_return":  round(total_ret, 1),
        "cagr":          round(cagr, 1),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 1),
        "avg_loss":      round(avg_loss, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown":  round(max_dd, 1),
        "sharpe":        round(sharpe, 2),
        "n_trades":      len(trades),
        "equity":        [round(e, 2) for e in equity],
        "trades":        trades,
        "annual":        annual,
    }


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

    n = len(list(product(*PARAM_GRID.values())))
    logger.info(f"S&P500: {bench_ret:.1f}% | {len(all_dates)} days | {n} combos")

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

    best_sharpe = max(results, key=lambda x: x["sharpe"])
    best_return = max(results, key=lambda x: x["total_return"])
    best        = best_sharpe

    logger.info(f"\n{'='*60}")
    logger.info(f"BEST (Sharpe): {best_sharpe['params']}")
    logger.info(f"  Return={best_sharpe['total_return']}% CAGR={best_sharpe['cagr']}% "
                f"WR={best_sharpe['win_rate']}% PF={best_sharpe['profit_factor']} "
                f"DD={best_sharpe['max_drawdown']}% Sharpe={best_sharpe['sharpe']} "
                f"n={best_sharpe['n_trades']}")
    logger.info(f"BEST (Return): {best_return['params']}")
    logger.info(f"  Return={best_return['total_return']}% CAGR={best_return['cagr']}% "
                f"WR={best_return['win_rate']}% PF={best_return['profit_factor']} "
                f"DD={best_return['max_drawdown']}% Sharpe={best_return['sharpe']} "
                f"n={best_return['n_trades']}")
    logger.info(f"S&P500: {bench_ret:.1f}%")
    logger.info("Annual performance:")
    for y in best_sharpe.get("annual", []):
        logger.info(f"  {y['year']}: {y['return']:+.1f}% | {y['trades']} trades | WR={y['win_rate']:.0f}%")
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
    for r_idx, r in enumerate(sorted(results, key=lambda x: x["sharpe"], reverse=True), 2):
        p = r["params"]
        vals = [p["score_thresh"], p["stop_atr_mult"], p["take_profit"], p["max_hold_days"],
                r["total_return"], r["cagr"], r["win_rate"], r["avg_win"], r["avg_loss"],
                r["profit_factor"], r["max_drawdown"], r["sharpe"], r["n_trades"]]
        fill = gf if r["total_return"] > bench_ret else (
               bf if r["total_return"] > 60 else (
               yf2 if r["total_return"] > 0 else rf))
        for c, v in enumerate(vals, 1):
            ws1.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFGHIJKLM":
        ws1.column_dimensions[col].width = 14

    # Sheet 2 — Best Config
    ws2 = wb.create_sheet("Best Config")
    rows = [
        ("", "Best Sharpe", "Best Return", "S&P500"),
        ("Score Threshold",  best_sharpe["params"]["score_thresh"],  best_return["params"]["score_thresh"],  ""),
        ("ATR Stop Mult",    best_sharpe["params"]["stop_atr_mult"], best_return["params"]["stop_atr_mult"], ""),
        ("Take Profit",      f"{best_sharpe['params']['take_profit']*100:.0f}%", f"{best_return['params']['take_profit']*100:.0f}%", ""),
        ("Max Hold Days",    best_sharpe["params"]["max_hold_days"], best_return["params"]["max_hold_days"],  ""),
        ("Bear Filter",      "✅ No entry in BEAR", "✅ No entry in BEAR", ""),
        ("Sector Limit",     "Max 2 per sector", "Max 2 per sector", ""),
        ("Pos Size Dynamic", "✅ Active", "✅ Active", ""),
        ("", "", "", ""),
        ("Total Return",  f"{best_sharpe['total_return']}%",  f"{best_return['total_return']}%",  f"{bench_ret:.1f}%"),
        ("CAGR",          f"{best_sharpe['cagr']}%/year",     f"{best_return['cagr']}%/year",     "~17%/year"),
        ("Win Rate",      f"{best_sharpe['win_rate']}%",      f"{best_return['win_rate']}%",      ""),
        ("Avg Win",       f"{best_sharpe['avg_win']}%",       f"{best_return['avg_win']}%",       ""),
        ("Avg Loss",      f"{best_sharpe['avg_loss']}%",      f"{best_return['avg_loss']}%",      ""),
        ("Profit Factor", best_sharpe["profit_factor"],       best_return["profit_factor"],       ""),
        ("Max Drawdown",  f"{best_sharpe['max_drawdown']}%",  f"{best_return['max_drawdown']}%",  ""),
        ("Sharpe Ratio",  best_sharpe["sharpe"],              best_return["sharpe"],              ""),
        ("Total Trades",  best_sharpe["n_trades"],            best_return["n_trades"],            ""),
    ]
    for r_idx, row in enumerate(rows, 1):
        for c, val in enumerate(row, 1):
            cell = ws2.cell(row=r_idx, column=c, value=val)
            if r_idx == 1: cell.fill = hf; cell.font = hft
    for col in "ABCD":
        ws2.column_dimensions[col].width = 25

    # Sheet 3 — Best Trades
    ws3 = wb.create_sheet("Best Trades")
    for c, h in enumerate(["Ticker","Sector","Entry","Exit","Entry$","Exit$","P&L%","Reason","Regime","Days"], 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, t in enumerate(sorted(best["trades"], key=lambda x: x["entry_date"]), 2):
        vals = [t["ticker"], t.get("sector",""), t["entry_date"], t["exit_date"],
                t["entry_price"], t["exit_price"],
                t["pnl_pct"], t["reason"], t["regime"], t["days_held"]]
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
        ticker_stats[tk]["n"]   += 1
        ticker_stats[tk]["pnl"] += t["pnl_pct"]
        if t["pnl_pct"] > 0: ticker_stats[tk]["wins"] += 1
    for c, h in enumerate(["Ticker","Sector","Trades","Win Rate%","Total PnL%","Avg PnL%"], 1):
        cell = ws4.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, (tk, s) in enumerate(
            sorted(ticker_stats.items(), key=lambda x: x[1]["pnl"], reverse=True), 2):
        wr  = s["wins"]/s["n"]*100
        avg = s["pnl"]/s["n"]
        for c, v in enumerate([tk, s["sector"], s["n"], f"{wr:.0f}%",
                                f"{s['pnl']:.1f}%", f"{avg:.1f}%"], 1):
            ws4.cell(row=r_idx, column=c, value=v).fill = gf if s["pnl"] > 0 else rf
    for col in "ABCDEF":
        ws4.column_dimensions[col].width = 15

    # Sheet 5 — Equity Curve
    ws5 = wb.create_sheet("Equity Curve")
    for c, h in enumerate(["Date","Model ($)","S&P500 ($10k)"], 1):
        ws5.cell(row=1, column=c, value=h)
    bench_norm = bench_df["close"] / bench_df["close"].iloc[0] * INITIAL_CASH
    for i, (d, e_s) in enumerate(zip(all_dates, best_sharpe["equity"]), 2):
        b = bench_norm.get(d)
        ws5.cell(row=i, column=1, value=str(d))
        ws5.cell(row=i, column=2, value=e_s)
        ws5.cell(row=i, column=3, value=round(float(b), 2) if b is not None else None)
    chart = LineChart()
    chart.title = "Model vs S&P500"
    chart.style = 10
    n_rows = len(best_sharpe["equity"]) + 1
    chart.add_data(Reference(ws5, min_col=2, max_col=3, min_row=1, max_row=n_rows), titles_from_data=True)
    chart.width = 28; chart.height = 16
    ws5.add_chart(chart, "E2")

    # Sheet 6 — Annual Performance
    ws6 = wb.create_sheet("Annual Perf")
    for c, h in enumerate(["Year","Model Return%","S&P500 approx%","Trades","Win Rate%"], 1):
        cell = ws6.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    sp500_annual = {2020: 16.3, 2021: 26.9, 2022: -19.4, 2023: 24.2, 2024: 23.3, 2025: -2.0}
    for r_idx, y in enumerate(best_sharpe.get("annual", []), 2):
        sp = sp500_annual.get(y["year"], "")
        vals = [y["year"], y["return"], sp, y["trades"], y["win_rate"]]
        fill = gf if y["return"] > 0 else rf
        for c, v in enumerate(vals, 1):
            ws6.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDE":
        ws6.column_dimensions[col].width = 18

    wb.save("backtest_results.xlsx")
    logger.info("Saved backtest_results.xlsx")


if __name__ == "__main__":
    run_backtest()
