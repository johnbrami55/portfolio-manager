import logging
import time

import requests

from config import CAC40_TICKER, STOXX600_TICKER, MA_SHORT, MA_LONG

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}


def fetch_closes(ticker):
    yf_ticker = "MC.PA" if ticker == CAC40_TICKER else "ASML.AS"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
    params = {"range": "1y", "interval": "1d"}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, params=params, timeout=15)
            r.raise_for_status()
            result = r.json()["chart"]["result"][0]
            closes = result["indicators"]["quote"][0]["close"]
            closes = [float(c) for c in reversed(closes) if c is not None]
            if not closes:
                raise ValueError("empty close list")
            return closes
        except Exception as e:
            logger.error(f"Failed to fetch {yf_ticker} (attempt {attempt + 1}): {e}")
            time.sleep(5 * (attempt + 1))
    return []


def compute_regime(closes):
    if len(closes) < MA_LONG:
        logger.warning("Insufficient data - defaulting to NEUTRAL")
        return {"regime": "NEUTRAL", "ma50": None, "ma200": None, "last_close": None, "detail": "Insufficient data"}

    ma50  = sum(closes[:MA_SHORT]) / MA_SHORT
    ma200 = sum(closes[:MA_LONG])  / MA_LONG
    last  = closes[0]

    if ma50 > ma200 and last > ma50:
        regime, detail = "BULL", f"MA50({ma50:.1f}) > MA200({ma200:.1f}) AND price > MA50"
    elif ma50 > ma200:
        regime, detail = "NEUTRAL", f"MA50({ma50:.1f}) > MA200({ma200:.1f}) BUT price < MA50"
    else:
        regime, detail = "BEAR", f"MA50({ma50:.1f}) < MA200({ma200:.1f})"

    return {"regime": regime, "ma50": ma50, "ma200": ma200, "last_close": last, "detail": detail}


def detect_regime():
    logger.info("Detecting market regime...")
    cac_closes   = fetch_closes(CAC40_TICKER)
    stoxx_closes = fetch_closes(STOXX600_TICKER)
    cac_result   = compute_regime(cac_closes)
    stoxx_result = compute_regime(stoxx_closes)
    regime       = cac_result["regime"]
    logger.info(f"Regime: {regime} | {cac_result['detail']}")
    return {"regime": regime, "cac40": cac_result, "stoxx600": stoxx_result}
