"""
utils.py — Utility functions: beta calculation, sector mapping, fee calculator.
"""

import logging
import numpy as np
import yfinance as yf
import pandas as pd
from config import (
    DEGIRO_FIXED_FEE, DEGIRO_VARIABLE_FEE, SECTOR_MAP,
    CAC40_TICKER, MA_SHORT, MA_LONG,
)

logger = logging.getLogger(__name__)


def calculate_fee(position_eur: float) -> float:
    """
    Calculate DEGIRO fee for a single-leg order (Euronext).
    Fee = 0.50 EUR + 0.004% of order value.
    """
    return DEGIRO_FIXED_FEE + DEGIRO_VARIABLE_FEE * position_eur


def calculate_roundtrip_fee(position_eur: float) -> float:
    """Round-trip fee (buy + sell)."""
    return calculate_fee(position_eur) * 2


def roundtrip_fee_pct(position_eur: float) -> float:
    """Round-trip fee as % of position."""
    if position_eur <= 0:
        return 1.0
    return calculate_roundtrip_fee(position_eur) / position_eur


def sector_of(ticker: str) -> str:
    """Return sector for a ticker from SECTOR_MAP."""
    return SECTOR_MAP.get(ticker, "Unknown")


def compute_beta(ticker: str, period: str = "1y") -> float:
    """
    Compute beta vs CAC40.
    Returns 1.0 on failure (neutral assumption).
    """
    try:
        stock_data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        index_data = yf.download(CAC40_TICKER, period=period, progress=False, auto_adjust=True)

        if stock_data.empty or index_data.empty:
            return 1.0

        stock_ret = stock_data["Close"].pct_change().dropna()
        index_ret = index_data["Close"].pct_change().dropna()

        # Align on common dates
        common = stock_ret.index.intersection(index_ret.index)
        if len(common) < 30:
            return 1.0

        s = stock_ret.loc[common].values
        m = index_ret.loc[common].values

        cov  = np.cov(s, m)[0, 1]
        var  = np.var(m)
        beta = cov / var if var != 0 else 1.0
        return round(float(beta), 3)

    except Exception as e:
        logger.debug(f"Beta computation failed for {ticker}: {e}")
        return 1.0


def compute_betas(tickers: list[str]) -> dict:
    """
    Compute betas for a list of tickers.
    Returns dict {ticker: beta}.
    """
    logger.info(f"Computing betas for {len(tickers)} tickers...")
    betas = {}

    # Download index once
    try:
        index_data = yf.download(CAC40_TICKER, period="1y", progress=False, auto_adjust=True)
        index_ret  = index_data["Close"].pct_change().dropna()
    except Exception as e:
        logger.error(f"Failed to download CAC40 for beta: {e}")
        return {t: 1.0 for t in tickers}

    for ticker in tickers:
        try:
            stock_data = yf.download(ticker, period="1y", progress=False, auto_adjust=True)
            if stock_data.empty:
                betas[ticker] = 1.0
                continue

            stock_ret = stock_data["Close"].pct_change().dropna()
            common    = stock_ret.index.intersection(index_ret.index)

            if len(common) < 30:
                betas[ticker] = 1.0
                continue

            s    = stock_ret.loc[common].values
            m    = index_ret.loc[common].values
            cov  = np.cov(s, m)[0, 1]
            var  = np.var(m)
            beta = float(cov / var) if var != 0 else 1.0
            betas[ticker] = round(beta, 3)

        except Exception as e:
            logger.debug(f"Beta failed {ticker}: {e}")
            betas[ticker] = 1.0

    return betas


def portfolio_beta(positions: dict) -> float:
    """Compute weighted average portfolio beta from open positions."""
    total_val = sum(p.get("position_eur", 0) for p in positions.values())
    if total_val == 0:
        return 0.0

    w_beta = sum(
        p.get("beta", 1.0) * p.get("position_eur", 0)
        for p in positions.values()
    )
    return round(w_beta / total_val, 3)


def sector_exposure(positions: dict) -> dict:
    """Compute sector weights from open positions."""
    total_val = sum(p.get("position_eur", 0) for p in positions.values())
    if total_val == 0:
        return {}

    sector_eur = {}
    for ticker, pos in positions.items():
        s = sector_of(ticker)
        sector_eur[s] = sector_eur.get(s, 0) + pos.get("position_eur", 0)

    return {s: round(v / total_val, 3) for s, v in sector_eur.items()}


def format_portfolio_snapshot(state: dict) -> str:
    """Format a text portfolio snapshot for Telegram."""
    positions = state.get("positions", {})
    cash_eur  = state.get("cash_eur", 0)
    total_pnl = state.get("performance", {}).get("total_pnl_eur", 0)
    initial   = state.get("initial_capital", 1890)
    regime    = state.get("current_regime", "?")
    pb        = portfolio_beta(positions)
    sectors   = sector_exposure(positions)

    lines = [
        f"📊 Portfolio Snapshot | Regime: {regime}",
        f"Global beta: {pb:.2f} | Cash: {cash_eur:.0f} EUR",
        f"Total P&L: {total_pnl:+.2f} EUR ({total_pnl/initial:+.1%})",
        "─" * 30,
    ]

    for ticker, pos in positions.items():
        entry = pos.get("entry_price", 0)
        # Attempt to get current price
        try:
            hist  = yf.Ticker(ticker).history(period="2d")
            price = float(hist["Close"].iloc[-1]) if not hist.empty else entry
        except Exception:
            price = entry

        pnl_pct = (price - entry) / entry if entry else 0
        pnl_eur = (price - entry) * pos.get("nb_shares", 0)
        lines.append(
            f"{ticker}: {pos['nb_shares']} @ {entry:.2f} | now {price:.2f} "
            f"({pnl_pct:+.1%} | {pnl_eur:+.0f} EUR) | β={pos.get('beta',1):.2f}"
        )

    lines.append("─" * 30)
    lines.append("Sectors: " + " | ".join(f"{s} {w:.0%}" for s, w in sectors.items()))
    return "\n".join(lines)
