"""
regime.py — Market regime detection using CAC40 moving averages.
"""

import logging
import time
import yfinance as yf
import pandas as pd
from config import CAC40_TICKER, STOXX600_TICKER, MA_SHORT, MA_LONG

logger = logging.getLogger(__name__)


def fetch_index_history(ticker: str, period: str = "1y") -> pd.Series:
    try:
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data.empty:
            raise ValueError(f"No data returned for {ticker}")
        return data["Close"].dropna()
    except Exception as e:
        logger.error(f"Failed to fetch history for {ticker}: {e}")
        return pd.Series(dtype=float)


def compute_regime(closes: pd.Series) -> dict:
    if len(closes) < MA_LONG:
        logger.warning("Insufficient data — defaulting to NEUTRAL")
        return {"regime": "NEUTRAL", "ma50": None, "ma200": None, "last_close": None, "detail": "Insufficient data"}

    ma50  = float(closes.rolling(MA_SHORT).mean().iloc[-1])
    ma200 = float(closes.rolling(MA_LONG).mean().iloc[-1])
    last  = float(closes.iloc[-1])

    if ma50 > ma200 and last > ma50:
        regime = "BULL"
        detail = f"MA50({ma50:.1f}) > MA200({ma200:.1f}) AND price({last:.1f}) > MA50"
    elif ma50 > ma200:
        regime = "NEUTRAL"
        detail = f"MA50({ma50:.1f}) > MA200({ma200:.1f}) BUT price({last:.1f}) < MA50"
    else:
        regime = "BEAR"
        detail = f"MA50({ma50:.1f}) < MA200({ma200:.1f})"

    return {"regime": regime, "ma50": ma50, "ma200": ma200, "last_close": last, "detail": detail}


def detect_regime() -> dict:
    logger.info("Detecting market regime...")

    cac_closes = fetch_index_history(CAC40_TICKER)
    time.sleep(2)
    stoxx_closes = fetch_index_history(STOXX600_TICKER)

    cac_result   = compute_regime(cac_closes)
    stoxx_result = compute_regime(stoxx_closes)
    regime       = cac_result["regime"]

    logger.info(f"Regime: {regime} | CAC40: {cac_result['detail']}")

    return {"regime": regime, "cac40": cac_result, "stoxx600": stoxx_result}
