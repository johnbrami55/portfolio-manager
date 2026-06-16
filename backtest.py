"""
backtest.py — Optimize portfolio manager signals on 5 years of historical data.
Fixed equity curve calculation.
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
MAX_POSITIONS = 6
FEE_US        = 2.00
INITIAL_CASH  = 10000.0  # virtual USD

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

PARAM_GRID = {
    "score_thresh":  [35, 40, 45],
    "stop_loss":     [-0.06, -0.08, -0.10],
    "take_profit":   [0.12, 0.18, 0.25],
    "max_hold_days": [45, 60, 90],
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
            "volume": quote.get("volume", []),
        }).dropna().set_index("date")
        df = df[(df.index >= pd.to_datetime(START_DATE).date()) &
                (df.index <= pd.to_datetime(END_DATE).date())]
        return df if len(df) > 200 else None
    except Exception as e:
        logger.warning(f"{ticker}: {e}")
        return None


def score_ticker(closes, volumes, regime):
    if len(closes) < 50:
        return 0.0
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

    deltas = [closes[i] - closes[i+1] for i in range(14)]
    gains  = sum(d for d in deltas if d > 0) / 14
    losses = sum(-d for d in deltas if d < 0) / 14
    rs     = gains / losses if losses != 0 else 100
    rsi    = 100 - 100 / (1 + rs)
    if 40 <= rsi <= 60:   rsi_s = 6.0
    elif rsi < 30:        rsi_s = 4.8
    elif rsi > 70:        rsi_s = 0.0
    else:                 rsi_s = 2.4

    avg_v = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else 1
    ratio = volumes[0] / avg_v if avg_v > 0 else 0
    if ratio >= 1.5:   vol_s = 8.0
    elif ratio >= 1.2: vol_s = 4.8
    elif ratio >= 0.7: vol_s = 2.4
    else:              vol_s = 0.0

    def ema(data, span):
        k = 2/(span+1); e = data[-1]
        for p in reversed(data[:-1]): e = p*k + e*(1-k)
        return e
    macd_s = 3.0 if len(closes) >= 26 and ema(closes[:12], 12) - ema(closes[:26], 26) > 0 else 0.0

    mom = (closes[0] - closes[62]) / closes[62] if len(closes) > 62 else 0
    mom_s = (0.0 if mom < 0 else 2.1) if regime == "BEAR" else (4.2 if mom > 0.05 else 2.1)
    if regime == "BEAR": mom_s *= 0.7

    sma  = sum(closes[:20]) / 20
    std  = (sum((x-sma)**2 for x in closes[:20]) / 20) ** 0.5
    pctb = (closes[0] - (sma - 2*std)) / (4*std) if std > 0 else 0.5
    if pctb >= 0.6:   boll_s = 5.6
    elif pctb >= 0.4: boll_s = 2.4
    elif pctb >= 0.1: boll_s = 4.8 if regime == "BEAR" else 3.2
    else:             boll_s = 0.0

    stoch_s = 3.6 if 20 <= rsi <= 60 else (0.0 if rsi > 80 else 2.4)

    return min(100.0, trend + rsi_s + vol_s + macd_s + mom_s + boll_s + stoch_s + 15.0)


def detect_regime(bench_closes):
    if len(bench_closes) < 200:
        return "NEUTRAL"
    ma50  = sum(bench_closes[:50]) / 50
    ma200 = sum(bench_closes[:200]) / 200
    if ma50 > ma200 * 1.02:    return "BULL"
    elif ma50 < ma200 * 0.98:  return "BEAR"
    return "NEUTRAL"


def run_single(all_data, bench_df, all_dates, params):
    score_thresh  = params["score_thresh"]
    stop_loss     = params["stop_loss"]
    take_profit   = params["take_profit"]
    max_hold_days = params["max_hold_days"]

    # Portfolio tracked in actual shares + cash
    cash      = INITIAL_CASH
    holdings  = {}  # {ticker: {"shares": n, "entry_price": p, "entry_date": d}}
    trades    = []
    equity    = []  # portfolio value each day

    bench_list = list(bench_df.index)

    for i, today in enumerate(all_dates):
        # Current portfolio value
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

        # Check exits
        for ticker in list(holdings.keys()):
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            pos       = holdings[ticker]
            cur_price = all_data[ticker].loc[today, "close"]
            pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]
            days_held = (today - pos["entry_date"]).days

            if pnl <= stop_loss or pnl >= take_profit or days_held >= max_hold_days:
                reason = ("stop_loss" if pnl <= stop_loss else
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
                })
                del holdings[ticker]

        # Check entries
        n_open = len(holdings)
        if n_open < MAX_POSITIONS and cash > 0:
            slot_size = INITIAL_CASH / MAX_POSITIONS
            scores = []
            for ticker, df in all_data.items():
                if ticker in holdings or today not in df.index:
                    continue
                t_idx   = list(df.index).index(today)
                closes  = df["close"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]
                volumes = df["volume"].iloc[max(0, t_idx-250):t_idx+1].tolist()[::-1]
                score   = score_ticker(closes, volumes, regime)
                if score >= score_thresh:
                    scores.append((ticker, score, df.loc[today, "close"]))

            scores.sort(key=lambda x: x[1], reverse=True)
            slots = MAX_POSITIONS - n_open

            for ticker, score, price in scores[:slots]:
                invest = min(slot_size, cash * 0.95)
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
                    }

    # Close all at end
    last_date = all_dates[-1]
    for ticker, pos in list(holdings.items()):
        if ticker in all_data and last_date in all_data[ticker].index:
            cur_price = all_data[ticker].loc[last_date, "close"]
            pnl = (cur_price - pos["entry_price"]) / pos["entry_price"]
            proceeds = pos["shares"] * cur_price - FEE_US
            cash += proceeds
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
            })

    if not trades:
        return None

    final_equity = cash
    total_ret    = (final_equity / INITIAL_CASH - 1) * 100
    years        = len(all_dates) / 252
    cagr         = ((final_equity / INITIAL_CASH) ** (1/years) - 1) * 100

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
    sharpe = (dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else 0

    return {
        "params":        params,
        "total_return":  round(total_ret, 1),
        "cagr":          round(cagr, 1),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 1),
        "avg_loss":      round(avg_loss, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown":  round(max_dd, 1),
        "sharpe":        round(float(sharpe), 2),
        "n_trades":      len(trades),
        "equity":        [round(e, 2) for e in equity],
        "trades":        trades,
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

    logger.info(f"S&P500 return: {bench_ret:.1f}% | {len(all_dates)} trading days")
    logger.info(f"Testing {len(list(product(*PARAM_GRID.values())))} combinations...")

    results = []
    for combo in product(*PARAM_GRID.values()):
        params = dict(zip(PARAM_GRID.keys(), combo))
        r = run_single(all_data, bench_df, all_dates, params)
        if r:
            results.append(r)
            logger.info(
                f"  thresh={params['score_thresh']} sl={params['stop_loss']} "
                f"tp={params['take_profit']} hold={params['max_hold_days']}d "
                f"→ {r['total_return']}% CAGR={r['cagr']}% "
                f"WR={r['win_rate']}% PF={r['profit_factor']} n={r['n_trades']}"
            )

    if not results:
        logger.error("No results"); return

    best = max(results, key=lambda x: x["total_return"])
    logger.info(f"\n{'='*60}")
    logger.info(f"BEST: {best['params']}")
    logger.info(f"  Total return : {best['total_return']}%  (S&P500: {bench_ret:.1f}%)")
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

    # Sheet 1 — Optimization
    ws1 = wb.active
    ws1.title = "Optimization"
    hdrs = ["Score Thresh","Stop Loss","Take Profit","Max Hold",
            "Total Return%","CAGR%","Win Rate%","Avg Win%","Avg Loss%",
            "Profit Factor","Max DD%","Sharpe","Trades"]
    for c, h in enumerate(hdrs, 1):
        cell = ws1.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft

    for r_idx, r in enumerate(sorted(results, key=lambda x: x["total_return"], reverse=True), 2):
        p = r["params"]
        vals = [p["score_thresh"], p["stop_loss"], p["take_profit"], p["max_hold_days"],
                r["total_return"], r["cagr"], r["win_rate"], r["avg_win"], r["avg_loss"],
                r["profit_factor"], r["max_drawdown"], r["sharpe"], r["n_trades"]]
        fill = gf if r["total_return"] > bench_ret else (yf2 if r["total_return"] > 0 else rf)
        for c, v in enumerate(vals, 1):
            ws1.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFGHIJKLM":
        ws1.column_dimensions[col].width = 14

    # Sheet 2 — Best Config
    ws2 = wb.create_sheet("Best Config")
    summary = [
        ("", "Best Model", f"S&P500"),
        ("Score Threshold", best["params"]["score_thresh"], ""),
        ("Stop Loss", f"{best['params']['stop_loss']*100:.0f}%", ""),
        ("Take Profit", f"{best['params']['take_profit']*100:.0f}%", ""),
        ("Max Hold Days", best["params"]["max_hold_days"], ""),
        ("", "", ""),
        ("Total Return", f"{best['total_return']}%", f"{bench_ret:.1f}%"),
        ("CAGR", f"{best['cagr']}%/year", ""),
        ("Win Rate", f"{best['win_rate']}%", ""),
        ("Avg Win", f"{best['avg_win']}%", ""),
        ("Avg Loss", f"{best['avg_loss']}%", ""),
        ("Profit Factor", best["profit_factor"], ""),
        ("Max Drawdown", f"{best['max_drawdown']}%", ""),
        ("Sharpe Ratio", best["sharpe"], ""),
        ("Total Trades", best["n_trades"], ""),
        ("Initial Capital", f"${INITIAL_CASH:,.0f}", ""),
        ("Final Capital", f"${INITIAL_CASH * (1 + best['total_return']/100):,.0f}", ""),
    ]
    for r_idx, row in enumerate(summary, 1):
        for c, val in enumerate(row, 1):
            cell = ws2.cell(row=r_idx, column=c, value=val)
            if r_idx == 1: cell.fill = hf; cell.font = hft
    for col in "ABC":
        ws2.column_dimensions[col].width = 22

    # Sheet 3 — Best Trades
    ws3 = wb.create_sheet("Best Trades")
    for c, h in enumerate(["Ticker","Entry","Exit","P&L%","Reason","Regime","Days"], 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, t in enumerate(sorted(best["trades"], key=lambda x: x["entry_date"]), 2):
        vals = [t["ticker"], t["entry_date"], t["exit_date"],
                t["pnl_pct"], t["reason"], t["regime"], t["days_held"]]
        fill = gf if t["pnl_pct"] > 0 else rf
        for c, v in enumerate(vals, 1):
            ws3.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFG":
        ws3.column_dimensions[col].width = 15

    # Sheet 4 — Equity Curve
    ws4 = wb.create_sheet("Equity Curve")
    ws4.cell(row=1, column=1, value="Day")
    ws4.cell(row=1, column=2, value="Model ($)")
    ws4.cell(row=1, column=3, value="S&P500 (normalized to $10k)")
    bench_norm = bench_df["close"] / bench_df["close"].iloc[0] * INITIAL_CASH
    b_list     = list(bench_df.index)
    for i, (d, e) in enumerate(zip(all_dates, best["equity"]), 2):
        b_val = bench_norm.get(d) if d in bench_norm.index else None
        ws4.cell(row=i, column=1, value=str(d))
        ws4.cell(row=i, column=2, value=e)
        ws4.cell(row=i, column=3, value=round(float(b_val), 2) if b_val is not None else None)

    chart = LineChart()
    chart.title = f"Model vs S&P500 — thresh={best['params']['score_thresh']} sl={best['params']['stop_loss']} tp={best['params']['take_profit']}"
    chart.style = 10
    n = len(best["equity"]) + 1
    chart.add_data(Reference(ws4, min_col=2, max_col=3, min_row=1, max_row=n), titles_from_data=True)
    chart.width = 28; chart.height = 16
    ws4.add_chart(chart, "E2")

    wb.save("backtest_results.xlsx")
    logger.info("Saved backtest_results.xlsx")


if __name__ == "__main__":
    run_backtest()
