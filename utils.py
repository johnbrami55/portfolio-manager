import logging
import os
import time
import requests
import numpy as np
from config import DEGIRO_FIXED_FEE, DEGIRO_VARIABLE_FEE, SECTOR_MAP

logger = logging.getLogger(__name__)
AV_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

def convert_ticker(ticker):
    return ticker.replace(".PA", ".PAR").replace(".AS", ".AMS").replace(".MI", ".MIL")

def calculate_fee(position_eur):
    return DEGIRO_FIXED_FEE + DEGIRO_VARIABLE_FEE * position_eur

def calculate_roundtrip_fee(position_eur):
    return calculate_fee(position_eur) * 2

def roundtrip_fee_pct(position_eur):
    if position_eur <= 0:
        return 1.0
    return calculate_roundtrip_fee(position_eur) / position_eur

def sector_of(ticker):
    return SECTOR_MAP.get(ticker, "Unknown")

def fetch_returns(ticker):
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "TIME_SERIES_DAILY", "symbol": convert_ticker(ticker),
                    "apikey": AV_KEY, "outputsize": "compact"},
            timeout=15,
        )
        ts = r.json().get("Time Series (Daily)", {})
        dates  = sorted(ts.keys(), reverse=True)[:60]
        closes = [float(ts[d]["4. close"]) for d in dates]
        return [closes[i]/closes[i+1]-1 for i in range(len(closes)-1)]
    except Exception:
        return []

def compute_betas(tickers):
    logger.info(f"Computing betas for {len(tickers)} tickers...")
    betas = {}

    # Use MC.PA as CAC40 proxy
    proxy_returns = fetch_returns("MC.PA")
    time.sleep(12)

    if not proxy_returns:
        return {t: 1.0 for t in tickers}

    for ticker in tickers:
        try:
            stock_returns = fetch_returns(ticker)
            time.sleep(12)
            if not stock_returns or len(stock_returns) < 20:
                betas[ticker] = 1.0
                continue
            n   = min(len(proxy_returns), len(stock_returns))
            s   = np.array(stock_returns[:n])
            m   = np.array(proxy_returns[:n])
            cov = np.cov(s, m)[0, 1]
            var = np.var(m)
            betas[ticker] = round(float(cov/var) if var != 0 else 1.0, 3)
        except Exception:
            betas[ticker] = 1.0

    return betas

def portfolio_beta(positions):
    total_val = sum(p.get("position_eur", 0) for p in positions.values())
    if total_val == 0:
        return 0.0
    w_beta = sum(p.get("beta", 1.0) * p.get("position_eur", 0) for p in positions.values())
    return round(w_beta / total_val, 3)

def sector_exposure(positions):
    total_val = sum(p.get("position_eur", 0) for p in positions.values())
    if total_val == 0:
        return {}
    sector_eur = {}
    for ticker, pos in positions.items():
        s = sector_of(ticker)
        sector_eur[s] = sector_eur.get(s, 0) + pos.get("position_eur", 0)
    return {s: round(v/total_val, 3) for s, v in sector_eur.items()}

def format_portfolio_snapshot(state):
    positions = state.get("positions", {})
    cash_eur  = state.get("cash_eur", 0)
    total_pnl = state.get("performance", {}).get("total_pnl_eur", 0)
    initial   = state.get("initial_capital", 1890)
    regime    = state.get("current_regime", "?")
    pb        = portfolio_beta(positions)
    sectors   = sector_exposure(positions)

    lines = [
        f"Portfolio | Regime: {regime}",
        f"Beta: {pb:.2f} | Cash: {cash_eur:.0f} EUR",
        f"P&L: {total_pnl:+.2f} EUR ({total_pnl/initial:+.1%})",
        "---",
    ]
    for ticker, pos in positions.items():
        entry   = pos.get("entry_price", 0)
        price   = entry
        pnl_pct = (price - entry) / entry if entry else 0
        pnl_eur = (price - entry) * pos.get("nb_shares", 0)
        lines.append(
            f"{ticker}: {pos['nb_shares']}x {entry:.2f} | {price:.2f} "
            f"({pnl_pct:+.1%} | {pnl_eur:+.0f}EUR)"
        )
    lines.append("Sectors: " + " | ".join(f"{s} {w:.0%}" for s, w in sectors.items()))
    return "\n".join(lines)
