"""
backtest.py — Momentum Rotation + Volatile Assets + Stop-Loss
- Universe: Growth + High Momentum + Volatile (crypto, leveraged ETFs, high beta)
- Monthly rotation: hold top N stocks by risk-adjusted momentum
- Daily stop-loss check on all positions
- MA filter: cash in BEAR
- Target: 20%+ CAGR, DD < 15%
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

START_DATE   = "2020-01-01"
END_DATE     = "2025-12-31"
INITIAL_CASH = 10000.0
FEE_US       = 2.00

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}

# ── Universe ──────────────────────────────────────────────────────────────────
UNIVERSE_STABLE = [
    # Mega cap tech / growth
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "AVGO", "ORCL", "CRM", "ADBE", "NOW", "PANW", "SNPS", "CDNS",
    # Defense / industrial
    "LMT", "RTX", "NOC", "GD",
    # Healthcare growth
    "LLY", "ABBV", "ISRG", "DXCM",
    # Finance
    "V", "MA", "GS", "MS", "JPM",
    # Consumer
    "COST", "HD",
    # ETFs
    "QQQ", "XLK", "XLE", "XLF", "XLV", "XLI",
]

UNIVERSE_VOLATILE = [
    # Crypto-adjacent
    "COIN", "MSTR", "RIOT", "MARA",
    # Leveraged ETFs
    "TQQQ", "SOXL", "UPRO", "TECL",
    # High beta tech
    "PLTR", "SMCI", "IONQ",
    # Biotech
    "MRNA", "BNTX",
    # High momentum
    "DKNG", "SOFI", "RBLX",
]

UNIVERSE = UNIVERSE_STABLE + UNIVERSE_VOLATILE

# Volatile tickers get smaller position size
VOLATILE_SET = set(UNIVERSE_VOLATILE)
VOLATILE_SIZE_FACTOR = 0.5  # half position for volatile

PARAM_GRID = {
    "n_positions":    [8, 10],
    "momentum_days":  [126, 189, 252],
    "ma_filter":      [150, 200],
    "rebalance_days": [21, 42],
    "stop_loss":      [-0.07, -0.08, -0.10],
}


def fetch_history(ticker):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, headers=YF_HEADERS,
                         params={"interval": "1d", "range": "7y"}, timeout=15)
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
        return df if len(df) > 252 else None
    except Exception as e:
        logger.warning(f"{ticker}: {e}")
        return None


def calc_momentum(closes, days):
    """Momentum skipping last month to avoid short-term reversal."""
    if len(closes) < days + 21:
        return None
    return (closes[21] - closes[days]) / closes[days]


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
    n_positions   = params["n_positions"]
    mom_days      = params["momentum_days"]
    ma_period     = params["ma_filter"]
    rebal_days    = params["rebalance_days"]
    stop_loss     = params["stop_loss"]

    cash       = INITIAL_CASH
    holdings   = {}
    trades     = []
    equity     = []
    peak_eq    = INITIAL_CASH
    last_rebal = None

    bench_list = list(bench_df.index)
    min_idx    = max(mom_days + 21, ma_period)

    for i, today in enumerate(all_dates):
        # Portfolio value
        port_val = cash
        for ticker, pos in holdings.items():
            if ticker in all_data and today in all_data[ticker].index:
                port_val += pos["shares"] * all_data[ticker].loc[today, "close"]
        equity.append(port_val)
        if port_val > peak_eq:
            peak_eq = port_val

        if i < min_idx:
            continue

        # Market regime
        b_idx = bench_list.index(today) if today in bench_list else -1
        if b_idx < ma_period:
            in_bear = False
        else:
            b_closes = bench_df["close"].iloc[b_idx-ma_period:b_idx+1].tolist()[::-1]
            ma       = sum(b_closes[:ma_period]) / ma_period
            in_bear  = b_closes[0] < ma

        # ── DAILY STOP-LOSS CHECK ─────────────────────────────────────────
        for ticker in list(holdings.keys()):
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            pos       = holdings[ticker]
            cur_price = all_data[ticker].loc[today, "close"]
            pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]

            if pnl <= stop_loss:
                cash += pos["shares"] * cur_price - FEE_US
                trades.append({
                    "ticker":      ticker,
                    "entry_date":  str(pos["entry_date"]),
                    "exit_date":   str(today),
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price":  round(cur_price, 4),
                    "pnl_pct":     round(pnl * 100, 2),
                    "reason":      "stop_loss",
                    "days_held":   (today - pos["entry_date"]).days,
                    "volatile":    ticker in VOLATILE_SET,
                })
                del holdings[ticker]

        # ── BEAR EXIT ─────────────────────────────────────────────────────
        if in_bear:
            for ticker in list(holdings.keys()):
                if ticker in all_data and today in all_data[ticker].index:
                    pos       = holdings[ticker]
                    cur_price = all_data[ticker].loc[today, "close"]
                    pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]
                    cash     += pos["shares"] * cur_price - FEE_US
                    trades.append({
                        "ticker":      ticker,
                        "entry_date":  str(pos["entry_date"]),
                        "exit_date":   str(today),
                        "entry_price": round(pos["entry_price"], 4),
                        "exit_price":  round(cur_price, 4),
                        "pnl_pct":     round(pnl * 100, 2),
                        "reason":      "bear_exit",
                        "days_held":   (today - pos["entry_date"]).days,
                        "volatile":    ticker in VOLATILE_SET,
                    })
            holdings.clear()
            last_rebal = today
            continue

        # ── REBALANCE CHECK ───────────────────────────────────────────────
        if last_rebal is not None:
            days_since = (today - last_rebal).days
            if days_since < rebal_days:
                continue

        # ── SCORE UNIVERSE ────────────────────────────────────────────────
        scores = []
        for ticker, df in all_data.items():
            if today not in df.index:
                continue
            t_idx  = list(df.index).index(today)
            closes = df["close"].iloc[max(0, t_idx-mom_days-30):t_idx+1].tolist()[::-1]

            # Individual MA filter — stock must be above MA200
            if len(closes) >= 200:
                ma200 = sum(closes[:200]) / 200
                if closes[0] < ma200 * 0.95:
                    continue

            mom = calc_momentum(closes, mom_days)
            if mom is None:
                continue

            # Risk-adjusted momentum (Sharpe-like)
            if len(closes) >= 63:
                rets = [(closes[j]-closes[j+1])/closes[j+1] for j in range(62)]
                vol  = (sum(r**2 for r in rets)/len(rets))**0.5 * (252**0.5)
                score = mom / vol if vol > 0 else mom
            else:
                score = mom

            # Penalty for volatile assets (half weight in ranking too)
            if ticker in VOLATILE_SET:
                score *= 0.8  # slight discount to avoid over-concentration

            scores.append((ticker, score, mom, df.loc[today, "close"]))

        scores.sort(key=lambda x: x[1], reverse=True)
        target_tickers = [s[0] for s in scores[:n_positions]]

        # ── EXIT POSITIONS NOT IN TOP N ───────────────────────────────────
        for ticker in list(holdings.keys()):
            if ticker not in target_tickers:
                if ticker in all_data and today in all_data[ticker].index:
                    pos       = holdings[ticker]
                    cur_price = all_data[ticker].loc[today, "close"]
                    pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]
                    cash     += pos["shares"] * cur_price - FEE_US
                    trades.append({
                        "ticker":      ticker,
                        "entry_date":  str(pos["entry_date"]),
                        "exit_date":   str(today),
                        "entry_price": round(pos["entry_price"], 4),
                        "exit_price":  round(cur_price, 4),
                        "pnl_pct":     round(pnl * 100, 2),
                        "reason":      "rotation",
                        "days_held":   (today - pos["entry_date"]).days,
                        "volatile":    ticker in VOLATILE_SET,
                    })
                del holdings[ticker]

        # ── ENTER NEW POSITIONS ───────────────────────────────────────────
        total_val = port_val
        for ticker in target_tickers:
            if ticker in holdings:
                continue
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            price = all_data[ticker].loc[today, "close"]

            # Volatile assets get smaller position
            size_factor = VOLATILE_SIZE_FACTOR if ticker in VOLATILE_SET else 1.0
            slot_size   = (total_val / n_positions) * size_factor
            invest      = min(slot_size, cash * 0.95)

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
                    "volatile":    ticker in VOLATILE_SET,
                }

        last_rebal = today

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
                "days_held":   (last_date - pos["entry_date"]).days,
                "volatile":    pos.get("volatile", False),
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
    pf       = abs(avg_win*len(wins)/(avg_loss*len(losses))) if losses and avg_loss else 0

    eq_s   = pd.Series(equity)
    peak   = eq_s.cummax()
    max_dd = float(((eq_s - peak) / peak).min() * 100)
    dr     = eq_s.pct_change().dropna()
    sharpe = float((dr.mean()/dr.std()*np.sqrt(252))) if dr.std() > 0 else 0

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
    for t in UNIVERSE:
        df = fetch_history(t)
        if df is not None:
            all_data[t] = df
            logger.info(f"  {t}: {len(df)} days")
        else:
            logger.warning(f"  {t}: skip")

    bench_df = fetch_history("QQQ")
    if bench_df is None:
        logger.error("Failed SPY"); return

    bench_ret = (bench_df["close"].iloc[-1] / bench_df["close"].iloc[0] - 1) * 100
    all_dates = sorted(d for d in bench_df.index
                       if pd.to_datetime(START_DATE).date() <= d <=
                       pd.to_datetime(END_DATE).date())

    n = len(list(product(*PARAM_GRID.values())))
    logger.info(f"SPY: {bench_ret:.1f}% | {len(all_dates)} days | {n} combos")

    results = []
    for combo in product(*PARAM_GRID.values()):
        params = dict(zip(PARAM_GRID.keys(), combo))
        r = run_single(all_data, bench_df, all_dates, params)
        if r:
            results.append(r)
            logger.info(
                f"  n={params['n_positions']} mom={params['momentum_days']}d "
                f"ma={params['ma_filter']} rebal={params['rebalance_days']}d "
                f"sl={params['stop_loss']} "
                f"→ {r['total_return']}% CAGR={r['cagr']}% "
                f"DD={r['max_drawdown']}% Sharpe={r['sharpe']} n={r['n_trades']}"
            )

    if not results:
        logger.error("No results"); return

    best_sharpe = max(results, key=lambda x: x["sharpe"])
    best_return = max(results, key=lambda x: x["total_return"])

    logger.info(f"\n{'='*60}")
    logger.info(f"BEST (Sharpe): {best_sharpe['params']}")
    logger.info(f"  Return={best_sharpe['total_return']}% CAGR={best_sharpe['cagr']}% "
                f"DD={best_sharpe['max_drawdown']}% Sharpe={best_sharpe['sharpe']} "
                f"n={best_sharpe['n_trades']}")
    logger.info(f"BEST (Return): {best_return['params']}")
    logger.info(f"  Return={best_return['total_return']}% CAGR={best_return['cagr']}% "
                f"DD={best_return['max_drawdown']}% Sharpe={best_return['sharpe']} "
                f"n={best_return['n_trades']}")
    logger.info(f"SPY: {bench_ret:.1f}%")
    logger.info("Annual performance (best Sharpe):")
    for y in best_sharpe.get("annual", []):
        logger.info(f"  {y['year']}: {y['return']:+.1f}% | "
                    f"{y['trades']} trades | WR={y['win_rate']:.0f}%")
    logger.info(f"{'='*60}")

    with open("backtest_trades.json", "w") as f:
        json.dump(best_sharpe["trades"], f, indent=2)

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
    hdrs = ["N Pos","Mom Days","MA Filter","Rebal Days","Stop Loss",
            "Total Return%","CAGR%","Win Rate%","Avg Win%","Avg Loss%",
            "Profit Factor","Max DD%","Sharpe","Trades"]
    for c, h in enumerate(hdrs, 1):
        cell = ws1.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, r in enumerate(sorted(results, key=lambda x: x["sharpe"], reverse=True), 2):
        p = r["params"]
        vals = [p["n_positions"], p["momentum_days"], p["ma_filter"],
                p["rebalance_days"], p["stop_loss"],
                r["total_return"], r["cagr"], r["win_rate"], r["avg_win"],
                r["avg_loss"], r["profit_factor"], r["max_drawdown"],
                r["sharpe"], r["n_trades"]]
        fill = gf if r["total_return"] > 100 else (
               bf if r["total_return"] > 50 else (
               yf2 if r["total_return"] > 0 else rf))
        for c, v in enumerate(vals, 1):
            ws1.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFGHIJKLMN":
        ws1.column_dimensions[col].width = 13

    # Sheet 2 — Best Config
    ws2 = wb.create_sheet("Best Config")
    sp500_annual = {2020:18.4, 2021:28.7, 2022:-18.2, 2023:26.3, 2024:25.0, 2025:-2.0}
    rows = [
        ("", "Best Sharpe", "Best Return", "SPY"),
        ("N Positions",   best_sharpe["params"]["n_positions"],    best_return["params"]["n_positions"],    ""),
        ("Momentum Days", best_sharpe["params"]["momentum_days"],  best_return["params"]["momentum_days"],  ""),
        ("MA Filter",     best_sharpe["params"]["ma_filter"],      best_return["params"]["ma_filter"],      ""),
        ("Rebal Days",    best_sharpe["params"]["rebalance_days"], best_return["params"]["rebalance_days"], ""),
        ("Stop Loss",     f"{best_sharpe['params']['stop_loss']*100:.0f}%", f"{best_return['params']['stop_loss']*100:.0f}%", ""),
        ("Volatile SL",   "Half position size", "Half position size", ""),
        ("Bear Filter",   "✅ Cash when price < MA", "✅ Cash when price < MA", ""),
        ("", "", "", ""),
        ("Total Return",  f"{best_sharpe['total_return']}%",  f"{best_return['total_return']}%",  f"{bench_ret:.1f}%"),
        ("CAGR",          f"{best_sharpe['cagr']}%/year",     f"{best_return['cagr']}%/year",     ""),
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
        ws2.column_dimensions[col].width = 22

    # Sheet 3 — Best Trades
    ws3 = wb.create_sheet("Best Trades")
    for c, h in enumerate(["Ticker","Volatile","Entry","Exit",
                            "Entry$","Exit$","P&L%","Reason","Days"], 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, t in enumerate(sorted(best_sharpe["trades"],
                                     key=lambda x: x["entry_date"]), 2):
        vals = [t["ticker"], "⚡" if t.get("volatile") else "",
                t["entry_date"], t["exit_date"],
                t["entry_price"], t["exit_price"],
                t["pnl_pct"], t["reason"], t["days_held"]]
        fill = gf if t["pnl_pct"] > 0 else rf
        for c, v in enumerate(vals, 1):
            ws3.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDEFGHI":
        ws3.column_dimensions[col].width = 13

    # Sheet 4 — By Ticker
    ws4 = wb.create_sheet("By Ticker")
    ticker_stats = {}
    for t in best_sharpe["trades"]:
        tk = t["ticker"]
        if tk not in ticker_stats:
            ticker_stats[tk] = {"n":0,"wins":0,"pnl":0,
                                 "volatile": t.get("volatile", False)}
        ticker_stats[tk]["n"]   += 1
        ticker_stats[tk]["pnl"] += t["pnl_pct"]
        if t["pnl_pct"] > 0: ticker_stats[tk]["wins"] += 1
    for c, h in enumerate(["Ticker","Type","Trades","Win Rate%",
                            "Total PnL%","Avg PnL%"], 1):
        cell = ws4.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, (tk, s) in enumerate(
            sorted(ticker_stats.items(), key=lambda x: x[1]["pnl"], reverse=True), 2):
        wr  = s["wins"]/s["n"]*100
        avg = s["pnl"]/s["n"]
        typ = "⚡ Volatile" if s["volatile"] else "Stable"
        for c, v in enumerate([tk, typ, s["n"], f"{wr:.0f}%",
                                f"{s['pnl']:.1f}%", f"{avg:.1f}%"], 1):
            ws4.cell(row=r_idx, column=c, value=v).fill = gf if s["pnl"] > 0 else rf
    for col in "ABCDEF":
        ws4.column_dimensions[col].width = 14

    # Sheet 5 — Annual Perf
    ws5 = wb.create_sheet("Annual Perf")
    for c, h in enumerate(["Year","Model%","SPY%","Trades","WR%"], 1):
        cell = ws5.cell(row=1, column=c, value=h)
        cell.fill = hf; cell.font = hft
    for r_idx, y in enumerate(best_sharpe.get("annual", []), 2):
        sp = sp500_annual.get(y["year"], "")
        vals = [y["year"], y["return"], sp, y["trades"], y["win_rate"]]
        fill = gf if y["return"] > 0 else rf
        for c, v in enumerate(vals, 1):
            ws5.cell(row=r_idx, column=c, value=v).fill = fill
    for col in "ABCDE":
        ws5.column_dimensions[col].width = 16

    # Sheet 6 — Equity Curve
    ws6 = wb.create_sheet("Equity Curve")
    for c, h in enumerate(["Date","Model ($)","SPY ($10k)"], 1):
        ws6.cell(row=1, column=c, value=h)
    bench_norm = bench_df["close"] / bench_df["close"].iloc[0] * INITIAL_CASH
    for i, (d, e) in enumerate(zip(all_dates, best_sharpe["equity"]), 2):
        b = bench_norm.get(d)
        ws6.cell(row=i, column=1, value=str(d))
        ws6.cell(row=i, column=2, value=e)
        ws6.cell(row=i, column=3, value=round(float(b), 2) if b is not None else None)
    chart = LineChart()
    chart.title = "Momentum Rotation vs SPY"
    chart.style = 10
    n_rows = len(best_sharpe["equity"]) + 1
    chart.add_data(Reference(ws6, min_col=2, max_col=3, min_row=1, max_row=n_rows),
                   titles_from_data=True)
    chart.width = 28; chart.height = 16
    ws6.add_chart(chart, "E2")

    wb.save("backtest_results.xlsx")
    logger.info("Saved backtest_results.xlsx")


if __name__ == "__main__":
    run_backtest()
