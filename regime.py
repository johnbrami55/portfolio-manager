import logging
import os
import requests

from config import CAC40_TICKER, STOXX600_TICKER, MA_SHORT, MA_LONG

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "yahoo-finance15.p.rapidapi.com"

def get_headers():
    return {
        "x-rapidapi-key": os.environ.get("RAPIDAPI_KEY", ""),
        "x-rapidapi-host": RAPIDAPI_HOST,
    }

def fetch_closes(ticker):
    url = f"https://{RAPIDAPI_HOST}/api/v1/markets/stock/history"
    params = {"symbol": ticker, "interval": "1d", "diffandsplits": "false"}
    try:
        r = requests.get(url, headers=get_headers(), params=params, timeout=10)
        data = r.json()
        if "body" not in data:
            return []
        return [v["close"] for v in data["body"].values() if "close" in v]
    except Exception as e:
        logger.error(f"Failed to fetch {ticker}: {e}")
        return []

def compute_regime(closes):
    if len(closes) < MA_LONG:
        logger.warning("Insufficient data — defaulting to NEUTRAL")
        return {"regime": "NEUTRAL", "ma50": None, "ma200": None, "last_close": None, "detail": "Insufficient data"}

    ma50  = sum(closes[-MA_SHORT:]) / MA_SHORT
    ma200 = sum(closes[-MA_LONG:])  / MA_LONG
    last  = closes[-1]

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
