"""
universe.py — Stock universe definition + liquidity filter.
Returns the list of tickers that pass all liquidity checks.
"""

import logging
import yfinance as yf
import pandas as pd
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)


def _check_liquidity(ticker: str, regime: str) -> tuple[bool, dict]:
    """
    Check liquidity for a single ticker.
    Returns (passes: bool, metrics: dict).
    """
    try:
        stock = yf.Ticker(ticker)
        hist  = stock.history(period=f"{LIQUIDITY_LOOKBACK_DAYS + 5}d")

        if hist.empty or len(hist) < 5:
            logger.debug(f"{ticker}: no price history, excluded")
            return False, {}

        # Use last LOOKBACK_DAYS rows
        hist = hist.tail(LIQUIDITY_LOOKBACK_DAYS)

        # Average daily EUR volume proxy: Volume * Close
        avg_volume_eur = (hist["Volume"] * hist["Close"]).mean()

        # Bid-ask proxy: avg((High-Low)/Close)
        avg_spread = ((hist["High"] - hist["Low"]) / hist["Close"]).mean()

        # Market cap from .info (graceful fallback)
        info       = stock.info
        market_cap = info.get("marketCap", 0) or 0
        currency   = info.get("currency", "EUR")

        # Rough EUR conversion if needed (assume 1:1 for EUR-quoted stocks)
        # For stocks quoted in EUR exchange, Close is already EUR
        last_close = float(hist["Close"].iloc[-1])

        # Tighter filters in BEAR
        min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
        min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

        metrics = {
            "avg_volume_eur": avg_volume_eur,
            "avg_spread":     avg_spread,
            "market_cap":     market_cap,
            "last_close":     last_close,
        }

        if avg_volume_eur < min_vol:
            logger.debug(f"{ticker}: volume {avg_volume_eur:.0f} EUR < {min_vol:.0f} threshold")
            return False, metrics
        if market_cap < min_cap:
            logger.debug(f"{ticker}: mkt cap {market_cap:.0f} < {min_cap:.0f} threshold")
            return False, metrics
        if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
            logger.debug(f"{ticker}: spread {avg_spread:.3f} > {LIQUIDITY_MAX_SPREAD_PCT} threshold")
            return False, metrics

        return True, metrics

    except Exception as e:
        logger.warning(f"{ticker}: liquidity check error — {e}")
        return False, {}


def get_liquid_universe(regime: str) -> list[dict]:
    """
    Filter FULL_UNIVERSE by liquidity constraints.
    Returns list of dicts: {ticker, metrics}.
    """
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers for liquidity (regime={regime})...")
    passed = []

    for ticker in FULL_UNIVERSE:
        ok, metrics = _check_liquidity(ticker, regime)
        if ok:
            passed.append({"ticker": ticker, "metrics": metrics})

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
