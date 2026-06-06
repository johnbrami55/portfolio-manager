import logging
import time
import os
import requests
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)
RAPIDAPI_HOST = "yahoo-finance15.p.rapidapi.com"

def get_headers():
    return {
        "x-rapidapi-key": os.environ.get("RAPIDAPI_KEY", ""),
        "x-rapidapi-host": RAPIDAPI_HOST,
    }

def fetch_history(ticker):
    url = f"https://{RAPIDAPI_HOST}/api/v1/markets/stock/history"
    params = {"symbol": ticker, "interval": "1d", "diffandsplits": "false"}
    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=10)
        data = r.json()
        if "body" not in data:
            return None
        body = data["body"]
        closes = [v["close"] for v in body.values() if "close" in v]
        highs = [v["high"] for v in body.values() if "high" in v]
        lows = [v["low"] for v in body.values() if "low" in v]
        vols = [v["volume"] for v in body.values() if "volume" in v]
        return {"closes": closes, "highs": highs, "lows": lows, "volumes": vols}
    except Exception as e:
        logger.warning(f"{ticker}: history error - {e}")
        return None

def fetch_info(ticker):
    url = f"https://{RAPIDAPI_HOST}/api/v1/markets/stock/quotes"
    params = {"ticker": ticker}
    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=10)
        data = r.json()
        if "body" not in data:
            return {}
        return data["body"][0] if data["body"] else {}
    except Exception as e:
        logger.warning(f"{ticker}: info error - {e}")
        return {}

def get_liquid_universe(regime):
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")
    passed = []
    min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
    min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

    for ticker in FULL_UNIVERSE:
        try:
            hist = fetch_history(ticker)
            if not hist or len(hist["closes"]) < 5:
                time.sleep(0.3)
                continue
            n = LIQUIDITY_LOOKBACK_DAYS
            closes = hist["closes"][-n:]
            highs = hist["highs"][-n:]
            lows = hist["lows"][-n:]
            volumes = hist["volumes"][-n:]
            last_close = closes[-1]
            avg_volume_eur = sum(v * c for v, c in zip(volumes, closes)) / len(closes)
            avg_spread = sum((h - l) / c for h, l, c in zip(highs, lows, closes)) / len(closes)
            info = fetch_info(ticker)
            market_cap = float(info.get("marketCap", 0) or 0)
            if avg_volume_eur < min_vol:
                time.sleep(0.3)
                continue
            if market_cap < min_cap:
                time.sleep(0.3)
                continue
            if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
                time.sleep(0.3)
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
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"{ticker}: {e}")
            time.sleep(0.5)
            continue

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
