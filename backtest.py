"""
backtest.py — Optimize and simulate portfolio manager signals on 5 years of historical data.
Tests multiple parameter combinations to find the best configuration.
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

# ── Fixed Parameters ──────────────────────────────────────────────────────────
START_DATE   = "2020-01-01"
END_DATE     = "2025-12-31"
MAX_POSITIONS = 6
FEE_US       = 2.00

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

# ── Parameter Grid to Test ────────────────────────────────────────────────────
PARAM_GRID = {
    "score_thresh": [35, 40, 45],
    "stop_loss":    [-0.06, -0.08, -0.10],
    "take_profit":  [0.12, 0.18, 0.25],
    "max_hold_days":[45, 60, 90],
}


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_history(ticker: str) -> pd.DataFrame | None:
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
        ts      = result[0]["timestamp"]
        quote   = result[0]["indicators"]["quote"][0]
        dates   = [datetime.utcfromtimestamp(t).date() for t in ts]
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


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_ticker_on_day(closes: list, volumes: list, regime: str) -> float:
    if len(closes) < 50:
        return 0.0

    # Trend
    ma20 = sum(closes[:20]) / 20
    ma50 = sum(closes[:50]) / 50
    trend = 0.0
    if len(closes) >= 200:
        ma200 = sum(closes[:200]) / 200
        if closes[0] > ma20: trend += 7/3
        if ma20 > ma50:      trend += 7/3
        if ma50 > ma200:     trend += 7/3
    elif len(closes) >= 100:
        ma100 = sum(closes[:100]) / 100
        if closes[0] > ma20: trend += 5.6/3
        if ma20 > ma50:      trend += 5.6/3
        if ma50 > ma100:     trend += 5.6/3
    if regime == "BEAR":
        trend *= 0.7

    # RSI
    deltas = [closes[i] - closes[i+1] for i in range(14)]
    gains  = sum(d for d in deltas if d > 0) / 14
    losses = sum(-d for d in deltas if d < 0) / 14
    rs     = gains / losses if losses != 0 else 100
    rsi    = 100 - 100 / (1 + rs)
    if 40 <= rsi <= 60:   rsi_s = 6.0
    elif rsi < 30:        rsi_s = 4.8
    elif rsi > 70:        rsi_s = 0.0
    else:                 rsi_s = 2.4

    # Volume
    avg_v = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else 1
    ratio = volumes[0] / avg_v if avg_v > 0 else 0
    if ratio >= 1.5:   vol_s = 8.0
    elif ratio >= 1.2: vol_s = 4.8
    elif ratio >= 0.7: vol_s = 2.4
    else:              vol_s = 0.0

    # MACD
    def ema(data, span):
        k = 2/(span+1); e = data[-1]
        for p in reversed(data[:-1]): e = p*k + e*(1-k)
        return e
    macd_s = 3.0 if ema(closes[:12], 12) - ema(closes[:26], 26) > 0 else 0.0

    # Momentum
    mom = (closes[0] - closes[62]) / closes[62] if len(closes) > 62 else 0
    if regime == "BEAR": mom_s = 0.0 if mom < 0 else 2.1
    else:                mom_s = 4.2 if mom > 0.05 else 2.1
    if regime == "BEAR": mom_s *= 0.7

    # Bollinger
    sma  = sum(closes[:20]) / 20
    std  = (sum((x-sma)**2 for x in closes[:20]) / 20) ** 0.5
    pctb = (closes[0] - (sma - 2*std)) / (4*std) if std > 0 else 0.5
    if pctb >= 0.6:   boll_s = 5.6
    elif pctb >= 0.4: boll_s = 2.4
    elif pctb >= 0.1: boll_s = 4.8 if regime == "BEAR" else 3.2
    else:             boll_s = 0.0

    # StochRSI simplified
    stoch_s = 3.6 if 20 <= rsi <= 60 else (0.0 if rsi > 80 else 2.4)

    return min(100.0, trend + rsi_s + vol_s + macd_s + mom_s + boll_s + stoch_s + 15.0)


def detect_regime(bench_closes: list) -> str:
    if len(bench_closes) < 200:
        return "NEUTRAL"
    ma50  = sum(bench_closes[:50]) / 50
    ma200 = sum(bench_closes[:200]) / 200
    if ma50 > ma200 * 1.02:   return "BULL"
    elif ma50 < ma200 * 0.98: return "BEAR"
    return "NEUTRAL"


# ── Single Backtest Run ───────────────────────────────────────────────────────

def run_single(all_data, bench_df, all_dates, params):
    score_thresh  = params["score_thresh"]
    stop_loss     = params["stop_loss"]
    take_profit   = params["take_profit"]
    max_hold_days = params["max_hold_days"]

    portfolio = {}
    trades    = []
    equity    = [1.0]

    bench_idx_map = {d: i for i, d in enumerate(bench_df.index)}

    for i, today in enumerate(all_dates):
        if i < 200:
            equity.append(equity[-1])
            continue

        # Regime
        b_idx  = bench_idx_map.get(today, -1)
        if b_idx < 200:
            regime = "NEUTRAL"
        else:
            b_closes = bench_df["close"].iloc[b_idx-200:b_idx+1].tolist()[::-1]
            regime   = detect_regime(b_closes)

        # Exits
        for ticker in list(portfolio.keys()):
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            pos       = portfolio[ticker]
            cur_price = all_data[ticker].loc[today, "close"]
            pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]
            days_held = (today - pos["entry_date"]).days

            if pnl <= stop_loss or pnl >= take_profit or days_held >= max_hold_days:
                reason  = ("stop_loss" if pnl <= stop_loss else
                           "take_profit" if pnl >= take_profit else "timeout")
                fee_pct = FEE_US / (cur_price * 10)
                net_pnl = pnl - fee_pct * 2
                trades.append({
                    "ticker":     ticker,
                    "entry_date": str(pos["entry_date"]),
                    "exit_date":  str(today),
                    "pnl_pct":    round(net_pnl * 100, 2),
                    "reason":     reason,
                    "regime":     regime,
                    "days_held":  days_held,
                })
                del portfolio[ticker]

        # Entries
        if i % 1 == 0 and len(portfolio) < MAX_POSITIONS:
            scores = []
            for ticker, df in all_data.items():
                if ticker in portfolio or today not in df.index:
                    continue
                t_idx   = list(df.index).index(today)
                closes  = df["close"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]
                volumes = df["volume"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]
                score   = score_ticker_on_day(closes, volumes, regime)
                if score >= score_thresh:
                    scores.append((ticker, score, df.loc[today, "close"]))

            scores.sort(key=lambda x: x[1], reverse=True)
            for ticker, score, price in scores[:MAX_POSITIONS - len(portfolio)]:
                portfolio[ticker] = {
                    "entry_price": price * (1 + FEE_US / (price * 10)),
                    "entry_date":  today,
                }

        # Equity update
        pos_val = 0.0
        n = len(portfolio)
        if n > 0:
            for ticker, pos in portfolio.items():
                if ticker in all_data and today in all_data[ticker].index:
                    cur = all_data[ticker].loc[today, "close"]
                    pos_val += (cur / pos["entry_price"] - 1) / MAX_POSITIONS
        equity.append(equity[-1] * (1 + pos_val) if n > 0 else equity[-1])

    if not trades:
        return None

    wins        = [t for t in trades if t["pnl_pct"] > 0]
    losses      = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate    = len(wins) / len(trades) * 100
    avg_win     = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss    = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    pf          = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss else 0
    total_ret   = (equity[-1] - 1) * 100
    years       = len(all_dates) / 252
    cagr        = ((equity[-1]) ** (1/years) - 1) * 100

    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak: peak = e
        dd = (e - peak) / peak
        if dd < max_dd: max_dd = dd

    eq_s   = pd.Series(equity)
    dr     = eq_s.pct_change().dropna()
    sharpe = (dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else 0

    return {
        "params":        params,
        "total_return":  round(total_ret, 1),
        "cagr":          round(cagr, 1),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 1),
        "avg_loss":      round(avg_loss, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown":  round(max_dd * 100, 1),
        "sharpe":        round(sharpe, 2),
        "n_trades":      len(trades),
        "equity":        equity,
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
        logger.error("Failed S&P500")
        return

    bench_start = bench_df["close"].iloc[0]
    bench_end   = bench_df["close"].iloc[-1]
    bench_ret   = (bench_end / bench_start - 1) * 100

    all_dates = sorted(bench_df.index)
    all_dates = [d for d in all_dates
                 if pd.to_datetime(START_DATE).date() <= d <=
                 pd.to_datetime(END_DATE).date()]

    logger.info(f"S&P500 return: {bench_ret:.1f}%")
    logger.info(f"Running parameter grid ({len(list(product(*PARAM_GRID.values())))} combinations)...")

    results = []
    keys    = list(PARAM_GRID.keys())
    values  = list(PARAM_GRID.values())

    for combo in product(*values):
        params = dict(zip(keys, combo))
        r = run_single(all_data, bench_df, all_dates, params)
        if r:
            results.append(r)
            logger.info(
                f"  thresh={params['score_thresh']} sl={params['stop_loss']} "
                f"tp={params['take_profit']} hold={params['max_hold_days']}d "
                f"→ {r['total_return']}% | WR={r['win_rate']}% | "
                f"PF={r['profit_factor']} | trades={r['n_trades']}"
            )

    if not results:
        logger.error("No results")
        return

    # Best by total return
    best = max(results, key=lambda x: x["total_return"])
    logger.info(f"\n{'='*60}")
    logger.info(f"BEST: {best['params']}")
    logger.info(f"  Total return : {best['total_return']}% (vs S&P500: {bench_ret:.1f}%)")
    logger.info(f"  CAGR         : {best['cagr']}%/year")
    logger.info(f"  Win rate     : {best['win_rate']}%")
    logger.info(f"  Profit factor: {best['profit_factor']}")
    logger.info(f"  Max drawdown : {best['max_drawdown']}%")
    logger.info(f"  Sharpe       : {best['sharpe']}")
    logger.info(f"  Trades       : {best['n_trades']}")
    logger.info(f"{'='*60}")

    # Save best trades
    with open("backtest_trades.json", "w") as f:
        json.dump(best["trades"], f, indent=2)

    # ── Excel ──
    wb  = openpyxl.Workbook()
    hf  = PatternFill("solid", fgColor="1a1a2e")
    hft = Font(color="FFFFFF", bold=True)
    gf  = PatternFill("solid", fgColor="c8e6c9")
    rf  = PatternFill("solid", fgColor="ffcdd2")
    yf  = PatternFill("solid", fgColor="fff9c4")

    # Sheet 1 — Optimization Results
    ws1 = wb.active
    ws1.title = "Optimization"
    hdrs = ["Score Thresh", "Stop Loss", "Take Profit", "Max Hold",
            "Total Return%", "CAGR%", "Win Rate%", "Avg Win%", "Avg Loss%",
            "Profit Factor", "Max DD%", "Sharpe", "Trades"]
    for c, h in enumerate(hdrs, 1):
        cell = ws1.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft

    results_sorted = sorted(results, key=lambda x: x["total_return"], reverse=True)
    for r_idx, r in enumerate(results_sorted, 2):
        p = r["params"]
        vals = [p["score_thresh"], p["stop_loss"], p["take_profit"],
                p["max_hold_days"], r["total_return"], r["cagr"],
                r["win_rate"], r["avg_win"], r["avg_loss"],
                r["profit_factor"], r["max_drawdown"], r["sharpe"], r["n_trades"]]
        fill = gf if r["total_return"] > bench_ret else (
               yf if r["total_return"] > 0 else rf)
        for c, v in enumerate(vals, 1):
            cell = ws1.cell(row=r_idx, column=c, value=v)
            cell.fill = fill

    for col in ["A","B","C","D","E","F","G","H","I","J","K","L","M"]:
        ws1.column_dimensions[col].width = 14

    # Sheet 2 — Best Config Summary
    ws2 = wb.create_sheet("Best Config")
    summary = [
        ("", "Best Model", f"S&P500 ({START_DATE[:4]}-{END_DATE[:4]})"),
        ("Score Threshold", best["params"]["score_thresh"], ""),
        ("Stop Loss", f"{best['params']['stop_loss']*100:.0f}%", ""),
        ("Take Profit", f"{best['params']['take_profit']*100:.0f}%", ""),
        ("Max Hold Days", best["params"]["max_hold_days"], ""),
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
    ]
    for r_idx, row in enumerate(summary, 1):
        for c, val in enumerate(row, 1):
            cell = ws2.cell(row=r_idx, column=c, value=val)
            if r_idx == 1:
                cell.fill = hf; cell.font = hft
    ws2.column_dimensions["A"].width = 20
    ws2.column_dimensions["B"].width = 20
    ws2.column_dimensions["C"].width = 20

    # Sheet 3 — Best Trades
    ws3 = wb.create_sheet("Best Trades")
    t_hdrs = ["Ticker", "Entry", "Exit", "P&L%", "Reason", "Regime", "Days"]
    for c, h in enumerate(t_hdrs, 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, t in enumerate(sorted(best["trades"],
                                      key=lambda x: x["entry_date"]), 2):
        vals = [t["ticker"], t["entry_date"], t["exit_date"],
                t["pnl_pct"], t["reason"], t["regime"], t["days_held"]]
        fill = gf if t["pnl_pct"] > 0 else rf
        for c, v in enumerate(vals, 1):
            ws3.cell(row=r_idx, column=c, value=v).fill = fill
    for col in ["A","B","C","D","E","F","G"]:
        ws3.column_dimensions[col].width = 15

    # Sheet 4 — Equity Curve (best)
    ws4 = wb.create_sheet("Equity Curve")
    ws4.cell(row=1, column=1, value="Day")
    ws4.cell(row=1, column=2, value="Best Model")
    ws4.cell(row=1, column=3, value="S&P500")
    bench_norm = bench_df["close"] / bench_df["close"].iloc[0]
    bench_vals = [bench_norm.iloc[i] if i < len(bench_norm) else None
                  for i in range(len(best["equity"]))]
    for i, (e, b) in enumerate(zip(best["equity"], bench_vals), 2):
        ws4.cell(row=i, column=1, value=i-1)
        ws4.cell(row=i, column=2, value=round(e, 4))
        ws4.cell(row=i, column=3, value=round(float(b), 4) if b else None)

    chart = LineChart()
    chart.title = f"Best Model vs S&P500 (thresh={best['params']['score_thresh']})"
    chart.style = 10
    n_rows = len(best["equity"]) + 1
    data_ref = Reference(ws4, min_col=2, max_col=3, min_row=1, max_row=n_rows)
    chart.add_data(data_ref, titles_from_data=True)
    chart.width = 25; chart.height = 15
    ws4.add_chart(chart, "E2")

    wb.save("backtest_results.xlsx")
    logger.info("Saved backtest_results.xlsx")


if __name__ == "__main__":
    run_backtest()
