"""
universe.py -- Fetch OHLCV data and filter liquid stocks.
EU tickers (.PA / .AS / .MI): Alpha Vantage (max 20 calls/run, 12s delay).
US / HK tickers            : Direct Yahoo Finance API with browser headers.
"""

import logging
import time
import os
import requests
import pandas as pd
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)
AV_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
MAX_AV_CALLS_PER_RUN = 20

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}


def _market_of(ticker: str) -> str:
    if ticker.endswith((".PA", ".AS", ".MI")):
        return "EU"
    if ticker.endswith(".HK"):
        return "HK"
    return "US"


def _convert_av(ticker: str) -> str:
    return ticker.replace(".PA", ".PAR").replace(".AS", ".AMS").replace(".MI", ".MIL")


def _fetch_av(ticker: str) -> dict | None:
    av_ticker = _convert_av(ticker)
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
            logger.warning(f"AV no data for {ticker}: {list(data.keys())}")
            return None
        dates   = sorted(ts.keys(), reverse=True)[:250]
        closes  = [float(ts[d]["4. close"])  for d in dates]
        highs   = [float(ts[d]["2. high"])   for d in dates]
        lows    = [float(ts[d]["3. low"])    for d in dates]
        volumes = [float(ts[d]["5. volume"]) for d in dates]
        return {"closes": closes, "highs": highs, "lows": lows, "volumes": volumes}
    except Exception as e:
        logger.warning(f"AV fetch error {ticker}: {e}")
        return None


def _fetch_yf_direct(ticker: str) -> dict | None:
    """Fetch daily OHLCV directly from Yahoo Finance API with browser headers."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": "3mo"}
        r = requests.get(url, headers=YF_HEADERS, params=params, timeout=10)
        if r.status_code != 200:
            logger.debug(f"{ticker}: YF status {r.status_code}")
            return None
        data = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        quote   = result[0]["indicators"]["quote"][0]
        closes  = [c for c in quote.get("close",  []) if c is not None]
        highs   = [h for h in quote.get("high",   []) if h is not None]
        lows    = [l for l in quote.get("low",    []) if l is not None]
        volumes = [v for v in quote.get("volume", []) if v is not None]
        if len(closes) < 5:
            return None
        # Reverse to newest-first
        return {
            "closes":  closes[::-1],
            "highs":   highs[::-1],
            "lows":    lows[::-1],
            "volumes": volumes[::-1],
        }
    except Exception as e:
        logger.warning(f"{ticker}: direct YF fetch error - {e}")
        return None


def _fetch_us_hk(tickers: list) -> dict:
    """Fetch US and HK tickers one by one using direct Yahoo Finance API."""
    result = {}
    for ticker in tickers:
        hist = _fetch_yf_direct(ticker)
        if hist:
            result[ticker] = hist
        time.sleep(0.5)
    logger.info(f"yfinance direct: {len(result)}/{len(tickers)} US/HK tickers fetched")
    return result


def get_liquid_universe(regime: str) -> list:
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")

    min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
    min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

    eu_tickers    = [t for t in FULL_UNIVERSE if _market_of(t) == "EU"]
    us_hk_tickers = [t for t in FULL_UNIVERSE if _market_of(t) != "EU"]

    eu_batch = eu_tickers[:MAX_AV_CALLS_PER_RUN]
    if len(eu_tickers) > MAX_AV_CALLS_PER_RUN:
        logger.warning(f"AV quota: only fetching first {MAX_AV_CALLS_PER_RUN}/{len(eu_tickers)} EU tickers")

    # Fetch EU via Alpha Vantage
    eu_hist = {}
    for ticker in eu_batch:
        hist = _fetch_av(ticker)
        if hist:
            eu_hist[ticker] = hist
        time.sleep(12)

    # Fetch US/HK via direct Yahoo Finance API
    yf_hist = _fetch_us_hk(us_hk_tickers)

    # Merge and apply liquidity filters
    all_hist = {**eu_hist, **yf_hist}
    passed   = []

    for ticker, hist in all_hist.items():
        try:
            closes  = hist["closes"]
            highs   = hist["highs"]
            lows    = hist["lows"]
            volumes = hist["volumes"]

            if len(closes) < 5:
                continue

            last_close     = closes[0]
            avg_volume_eur = sum(v * c for v, c in zip(volumes, closes)) / len(closes)
            avg_spread     = sum((h - l) / c for h, l, c in zip(highs, lows, closes)) / len(closes)

            if ticker.endswith(".HK"):
                avg_volume_eur *= 0.12

            if avg_volume_eur < min_vol:
                continue
            if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
                continue

            passed.append({
                "ticker": ticker,
                "hist":   hist,
                "metrics": {
                    "avg_volume_eur": avg_volume_eur,
                    "avg_spread":     avg_spread,
                    "market_cap":     min_cap + 1,
                    "last_close":     last_close,
                }
            })
            logger.info(f"{ticker}: OK (vol={avg_volume_eur:.0f} EUR)")

        except Exception as e:
            logger.warning(f"{ticker} liquidity check error: {e}")
            continue

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
