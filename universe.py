import logging
import time
import os
import requests
import yfinance as yf
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)

def get_session():
    cookie = os.environ.get("YAHOO_COOKIE", "")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie,
    })
    return session

def get_liquid_universe(regime):
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")
    passed = []
    min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
    min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR
    session = get_session()

    for ticker in FULL_UNIVERSE:
        try:
            tk = yf.Ticker(ticker, session=session)
            hist = tk.history(period=f"{LIQUIDITY_LOOKBACK_DAYS + 5}d")
            if hist.empty or len(hist) < 5:
                continue
            hist = hist.tail(LIQUIDITY_LOOKBACK_DAYS)
            avg_volume_eur = (hist["Volume"] * hist["Close"]).mean()
            avg_spread = ((hist["High"] - hist["Low"]) / hist["Close"]).mean()
            last_close = float(hist["Close"].iloc[-1])
            info = tk.info
            market_cap = info.get("marketCap", 0) or 0
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
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"{ticker}: {e}")
            time.sleep(1)
            continue

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
