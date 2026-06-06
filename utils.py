import logging
import time
import numpy as np
import yfinance as yf
from config import (
    DEGIRO_FIXED_FEE, DEGIRO_VARIABLE_FEE, SECTOR_MAP,
    CAC40_TICKER,
)

logger = logging.getLogger(__name__)


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

def compute_betas(tickers):
    logger.info(f"Computing betas for {len(tickers)} tickers...")
    betas = {}
    try:
        index_data = yf.download(CAC40_TICKER, period="1y", progress=False, auto_adjust=True)
        index_ret = index_data["Close"].pct_change().dropna()
    except Exception as e:
        logger.error(f"Failed to download CAC40: {e}")
        return {t: 1.0 for t in tickers}

    for ticker in tickers:
        try:
            time.sleep(1)
            stock_data = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
            if stock_data.empty:
                betas[ticker] = 1.0
                continue
            stock_ret = stock_data["Close"].pct_change().dropna()
            common = stock_ret.index.intersection(index_ret.index)
            if len(common) < 30:
                betas[ticker] = 1.0
                continue
            s = stock_ret.loc[common].values
            m = index_ret.loc[common].values
            cov = np.cov(s, m)[0, 1]
            var = np.var(m)
            betas[ticker] = round(float(cov / var) if var != 0 else 1.0, 3)
        except Exception as e:
            logger.debug(f"Beta failed {ticker}: {e}")
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
    return {s: round(v / total_val, 3) for s, v in sector_eur.items()}

def format_portfolio_snapshot(state):
    import yfinance as yf
    positions = state.get("positions", {})
    cash_eur = state.get("cash_eur", 0)
    total_pnl = state.get("performance", {}).get("total_pnl_eur", 0)
    initial = state.get("initial_capital", 1890)
    regime = state.get("current_regime", "?")
    pb = portfolio_beta(positions)
    sectors = sector_exposure(positions)

    lines = [
        f"Portfolio | Regime: {regime}",
        f"Beta: {pb:.2f} | Cash: {cash_eur:.0f} EUR",
        f"P&L: {total_pnl:+.2f} EUR ({total_pnl/initial:+.1%})",
        "---",
    ]
    for ticker, pos in positions.items():
        entry = pos.get("entry_price", 0)
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            price = float(hist["Close"].iloc[-1]) if not hist.empty else entry
        except Exception:
            price = entry
        pnl_pct = (price - entry) / entry if entry else 0
        pnl_eur = (price - entry) * pos.get("nb_shares", 0)
        lines.append(f"{ticker}: {pos['nb_shares']}x {entry:.2f} | {price:.2f} ({pnl_pct:+.1%} | {pnl_eur:+.0f}EUR)")

    lines.append("Sectors: " + " | ".join(f"{s} {w:.0%}" for s, w in sectors.items()))
    return "\n".join(lines)
