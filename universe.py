"""
universe.py -- Fetch OHLCV data and filter liquid stocks.
EU tickers (.PA / .AS / .MI): Alpha Vantage (max 20 calls/run, 12s delay).
US / HK tickers            : yfinance batch download (free, no rate limit).
"""

import logging
import time
import os
import requests
import pandas as pd
import yfinance as yf
from config import (
    FULL_UNIVERSE,
    LIQUIDITY_MIN_VOLUME_EUR, LIQUIDITY_MIN_MARKET_CAP_EUR,
    LIQUIDITY_MAX_SPREAD_PCT, LIQUIDITY_LOOKBACK_DAYS,
    BEAR_MIN_VOLUME_EUR, BEAR_MIN_MARKET_CAP_EUR,
)

logger = logging.getLogger(__name__)
AV_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

# Alpha Vantage: max calls per run (free tier = 25/day, 3 runs/day => ~8/run safe)
# Set higher if you have a paid key.
MAX_AV_CALLS_PER_RUN = 20


# ── Helpers ───────────────────────────────────────────────────────────────────

def _market_of(ticker: str) -> str:
    """Return EU, US, or HK based on ticker suffix."""
    if ticker.endswith((".PA", ".AS", ".MI")):
        return "EU"
    if ticker.endswith(".HK"):
        return "HK"
    return "US"


def _convert_av(ticker: str) -> str:
    """Convert yfinance suffix to Alpha Vantage format."""
    return ticker.replace(".PA", ".PAR").replace(".AS", ".AMS").replace(".MI", ".MIL")


# ── Alpha Vantage fetch (EU) ──────────────────────────────────────────────────

def _fetch_av(ticker: str) -> dict | None:
    """Fetch daily OHLCV via Alpha Vantage. Returns newest-first dict or None."""
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


# ── yfinance batch fetch (US / HK) ───────────────────────────────────────────

def _parse_yf_raw(raw: pd.DataFrame, tickers: list) -> dict:
    """
    Parse a yfinance download result.
    Multi-ticker download  -> columns are MultiIndex (metric, ticker).
    Single-ticker download -> columns are flat ['Open','High','Low','Close','Volume'].
    Returns {ticker: {closes, highs, lows, volumes}} newest-first.
    """
    result = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            if multi:
                df = pd.DataFrame({
                    "Close":  raw["Close"][t],
                    "High":   raw["High"][t],
                    "Low":    raw["Low"][t],
                    "Volume": raw["Volume"][t],
                }).dropna()
            else:
                df = raw[["Close", "High", "Low", "Volume"]].dropna()
            if len(df) < 5:
                continue
            # yfinance returns oldest-first; reverse to newest-first
            result[t] = {
                "closes":  df["Close"].iloc[::-1].tolist(),
                "highs":   df["High"].iloc[::-1].tolist(),
                "lows":    df["Low"].iloc[::-1].tolist(),
                "volumes": df["Volume"].iloc[::-1].tolist(),
            }
        except Exception as e:
            logger.warning(f"yfinance parse {t}: {e}")
    return result


def _fetch_yf_batch(tickers: list) -> dict:
    """Download US/HK tickers in one yfinance batch call. Retries once on failure."""
    if not tickers:
        return {}
    download_arg = tickers if len(tickers) > 1 else tickers[0]
    for attempt in range(2):
        try:
            raw = yf.download(
                download_arg,
                period="3mo",
                auto_adjust=True,
                progress=False,
            )
            if raw.empty:
                raise ValueError("empty DataFrame")
            return _parse_yf_raw(raw, tickers)
        except Exception as e:
            if attempt == 0:
                logger.warning(f"yfinance batch attempt 1 failed ({e}), retrying in 5s...")
                time.sleep(5)
            else:
                logger.error(f"yfinance batch failed after retry: {e}")
    return {}


# ── Main entry point ──────────────────────────────────────────────────────────

def get_liquid_universe(regime: str) -> list:
    logger.info(f"Scanning {len(FULL_UNIVERSE)} tickers (regime={regime})...")

    min_vol = BEAR_MIN_VOLUME_EUR if regime == "BEAR" else LIQUIDITY_MIN_VOLUME_EUR
    min_cap = BEAR_MIN_MARKET_CAP_EUR if regime == "BEAR" else LIQUIDITY_MIN_MARKET_CAP_EUR

    # Partition universe by market
    eu_tickers   = [t for t in FULL_UNIVERSE if _market_of(t) == "EU"]
    us_hk_tickers = [t for t in FULL_UNIVERSE if _market_of(t) != "EU"]

    # Limit Alpha Vantage calls to avoid daily quota exhaustion
    eu_batch = eu_tickers[:MAX_AV_CALLS_PER_RUN]
    if len(eu_tickers) > MAX_AV_CALLS_PER_RUN:
        logger.warning(
            f"AV quota: only fetching first {MAX_AV_CALLS_PER_RUN}/{len(eu_tickers)} EU tickers"
        )

    # ── Fetch EU via Alpha Vantage ────────────────────────────────────────────
    eu_hist: dict[str, dict] = {}
    for ticker in eu_batch:
        hist = _fetch_av(ticker)
        if hist:
            eu_hist[ticker] = hist
        time.sleep(12)  # 5 req/min max on free tier

    # ── Fetch US/HK via yfinance ──────────────────────────────────────────────
    yf_hist = _fetch_yf_batch(us_hk_tickers)
    logger.info(f"yfinance: {len(yf_hist)}/{len(us_hk_tickers)} US/HK tickers fetched")

    # ── Merge and apply liquidity filters ────────────────────────────────────
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

            # HK prices are in HKD; convert volume*price to rough EUR (1 HKD ~ 0.12 EUR)
            if ticker.endswith(".HK"):
                avg_volume_eur *= 0.12

            if avg_volume_eur < min_vol:
                logger.debug(f"{ticker}: vol too low ({avg_volume_eur:.0f} EUR)")
                continue
            if avg_spread > LIQUIDITY_MAX_SPREAD_PCT:
                logger.debug(f"{ticker}: spread too wide ({avg_spread:.3f})")
                continue

            passed.append({
                "ticker": ticker,
                "hist":   hist,
                "metrics": {
                    "avg_volume_eur": avg_volume_eur,
                    "avg_spread":     avg_spread,
                    "market_cap":     min_cap + 1,  # no free market cap data
                    "last_close":     last_close,
                }
            })
            logger.info(f"{ticker}: OK (vol={avg_volume_eur:.0f} EUR)")

        except Exception as e:
            logger.warning(f"{ticker} liquidity check error: {e}")
            continue

    logger.info(f"Liquid universe: {len(passed)}/{len(FULL_UNIVERSE)} tickers passed")
    return passed
