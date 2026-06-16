"""
backtest.py — Simulate portfolio manager signals on 5 years of historical data.
Uses Yahoo Finance for US/HK tickers (no API limit).
Measures % returns vs S&P500 and Hang Seng benchmarks.
"""
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import requests
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.chart import LineChart, Reference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── Parameters ────────────────────────────────────────────────────────────────
START_DATE     = "2020-01-01"
END_DATE       = "2025-12-31"
SCORE_THRESH   = 52       # same as production BEAR threshold (conservative)
MAX_POSITIONS  = 6
POSITION_SIZE  = 1 / MAX_POSITIONS  # equal weight
STOP_LOSS      = -0.08    # -8%
TAKE_PROFIT    = +0.18    # +18%
RUN_FREQ_DAYS  = 1        # simulate a run every N trading days

# DEGIRO fees
FEE_US  = 2.00  # USD per trade (both buy and sell)

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}

US_TICKERS = [
    "KO", "BAC", "ABT", "NEE", "PFE", "F", "T", "VZ", "KHC", "PYPL",
    "NKE", "GM", "MO", "DIS", "SBUX", "CVS", "XOM", "WMT", "PG", "MRK",
    "PEP", "CSCO", "DHR", "PM", "RTX", "UPS", "NVDA", "AMD", "TSLA", "PLTR",
    "SOFI", "COIN", "PYPL", "SQ", "DKNG",
]

HK_TICKERS = [
    "0700.HK", "0941.HK", "1299.HK", "0005.HK", "0388.HK",
    "2318.HK", "1398.HK", "0939.HK", "0883.HK", "9999.HK",
]

BENCHMARKS = {
    "S&P500": "^GSPC",
    "HangSeng": "^HSI",
}


# ── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_history(ticker: str) -> pd.DataFrame | None:
    """Fetch 5 years of daily OHLCV from Yahoo Finance."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": "6y"}
        r = requests.get(url, headers=YF_HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        ts        = result[0]["timestamp"]
        quote     = result[0]["indicators"]["quote"][0]
        closes    = quote.get("close", [])
        highs     = quote.get("high", [])
        lows      = quote.get("low", [])
        volumes   = quote.get("volume", [])
        dates     = [datetime.utcfromtimestamp(t).date() for t in ts]
        df = pd.DataFrame({
            "date":   dates,
            "close":  closes,
            "high":   highs,
            "low":    lows,
            "volume": volumes,
        }).dropna().set_index("date")
        df = df[(df.index >= pd.to_datetime(START_DATE).date()) &
                (df.index <= pd.to_datetime(END_DATE).date())]
        return df if len(df) > 100 else None
    except Exception as e:
        logger.warning(f"{ticker}: fetch error {e}")
        return None


# ── Scoring (simplified, same logic as scorer.py) ────────────────────────────

def score_ticker_on_day(df: pd.DataFrame, idx: int, regime: str) -> float:
    """Score a ticker at a specific historical date index."""
    if idx < 200:
        return 0.0

    closes  = df["close"].iloc[max(0, idx-250):idx+1].tolist()[::-1]
    volumes = df["volume"].iloc[max(0, idx-250):idx+1].tolist()[::-1]

    if len(closes) < 50:
        return 0.0

    # Trend
    ma20  = sum(closes[:20]) / 20
    ma50  = sum(closes[:50]) / 50
    trend = 0.0
    if len(closes) >= 200:
        ma200 = sum(closes[:200]) / 200
        if closes[0] > ma20: trend += 7/3
        if ma20 > ma50:      trend += 7/3
        if ma50 > ma200:     trend += 7/3
    if regime == "BEAR":
        trend *= 0.7

    # RSI
    deltas = [closes[i] - closes[i+1] for i in range(14)]
    gains  = sum(d for d in deltas if d > 0) / 14
    losses = sum(-d for d in deltas if d < 0) / 14
    rs     = gains / losses if losses != 0 else 100
    rsi    = 100 - 100 / (1 + rs)
    if 40 <= rsi <= 60:   rsi_score = 6.0
    elif rsi < 30:        rsi_score = 4.8
    elif rsi > 70:        rsi_score = 0.0
    else:                 rsi_score = 2.4

    # Volume
    avg_vol = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else 1
    ratio   = volumes[0] / avg_vol if avg_vol > 0 else 0
    if ratio >= 1.5:   vol_score = 8.0
    elif ratio >= 1.2: vol_score = 4.8
    elif ratio >= 0.7: vol_score = 2.4
    else:              vol_score = 0.0

    # MACD
    def ema(data, span):
        k = 2 / (span + 1); e = data[-1]
        for p in reversed(data[:-1]): e = p * k + e * (1 - k)
        return e
    macd = ema(closes[:12], 12) - ema(closes[:26], 26)
    macd_score = 3.0 if macd > 0 else 0.0

    # Momentum (3M)
    mom = (closes[0] - closes[62]) / closes[62] if len(closes) > 62 else 0
    if regime == "BEAR": mom_score = 0.0 if mom < 0 else 2.1
    else:                mom_score = 4.2 if mom > 0.05 else 2.1

    # Bollinger
    sma = sum(closes[:20]) / 20
    std = (sum((x - sma)**2 for x in closes[:20]) / 20) ** 0.5
    pct_b = (closes[0] - (sma - 2*std)) / (4*std) if std > 0 else 0.5
    if pct_b >= 0.6:   boll = 5.6
    elif pct_b >= 0.4: boll = 2.4
    elif pct_b >= 0.1: boll = 4.8 if regime == "BEAR" else 3.2
    else:              boll = 0.0

    # StochRSI (simplified)
    stoch = 3.6 if 20 <= rsi <= 60 else (0.0 if rsi > 80 else 2.4)

    fund  = 15.0  # placeholder
    total = min(100.0, trend + rsi_score + vol_score + macd_score +
                mom_score + boll + stoch + fund)
    return total


def detect_regime_on_day(benchmark_df: pd.DataFrame, idx: int) -> str:
    """Detect regime from S&P500 MA50 vs MA200."""
    if idx < 200:
        return "NEUTRAL"
    closes = benchmark_df["close"].iloc[idx-200:idx+1].tolist()
    ma50   = sum(closes[-50:]) / 50
    ma200  = sum(closes) / 201
    if ma50 > ma200 * 1.02:  return "BULL"
    elif ma50 < ma200 * 0.98: return "BEAR"
    return "NEUTRAL"


# ── Backtest Engine ───────────────────────────────────────────────────────────

def run_backtest():
    logger.info("Downloading data...")

    # Download all tickers
    all_data = {}
    for t in US_TICKERS + HK_TICKERS:
        df = fetch_history(t)
        if df is not None and len(df) > 200:
            all_data[t] = df
            logger.info(f"  {t}: {len(df)} days")
        else:
            logger.warning(f"  {t}: insufficient data")

    # Download benchmark (S&P500)
    bench_df = fetch_history("^GSPC")
    if bench_df is None:
        logger.error("Failed to download S&P500 benchmark")
        return

    # Common trading dates
    all_dates = sorted(bench_df.index)
    all_dates = [d for d in all_dates
                 if pd.to_datetime(START_DATE).date() <= d <= pd.to_datetime(END_DATE).date()]

    logger.info(f"Backtest period: {all_dates[0]} → {all_dates[-1]} ({len(all_dates)} trading days)")

    # Portfolio state
    portfolio   = {}  # {ticker: {"entry_price": x, "entry_date": d, "shares": 1}}
    trades      = []
    equity      = [1.0]  # normalized to 1.0 at start
    equity_dates = [all_dates[0]]
    cash_pct    = 1.0   # 100% at start

    bench_start = bench_df.loc[all_dates[0], "close"]

    for i, today in enumerate(all_dates):
        if i < 200:
            equity.append(equity[-1])
            equity_dates.append(today)
            continue

        regime = detect_regime_on_day(bench_df, list(bench_df.index).index(today))
        thresh = {"BULL": 45, "NEUTRAL": 48, "BEAR": SCORE_THRESH}[regime]

        # Check exits
        for ticker in list(portfolio.keys()):
            if ticker not in all_data or today not in all_data[ticker].index:
                continue
            pos        = portfolio[ticker]
            cur_price  = all_data[ticker].loc[today, "close"]
            pnl        = (cur_price - pos["entry_price"]) / pos["entry_price"]
            days_held  = (today - pos["entry_date"]).days

            sell = False
            reason = ""
            if pnl <= STOP_LOSS:   sell = True; reason = "stop_loss"
            elif pnl >= TAKE_PROFIT: sell = True; reason = "take_profit"
            elif days_held >= 60:  sell = True; reason = "timeout"

            if sell:
                fee_pct = FEE_US / (cur_price * 10) if ticker not in [t for t in HK_TICKERS] else 0.002
                net_pnl = pnl - fee_pct * 2
                trades.append({
                    "ticker":      ticker,
                    "entry_date":  str(pos["entry_date"]),
                    "exit_date":   str(today),
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price":  round(cur_price, 4),
                    "pnl_pct":     round(net_pnl * 100, 2),
                    "reason":      reason,
                    "regime":      regime,
                    "days_held":   days_held,
                })
                cash_pct += (1 / MAX_POSITIONS) * (1 + net_pnl)
                del portfolio[ticker]

        # Check entries (every RUN_FREQ_DAYS)
        if i % RUN_FREQ_DAYS == 0 and len(portfolio) < MAX_POSITIONS:
            scores = []
            for ticker, df in all_data.items():
                if ticker in portfolio or today not in df.index:
                    continue
                df_idx = list(df.index).index(today)
                score  = score_ticker_on_day(df, df_idx, regime)
                if score >= thresh:
                    scores.append((ticker, score, df.loc[today, "close"]))

            scores.sort(key=lambda x: x[1], reverse=True)
            slots = MAX_POSITIONS - len(portfolio)

            for ticker, score, price in scores[:slots]:
                fee_pct = FEE_US / (price * 10)
                portfolio[ticker] = {
                    "entry_price": price * (1 + fee_pct),
                    "entry_date":  today,
                    "score":       score,
                }
                cash_pct -= 1 / MAX_POSITIONS

        # Update equity curve
        pos_value = 0.0
        for ticker, pos in portfolio.items():
            if ticker in all_data and today in all_data[ticker].index:
                cur    = all_data[ticker].loc[today, "close"]
                pos_value += (cur / pos["entry_price"] - 1) / MAX_POSITIONS
        equity.append(equity[-1] * (1 + pos_value / max(len(equity), 1))
                      if len(portfolio) > 0 else equity[-1])
        equity_dates.append(today)

    # Final equity (close all positions at last price)
    for ticker, pos in portfolio.items():
        last_date  = all_dates[-1]
        if ticker in all_data and last_date in all_data[ticker].index:
            cur_price = all_data[ticker].loc[last_date, "close"]
            pnl       = (cur_price - pos["entry_price"]) / pos["entry_price"]
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

    # ── Metrics ──
    total_return  = (equity[-1] - 1) * 100
    years         = len(all_dates) / 252
    cagr          = ((equity[-1]) ** (1 / years) - 1) * 100
    bench_end     = bench_df.loc[all_dates[-1], "close"]
    bench_return  = (bench_end / bench_start - 1) * 100

    wins          = [t for t in trades if t["pnl_pct"] > 0]
    losses        = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate      = len(wins) / len(trades) * 100 if trades else 0
    avg_win       = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss      = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    profit_factor = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss != 0 else 0

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak: peak = e
        dd = (e - peak) / peak
        if dd < max_dd: max_dd = dd

    # Sharpe ratio (annualized, risk-free = 0)
    eq_series   = pd.Series(equity)
    daily_ret   = eq_series.pct_change().dropna()
    sharpe      = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0

    logger.info(f"\n{'='*50}")
    logger.info(f"Total return   : {total_return:.1f}%")
    logger.info(f"CAGR           : {cagr:.1f}%/year")
    logger.info(f"S&P500 return  : {bench_return:.1f}%")
    logger.info(f"Win rate       : {win_rate:.1f}%")
    logger.info(f"Avg win        : {avg_win:.1f}%")
    logger.info(f"Avg loss       : {avg_loss:.1f}%")
    logger.info(f"Profit factor  : {profit_factor:.2f}")
    logger.info(f"Max drawdown   : {max_dd*100:.1f}%")
    logger.info(f"Sharpe ratio   : {sharpe:.2f}")
    logger.info(f"Total trades   : {len(trades)}")
    logger.info(f"{'='*50}")

    # ── Save trades JSON ──
    with open("backtest_trades.json", "w") as f:
        json.dump(trades, f, indent=2)

    # ── Excel Report ──
    wb = openpyxl.Workbook()

    # Sheet 1 - Summary
    ws = wb.active
    ws.title = "Summary"
    header_fill = PatternFill("solid", fgColor="1a1a2e")
    header_font = Font(color="FFFFFF", bold=True)

    metrics = [
        ("Metric", "Model", "S&P500"),
        ("Total Return", f"{total_return:.1f}%", f"{bench_return:.1f}%"),
        ("CAGR", f"{cagr:.1f}%/year", ""),
        ("Win Rate", f"{win_rate:.1f}%", ""),
        ("Avg Win", f"{avg_win:.1f}%", ""),
        ("Avg Loss", f"{avg_loss:.1f}%", ""),
        ("Profit Factor", f"{profit_factor:.2f}", ""),
        ("Max Drawdown", f"{max_dd*100:.1f}%", ""),
        ("Sharpe Ratio", f"{sharpe:.2f}", ""),
        ("Total Trades", str(len(trades)), ""),
        ("Period", f"{START_DATE} → {END_DATE}", ""),
    ]

    for row_idx, row in enumerate(metrics, 1):
        for col_idx, val in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            if row_idx == 1:
                cell.fill = header_fill
                cell.font = header_font

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 20

    # Sheet 2 - All Trades
    ws2 = wb.create_sheet("Trades")
    headers = ["Ticker", "Entry Date", "Exit Date", "Entry Price",
               "Exit Price", "P&L %", "Reason", "Regime", "Days Held"]
    for col, h in enumerate(headers, 1):
        cell = ws2.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font

    green_fill = PatternFill("solid", fgColor="c8e6c9")
    red_fill   = PatternFill("solid", fgColor="ffcdd2")

    for row_idx, t in enumerate(sorted(trades, key=lambda x: x["entry_date"]), 2):
        vals = [t["ticker"], t["entry_date"], t["exit_date"],
                t["entry_price"], t["exit_price"], t["pnl_pct"],
                t["reason"], t["regime"], t["days_held"]]
        fill = green_fill if t["pnl_pct"] > 0 else red_fill
        for col, val in enumerate(vals, 1):
            cell = ws2.cell(row=row_idx, column=col, value=val)
            cell.fill = fill

    for col in ["A","B","C","D","E","F","G","H","I"]:
        ws2.column_dimensions[col].width = 15

    # Sheet 3 - Equity Curve
    ws3 = wb.create_sheet("Equity Curve")
    ws3.cell(row=1, column=1, value="Date")
    ws3.cell(row=1, column=2, value="Model")
    ws3.cell(row=1, column=3, value="S&P500 (normalized)")

    bench_vals = bench_df.loc[bench_df.index.isin(equity_dates), "close"]
    bench_norm = bench_vals / bench_vals.iloc[0] if len(bench_vals) > 0 else pd.Series([1.0])

    for i, (d, e) in enumerate(zip(equity_dates, equity), 2):
        ws3.cell(row=i, column=1, value=str(d))
        ws3.cell(row=i, column=2, value=round(e, 4))
        b_val = bench_norm.get(d, None)
        ws3.cell(row=i, column=3, value=round(float(b_val), 4) if b_val else None)

    # Chart
    chart = LineChart()
    chart.title = "Model vs S&P500"
    chart.style = 10
    chart.y_axis.title = "Return (normalized)"
    chart.x_axis.title = "Date"
    data_ref  = Reference(ws3, min_col=2, max_col=3, min_row=1, max_row=len(equity)+1)
    chart.add_data(data_ref, titles_from_data=True)
    chart.width  = 25
    chart.height = 15
    ws3.add_chart(chart, "E2")

    # Sheet 4 - By Ticker
    ws4 = wb.create_sheet("By Ticker")
    ticker_stats = {}
    for t in trades:
        tk = t["ticker"]
        if tk not in ticker_stats:
            ticker_stats[tk] = {"trades": 0, "wins": 0, "total_pnl": 0}
        ticker_stats[tk]["trades"] += 1
        ticker_stats[tk]["total_pnl"] += t["pnl_pct"]
        if t["pnl_pct"] > 0:
            ticker_stats[tk]["wins"] += 1

    ws4_headers = ["Ticker", "Trades", "Win Rate", "Total P&L %", "Avg P&L %"]
    for col, h in enumerate(ws4_headers, 1):
        cell = ws4.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font

    for row_idx, (tk, stats) in enumerate(
            sorted(ticker_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True), 2):
        wr  = stats["wins"] / stats["trades"] * 100
        avg = stats["total_pnl"] / stats["trades"]
        for col, val in enumerate(
                [tk, stats["trades"], f"{wr:.0f}%",
                 f"{stats['total_pnl']:.1f}%", f"{avg:.1f}%"], 1):
            ws4.cell(row=row_idx, column=col, value=val)

    wb.save("backtest_results.xlsx")
    logger.info("Saved backtest_results.xlsx")


if __name__ == "__main__":
    run_backtest()
