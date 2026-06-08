import logging

import yfinance as yf

from config import CAC40_TICKER, STOXX600_TICKER, MA_SHORT, MA_LONG

logger = logging.getLogger(__name__)


def fetch_closes(ticker):
    yf_ticker = "MC.PA" if ticker == CAC40_TICKER else "ASML.AS"
    try:
        df = yf.download(yf_ticker, period="1y", progress=False, auto_adjust=True)
        if df.empty:
            logger.error(f"No data for {yf_ticker}")
            return []
        closes = df["Close"].dropna().iloc[::-1].tolist()
        return [float(c) for c in closes]
    except Exception as e:
        logger.error(f"Failed to fetch {ticker}: {e}")
        return []


def compute_regime(closes):
    if len(closes) < MA_LONG:
        logger.warning("Insufficient data – defaulting to NEUTRAL")
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
