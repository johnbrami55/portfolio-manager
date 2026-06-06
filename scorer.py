import logging
import os
import time
import requests
import numpy as np
from config import (
    SCORE_TECH_TREND_MAX, SCORE_TECH_RSI_MAX, SCORE_TECH_VOLUME_MAX,
    SCORE_TECH_MACD_MAX, SCORE_TECH_MOMENTUM_MAX,
    SCORE_FUND_EPS_REVISIONS_MAX, SCORE_FUND_VALUATION_MAX,
    SCORE_FUND_BALANCE_SHEET_MAX, SCORE_FUND_GROWTH_MAX,
    RSI_THRESHOLDS, VOLUME_HIGH_MULT, VOLUME_MED_MULT,
    BULL_BETA_BONUS_THRESHOLD, BEAR_BETA_BONUS_THRESHOLD, REGIME_BONUS_PTS,
)

logger = logging.getLogger(__name__)
AV_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

def convert_ticker(ticker):
    return ticker.replace(".PA", ".PAR").replace(".AS", ".AMS").replace(".MI", ".MIL")

def fetch_daily(ticker):
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "TIME_SERIES_DAILY", "symbol": convert_ticker(ticker),
                    "apikey": AV_KEY, "outputsize": "full"},
            timeout=15,
        )
        ts = r.json().get("Time Series (Daily)", {})
        dates = sorted(ts.keys(), reverse=True)[:250]
        return {
            "closes":  [float(ts[d]["4. close"])  for d in dates],
            "highs":   [float(ts[d]["2. high"])   for d in dates],
            "lows":    [float(ts[d]["3. low"])    for d in dates],
            "volumes": [float(ts[d]["5. volume"]) for d in dates],
        }
    except Exception as e:
        logger.debug(f"{ticker} fetch_daily: {e}")
        return None

def score_trend(closes):
    if len(closes) < 200:
        return 0.0, ["Insufficient history"]
    ma20  = sum(closes[:20])  / 20
    ma50  = sum(closes[:50])  / 50
    ma200 = sum(closes[:200]) / 200
    last  = closes[0]
    pts, signals = 0.0, []
    if last > ma20:  pts += 5; signals.append(f"Price > MA20 ({ma20:.2f})")
    if ma20 > ma50:  pts += 5; signals.append(f"MA20 > MA50 ({ma50:.2f})")
    if ma50 > ma200: pts += 5; signals.append(f"MA50 > MA200 ({ma200:.2f})")
    return pts, signals

def score_rsi(closes, regime):
    if len(closes) < 15:
        return 0.0, ["Insufficient data"]
    deltas = [closes[i] - closes[i+1] for i in range(14)]
    gains  = sum(d for d in deltas if d > 0) / 14
    losses = sum(-d for d in deltas if d < 0) / 14
    rs  = gains / losses if losses != 0 else 100
    rsi = 100 - 100 / (1 + rs)
    th  = RSI_THRESHOLDS[regime]
    if th["min"] <= rsi <= th["max"]:
        return SCORE_TECH_RSI_MAX, [f"RSI {rsi:.1f} ideal"]
    elif rsi < th["oversold"]:
        return SCORE_TECH_RSI_MAX * 0.8, [f"RSI {rsi:.1f} oversold bonus"]
    elif rsi > th["overbought"]:
        return 0.0, [f"RSI {rsi:.1f} overbought penalty"]
    else:
        return SCORE_TECH_RSI_MAX * 0.4, [f"RSI {rsi:.1f} partial"]

def score_volume(closes, volumes):
    if len(volumes) < 20:
        return 0.0, ["No volume data"]
    avg_vol = sum(volumes[1:21]) / 20
    cur_vol = volumes[0]
    ratio   = cur_vol / avg_vol if avg_vol > 0 else 0
    if ratio >= VOLUME_HIGH_MULT:
        return SCORE_TECH_VOLUME_MAX, [f"Volume {ratio:.1f}x avg"]
    elif ratio >= VOLUME_MED_MULT:
        return SCORE_TECH_VOLUME_MAX * 0.6, [f"Volume {ratio:.1f}x avg"]
    return 0.0, [f"Volume {ratio:.1f}x avg (weak)"]

def score_macd(closes):
    if len(closes) < 26:
        return 0.0, ["Insufficient data"]
    def ema(data, span):
        k = 2 / (span + 1)
        e = data[-1]
        for p in reversed(data[:-1]):
            e = p * k + e * (1 - k)
        return e
    macd   = ema(closes[:12], 12) - ema(closes[:26], 26)
    signal = ema(closes[:9],  9)
    if macd > 0 and macd > signal:
        return SCORE_TECH_MACD_MAX, ["MACD bullish"]
    elif macd > 0:
        return SCORE_TECH_MACD_MAX * 0.5, ["MACD above zero"]
    return 0.0, ["MACD bearish"]

def score_momentum(closes, universe_returns):
    if len(closes) < 63:
        return 0.0, ["Insufficient history"]
    ret_3m = (closes[0] - closes[62]) / closes[62]
    if not universe_returns:
        return SCORE_TECH_MOMENTUM_MAX * 0.5, [f"3M {ret_3m:.1%}"]
    q75 = sorted(universe_returns)[int(len(universe_returns) * 0.75)]
    med = sorted(universe_returns)[int(len(universe_returns) * 0.5)]
    if ret_3m >= q75:
        return SCORE_TECH_MOMENTUM_MAX, [f"3M {ret_3m:.1%} top quartile"]
    elif ret_3m >= med:
        return SCORE_TECH_MOMENTUM_MAX * 0.5, [f"3M {ret_3m:.1%} above median"]
    return 0.0, [f"3M {ret_3m:.1%} below median"]

def score_ticker(ticker, regime, universe_returns, beta):
    result = {
        "ticker": ticker, "score": 0.0, "tech_score": 0.0,
        "fund_score": 0.0, "regime_bonus": 0.0,
        "signals_tech": [], "signals_fund": [], "beta": beta,
        "error": None, "breakdown": {},
    }
    try:
        hist = fetch_daily(ticker)
        time.sleep(12)
        if not hist or len(hist["closes"]) < 50:
            result["error"] = "Insufficient data"
            return result

        closes  = hist["closes"]
        volumes = hist["volumes"]

        t1, s1 = score_trend(closes)
        t2, s2 = score_rsi(closes, regime)
        t3, s3 = score_volume(closes, volumes)
        t4, s4 = score_macd(closes)
        t5, s5 = score_momentum(closes, universe_returns)
        tech = t1 + t2 + t3 + t4 + t5

        # Fundamental: basic scoring from price data only
        f1 = SCORE_FUND_EPS_REVISIONS_MAX * 0.3
        f2 = SCORE_FUND_VALUATION_MAX * 0.3
        f3 = SCORE_FUND_BALANCE_SHEET_MAX * 0.5
        f4 = SCORE_FUND_GROWTH_MAX * 0.3
        fund = f1 + f2 + f3 + f4
        fund_sigs = ["Fundamental data limited (Alpha Vantage free tier)"]

        bonus, bonus_reason = 0.0, ""
        if regime == "BULL" and beta >= BULL_BETA_BONUS_THRESHOLD:
            bonus = REGIME_BONUS_PTS
            bonus_reason = f"High-beta in BULL (β={beta:.2f})"
        elif regime == "BEAR" and beta < BEAR_BETA_BONUS_THRESHOLD:
            bonus = REGIME_BONUS_PTS
            bonus_reason = f"Low-beta in BEAR (β={beta:.2f})"

        total = min(100.0, tech + fund + bonus)
        result.update({
            "score": total, "tech_score": tech, "fund_score": fund,
            "regime_bonus": bonus, "bonus_reason": bonus_reason,
            "signals_tech": s1+s2+s3+s4+s5, "signals_fund": fund_sigs,
            "breakdown": {"trend": t1, "rsi": t2, "volume": t3, "macd": t4, "momentum": t5},
        })
    except Exception as e:
        logger.error(f"Scoring failed for {ticker}: {e}")
        result["error"] = str(e)
    return result

def score_universe(liquid_tickers, regime, betas):
    logger.info(f"Scoring {len(liquid_tickers)} tickers (regime={regime})...")
    universe_returns = []
    all_data = {}

    for item in liquid_tickers:
        t = item["ticker"]
        try:
            hist = fetch_daily(t)
            time.sleep(12)
            if hist and len(hist["closes"]) >= 63:
                ret = (hist["closes"][0] - hist["closes"][62]) / hist["closes"][62]
                universe_returns.append(ret)
                all_data[t] = hist
        except Exception:
            pass

    scores = []
    for item in liquid_tickers:
        t    = item["ticker"]
        beta = betas.get(t, 1.0)
        if t in all_data:
            hist = all_data[t]
            closes  = hist["closes"]
            volumes = hist["volumes"]
            result  = {"ticker": t, "score": 0.0, "tech_score": 0.0,
                       "fund_score": 0.0, "regime_bonus": 0.0,
                       "signals_tech": [], "signals_fund": [], "beta": beta,
                       "error": None, "breakdown": {}}
            t1,s1 = score_trend(closes)
            t2,s2 = score_rsi(closes, regime)
            t3,s3 = score_volume(closes, volumes)
            t4,s4 = score_macd(closes)
            t5,s5 = score_momentum(closes, universe_returns)
            tech  = t1+t2+t3+t4+t5
            fund  = (SCORE_FUND_EPS_REVISIONS_MAX + SCORE_FUND_VALUATION_MAX +
                     SCORE_FUND_BALANCE_SHEET_MAX + SCORE_FUND_GROWTH_MAX) * 0.3
            bonus, bonus_reason = 0.0, ""
            if regime == "BULL" and beta >= BULL_BETA_BONUS_THRESHOLD:
                bonus = REGIME_BONUS_PTS; bonus_reason = f"High-beta BULL β={beta:.2f}"
            elif regime == "BEAR" and beta < BEAR_BETA_BONUS_THRESHOLD:
                bonus = REGIME_BONUS_PTS; bonus_reason = f"Low-beta BEAR β={beta:.2f}"
            total = min(100.0, tech + fund + bonus)
            result.update({
                "score": total, "tech_score": tech, "fund_score": fund,
                "regime_bonus": bonus, "bonus_reason": bonus_reason,
                "signals_tech": s1+s2+s3+s4+s5,
                "signals_fund": ["Fundamental: Alpha Vantage free tier"],
            })
            scores.append(result)

    scores.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Scored {len(scores)} tickers successfully")
    return scores
