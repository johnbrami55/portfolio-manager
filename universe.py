import logging
import time
import yfinance as yf
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)
FETCH_DELAY_SECONDS = 2.0

def _check_liquidity(ticker, regime):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=f"{LIQUIDITY_LOOKBACK_DAYS + 5}d")
        if hist.empty or len(hist) < 5:
            return False, {}
        hist = hist.tail(LIQUIDITY_LOOKBACK_DAYS)
        avg_volume_eur = (hist["Volume"] * hist["Close"]).mean()
        avg_spread = ((hist["High"] - hist["Low"]) / hist["Close"]).mean()
        info = stock.info
        market_cap = info.get("marketCap", 0) or 0
        last_close = float(hist["Close"].iloc[-1])
        min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
        min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR
        metrics = {"avg_volume_eur": avg_volume_eur, "avg_spread": avg_spread, "market_cap": market_cap, "last_close": last_close}
        if avg_volume_eur < min_vol:
            return False, metrics
        if market_cap < min_cap:
            return False, metrics
        if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
            return False, metrics
        return True, metrics
    except Exception as e:
        logger.warning(f"{ticker}: liquidity check error - {e}")
        return False, {}

def get_liquid_universe(regime):
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")
    passed = []
    for i, ticker in enumerate(FULL_UNIVERSE):
        ok, metrics = _check_liquidity(ticker, regime)
        if ok:
            passed.append({"ticker": ticker, "metrics": metrics})
        if i < len(FULL_UNIVERSE) - 1:
            time.sleep(FETCH_DELAY_SECONDS)
    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
