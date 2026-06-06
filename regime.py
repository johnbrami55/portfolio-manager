import logging
import time
import yfinance as yf
import pandas as pd
from config import CAC40_TICKER, STOXX600_TICKER, MA_SHORT, MA_LONG

logger = logging.getLogger(__name__)

def compute_regime(closes):
    if len(closes) < MA_LONG:
        return {"regime": "NEUTRAL", "ma50": None, "ma200": None, "last_close": None, "detail": "Insufficient data"}
    ma50 = float(closes.rolling(MA_SHORT).mean().iloc[-1])
    ma200 = float(closes.rolling(MA_LONG).mean().iloc[-1])
    last = float(closes.iloc[-1])
    if ma50 > ma200 and last > ma50:
        regime, detail = "BULL", f"MA50({ma50:.1f}) > MA200({ma200:.1f}) AND price > MA50"
    elif ma50 > ma200:
        regime, detail = "NEUTRAL", f"MA50({ma50:.1f}) > MA200({ma200:.1f}) BUT price < MA50"
    else:
        regime, detail = "BEAR", f"MA50({ma50:.1f}) < MA200({ma200:.1f})"
    return {"regime": regime, "ma50": ma50, "ma200": ma200, "last_close": last, "detail": detail}

def detect_regime():
    logger.info("Detecting market regime...")
    try:
        data = yf.download(
            [CAC40_TICKER, STOXX600_TICKER],
            period="1y",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
        cac_closes = data[CAC40_TICKER]["Close"].dropna()
        stoxx_closes = data[STOXX600_TICKER]["Close"].dropna()
    except Exception as e:
        logger.error(f"Failed to fetch indices: {e}")
        return {"regime": "NEUTRAL", "cac40": {"regime": "NEUTRAL", "detail": "Error"}, "stoxx600": {"regime": "NEUTRAL", "detail": "Error"}}

    cac_result = compute_regime(cac_closes)
    stoxx_result = compute_regime(stoxx_closes)
    regime = cac_result["regime"]
    logger.info(f"Regime: {regime} | {cac_result['detail']}")
    return {"regime": regime, "cac40": cac_result, "stoxx600": stoxx_result}
