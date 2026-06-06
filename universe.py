bash

cat > /home/claude/portfolio_manager/universe.py << 'PYEOF'
"""
universe.py — Stock universe definition + liquidity filter.
Includes rate-limit handling with delays between yfinance calls.
"""

import logging
import time
import yfinance as yf
import pandas as pd
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)

# Delay between each ticker fetch to avoid rate limiting
FETCH_DELAY_SECONDS = 2.0


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

        hist = hist.tail(LIQUIDITY_LOOKBACK_DAYS)

        avg_volume_eur = (hist["Volume"] * hist["Close"]).mean()
        avg_spread     = ((hist["High"] - hist["Low"]) / hist["Close"]).mean()

        info       = stock.info
        market_cap = info.get("marketCap", 0) or 0
        last_close = float(hist["Close"].iloc[-1])

        min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
        min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

        metrics = {
            "avg_volume_eur": avg_volume_eur,
            "avg_spread":     avg_spread,
            "market_cap":     market_cap,
            "last_close":     last_close,
        }

        if avg_volume_eur < min_vol:
            logger.debug(f"{ticker}: volume {avg_volume_eur:.0f} EUR < {min_vol:.0f}")
            return False, metrics
        if market_cap < min_cap:
            logger.debug(f"{ticker}: mkt cap {market_cap:.0f} < {min_cap:.0f}")
            return False, metrics
        if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
            logger.debug(f"{ticker}: spread {avg_spread:.3f} > {LIQUIDITY_MAX_SPREAD_PCT}")
            return False, metrics

        return True, metrics

    except Exception as e:
        logger.warning(f"{ticker}: liquidity check error — {e}")
        return False, {}


def get_liquid_universe(regime: str) -> list[dict]:
    """
    Filter FULL_UNIVERSE by liquidity constraints.
    Adds delay between calls to avoid Yahoo Finance rate limiting.
    Returns list of dicts: {ticker, metrics}.
    """
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers for liquidity (regime={regime})...")
    passed = []

    for i, ticker in enumerate(FULL_UNIVERSE):
        ok, metrics = _check_liquidity(ticker, regime)
        if ok:
            passed.append({"ticker": ticker, "metrics": metrics})

        # Rate limit protection: pause between each request
        if i < len(FULL_UNIVERSE) - 1:
            time.sleep(FETCH_DELAY_SECONDS)

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
PYEOF
Sortie

exit code 0
Terminé
Voici le contenu corrigé — copie-colle le dans GitHu
