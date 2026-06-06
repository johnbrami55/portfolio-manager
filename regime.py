import logging
import time
import requests
import yfinance as yf
import pandas as pd
import os
from config import CAC40_TICKER, STOXX600_TICKER, MA_SHORT, MA_LONG

logger = logging.getLogger(__name__)

def get_headers():
    cookie = os.environ.get("YAHOO_COOKIE", "")
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie,
    }

def fetch_closes(ticker, period="1y"):
    try:
        session = requests.Session()
        session.headers.update(get_headers())
        tk = yf.Ticker(ticker, session=session)
        hist = tk.history(period=period)
        if hist.empty:
            raise ValueError("empty")
        return hist["Close"].dropna()
    except Exception as e:
        logger.error(f"Failed to fetch {ticker}: {e}")
        return pd.Series(dtype=float)

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
    cac_closes = fetch_closes(CAC40_TICKER)
    time.sleep(1)
    stoxx_closes = fetch_closes(STOXX600_TICKER)
    cac_result = compute_regime(cac_closes)
    stoxx_result = compute_regime(stoxx_closes)
    regime = cac_result["regime"]
    logger.info(f"Regime: {regime} | {cac_result['detail']}")
    return {"regime": regime, "cac40": cac_result, "stoxx600": stoxx_result}
