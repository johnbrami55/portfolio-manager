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

def get_liquid_universe(regime):
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")
    passed = []

    min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
    min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

    # Download all tickers in one batch request
    try:
        data = yf.download(
            FULL_UNIVERSE,
            period=f"{LIQUIDITY_LOOKBACK_DAYS + 5}d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
    except Exception as e:
        logger.error(f"Batch download failed: {e}")
        return []

    for ticker in FULL_UNIVERSE:
        try:
            if len(FULL_UNIVERSE) > 1:
                hist = data[ticker].dropna(how="all").tail(LIQUIDITY_LOOKBACK_DAYS)
            else:
                hist = data.dropna(how="all").tail(LIQUIDITY_LOOKBACK_DAYS)

            if hist.empty or len(hist) < 5:
                continue

            avg_volume_eur = (hist["Volume"] * hist["Close"]).mean()
            avg_spread = ((hist["High"] - hist["Low"]) / hist["Close"]).mean()
            last_close = float(hist["Close"].iloc[-1])

            # Get market cap separately (no choice)
            try:
                info = yf.Ticker(ticker).info
                market_cap = info.get("marketCap", 0) or 0
                time.sleep(0.5)
            except Exception:
                market_cap = min_cap + 1

            if avg_volume_eur < min_vol:
                continue
            if market_cap < min_cap:
                continue
            if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
                continue

            passed.append({
                "ticker": ticker,
                "metrics": {
                    "avg_volume_eur": avg_volume_eur,
                    "avg_spread": avg_spread,
                    "market_cap": market_cap,
                    "last_close": last_close,
                }
            })

        except Exception as e:
            logger.debug(f"{ticker}: {e}")
            continue

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
