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
AV_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

def convert_ticker(ticker):
    return ticker.replace(".PA", ".PAR").replace(".AS", ".AMS").replace(".MI", ".MIL")

def fetch_history(ticker):
    av_ticker = convert_ticker(ticker)
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": av_ticker,
                "apikey": AV_KEY,
                "outputsize": "compact",
            },
            timeout=15,
        )
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts:
            return None
        dates = sorted(ts.keys(), reverse=True)[:250]
        closes  = [float(ts[d]["4. close"])  for d in dates]
        highs   = [float(ts[d]["2. high"])   for d in dates]
        lows    = [float(ts[d]["3. low"])    for d in dates]
        volumes = [float(ts[d]["5. volume"]) for d in dates]
        return {"closes": closes, "highs": highs, "lows": lows, "volumes": volumes}
    except Exception as e:
        logger.warning(f"{ticker}: {e}")
        return None

def get_liquid_universe(regime):
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")
    passed = []
    min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
    min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

    for ticker in FULL_UNIVERSE:
        try:
            hist = fetch_history(ticker)
            if not hist or len(hist["closes"]) < 5:
                time.sleep(6)
                continue

            closes  = hist["closes"]
            highs   = hist["highs"]
            lows    = hist["lows"]
            volumes = hist["volumes"]

            last_close     = closes[0]
            avg_volume_eur = sum(v * c for v, c in zip(volumes, closes)) / len(closes)
            avg_spread     = sum((h - l) / c for h, l, c in zip(highs, lows, closes)) / len(closes)
            market_cap     = min_cap + 1  # Alpha Vantage gratuit n'a pas market cap

            if avg_volume_eur < min_vol:
                time.sleep(6); continue
            if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
                time.sleep(6); continue

            passed.append({
                "ticker": ticker,
                "hist":   hist,
                "metrics": {
                    "avg_volume_eur": avg_volume_eur,
                    "avg_spread":     avg_spread,
                    "market_cap":     market_cap,
                    "last_close":     last_close,
                }
            })
            logger.info(f"{ticker}: OK (vol={avg_volume_eur:.0f} EUR)")
            time.sleep(6)

        except Exception as e:
            logger.warning(f"{ticker}: {e}")
            time.sleep(6)
            continue

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
