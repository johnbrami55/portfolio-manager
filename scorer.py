import logging
import numpy as np
from config import (
    SCORE_TECH_TREND_MAX, SCORE_TECH_RSI_MAX, SCORE_TECH_VOLUME_MAX,
    SCORE_TECH_MACD_MAX, SCORE_TECH_MOMENTUM_MAX,
    SCORE_TECH_BOLLINGER_MAX, SCORE_TECH_STOCHRSI_MAX,
    SCORE_FUND_EPS_REVISIONS_MAX, SCORE_FUND_VALUATION_MAX,
    SCORE_FUND_BALANCE_SHEET_MAX, SCORE_FUND_GROWTH_MAX,
    RSI_THRESHOLDS, VOLUME_HIGH_MULT, VOLUME_MED_MULT,
    BULL_BETA_BONUS_THRESHOLD, BEAR_BETA_BONUS_THRESHOLD, REGIME_BONUS_PTS,
)

logger = logging.getLogger(__name__)

BEAR_TREND_FACTOR    = 0.7
BEAR_MOMENTUM_FACTOR = 0.7


def _rsi_series(closes, period=14):
    reversed_closes = list(reversed(closes))
    rsi_vals = []
    for i in range(period, len(reversed_closes)):
        window = reversed_closes[i - period:i + 1]
        deltas = [window[j] - window[j - 1] for j in range(1, len(window))]
        gains  = sum(d for d in deltas if d > 0) / period
        losses = sum(-d for d in deltas if d < 0) / period
        rs = gains / losses if losses != 0 else 100
        rsi_vals.append(100 - 100 / (1 + rs))
    return list(reversed(rsi_vals))


def score_trend(closes):
    """
    Score trend using available history.
    - 200+ closes : full MA20/MA50/MA200 (3 signals × step)
    - 100+ closes : MA20/MA50/MA100 fallback — max = SCORE_TECH_TREND_MAX * 0.8
    - <100 closes : MA20/MA50 only — max = SCORE_TECH_TREND_MAX * 0.5
    """
    last = closes[0]
    pts, signals = 0.0, []

    if len(closes) >= 200:
        ma20  = sum(closes[:20])  / 20
        ma50  = sum(closes[:50])  / 50
        ma200 = sum(closes[:200]) / 200
        step  = SCORE_TECH_TREND_MAX / 3
        if last > ma20:  pts += step; signals.append(f"Price > MA20 ({ma20:.2f})")
        if ma20 > ma50:  pts += step; signals.append(f"MA20 > MA50 ({ma50:.2f})")
        if ma50 > ma200: pts += step; signals.append(f"MA50 > MA200 ({ma200:.2f})")

    elif len(closes) >= 100:
        ma20  = sum(closes[:20])  / 20
        ma50  = sum(closes[:50])  / 50
        ma100 = sum(closes[:100]) / 100
        step  = SCORE_TECH_TREND_MAX * 0.8 / 3
        if last > ma20:   pts += step; signals.append(f"Price > MA20 ({ma20:.2f})")
        if ma20 > ma50:   pts += step; signals.append(f"MA20 > MA50 ({ma50:.2f})")
        if ma50 > ma100:  pts += step; signals.append(f"MA50 > MA100 ({ma100:.2f})")
        signals.append("Trend: 100d history (MA200 unavailable)")

    elif len(closes) >= 50:
        ma20 = sum(closes[:20]) / 20
        ma50 = sum(closes[:50]) / 50
        step = SCORE_TECH_TREND_MAX * 0.5 / 2
        if last > ma20:  pts += step; signals.append(f"Price > MA20 ({ma20:.2f})")
        if ma20 > ma50:  pts += step; signals.append(f"MA20 > MA50 ({ma50:.2f})")
        signals.append("Trend: 50d history (MA200 unavailable)")

    else:
        signals.append("Insufficient history")

    return pts, signals


def score_rsi(closes, regime):
    if len(closes) < 15:
        return 0.0, ["Insufficient data"]
    deltas = [closes[i] - closes[i + 1] for i in range(14)]
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
    """
    Volume scoring — lowered thresholds to avoid systematic 0 in current market.
    HIGH  : >= VOLUME_HIGH_MULT (1.5x) → full points
    MED   : >= VOLUME_MED_MULT  (1.2x) → 60%
    LOW   : >= 0.7x             → 30%  (NEW — was 0 before)
    WEAK  : < 0.7x              → 0
    """
    if len(volumes) < 20:
        return 0.0, ["No volume data"]
    avg_vol = sum(volumes[1:21]) / 20
    cur_vol = volumes[0]
    ratio   = cur_vol / avg_vol if avg_vol > 0 else 0
    if ratio >= VOLUME_HIGH_MULT:
        return SCORE_TECH_VOLUME_MAX, [f"Volume {ratio:.1f}x avg"]
    elif ratio >= VOLUME_MED_MULT:
        return SCORE_TECH_VOLUME_MAX * 0.6, [f"Volume {ratio:.1f}x avg"]
    elif ratio >= 0.7:
        return SCORE_TECH_VOLUME_MAX * 0.3, [f"Volume {ratio:.1f}x avg (normal)"]
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
    signal = ema(closes[:9], 9)
    if macd > 0 and macd > signal:
        return SCORE_TECH_MACD_MAX, ["MACD bullish crossover"]
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


def score_bollinger(closes, regime):
    if len(closes) < 21:
        return 0.0, ["Insufficient data"]
    window = closes[:20]
    sma    = sum(window) / 20
    std    = (sum((x - sma) ** 2 for x in window) / 20) ** 0.5
    if std == 0:
        return 0.0, ["Bollinger std=0"]
    upper  = sma + 2 * std
    lower  = sma - 2 * std
    price  = closes[0]
    pct_b  = (price - lower) / (upper - lower)

    if pct_b > 1.0:
        if regime == "BULL":
            return SCORE_TECH_BOLLINGER_MAX, [f"Bollinger breakout up (%B={pct_b:.2f})"]
        elif regime == "NEUTRAL":
            return SCORE_TECH_BOLLINGER_MAX * 0.5, [f"Bollinger near upper (%B={pct_b:.2f})"]
        else:
            return 0.0, [f"Bollinger overbought in BEAR (%B={pct_b:.2f})"]
    elif pct_b >= 0.6:
        return SCORE_TECH_BOLLINGER_MAX * 0.7, [f"Bollinger upper zone (%B={pct_b:.2f})"]
    elif pct_b >= 0.4:
        return SCORE_TECH_BOLLINGER_MAX * 0.3, [f"Bollinger mid (%B={pct_b:.2f})"]
    elif pct_b >= 0.1:
        if regime == "BEAR":
            return SCORE_TECH_BOLLINGER_MAX * 0.6, [f"Bollinger near lower/defensive (%B={pct_b:.2f})"]
        else:
            return SCORE_TECH_BOLLINGER_MAX * 0.4, [f"Bollinger near lower/bounce (%B={pct_b:.2f})"]
    else:
        return 0.0, [f"Bollinger below lower band (%B={pct_b:.2f})"]


def score_stoch_rsi(closes, regime):
    if len(closes) < 30:
        return 0.0, ["Insufficient data"]

    rsi_vals = _rsi_series(closes, period=14)
    if len(rsi_vals) < 14:
        return 0.0, ["Insufficient RSI history"]

    rsi_window = rsi_vals[:14]
    rsi_min = min(rsi_window)
    rsi_max = max(rsi_window)
    if rsi_max == rsi_min:
        return 0.0, ["StochRSI flat"]

    k = (rsi_vals[0] - rsi_min) / (rsi_max - rsi_min) * 100
    k_series = [(rsi_vals[i] - rsi_min) / (rsi_max - rsi_min) * 100 for i in range(min(3, len(rsi_vals)))]
    d = sum(k_series) / len(k_series)

    if k < 20 and k > d:
        return SCORE_TECH_STOCHRSI_MAX, [f"StochRSI oversold reversal (K={k:.0f}, D={d:.0f})"]
    elif k < 20:
        return SCORE_TECH_STOCHRSI_MAX * 0.6, [f"StochRSI oversold (K={k:.0f})"]
    elif k > 80:
        if regime == "BULL":
            return SCORE_TECH_STOCHRSI_MAX * 0.5, [f"StochRSI overbought/momentum (K={k:.0f})"]
        return 0.0, [f"StochRSI overbought (K={k:.0f})"]
    elif k >= 50:
        return SCORE_TECH_STOCHRSI_MAX * 0.8, [f"StochRSI bullish zone (K={k:.0f})"]
    else:
        return SCORE_TECH_STOCHRSI_MAX * 0.3, [f"StochRSI neutral (K={k:.0f})"]


def compute_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    tr_list = []
    for i in range(period):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i + 1]),
            abs(lows[i] - closes[i + 1]),
        )
        tr_list.append(tr)
    atr = sum(tr_list) / period
    return round(atr / closes[0], 4) if closes[0] > 0 else None


def score_universe_from_cache(liquid_items, regime, betas):
    logger.info(f"Scoring {len(liquid_items)} tickers from cache (regime={regime})...")

    universe_returns = []
    for item in liquid_items:
        hist = item.get("hist")
        if hist and len(hist.get("closes", [])) >= 63:
            closes = hist["closes"]
            universe_returns.append((closes[0] - closes[62]) / closes[62])

    scores = []
    for item in liquid_items:
        ticker = item["ticker"]
        beta   = betas.get(ticker, 1.0)
        hist   = item.get("hist")

        if not hist or len(hist.get("closes", [])) < 50:
            logger.debug(f"{ticker}: no cached data, skipping")
            continue

        closes  = hist["closes"]
        highs   = hist.get("highs", [])
        lows    = hist.get("lows", [])
        volumes = hist["volumes"]

        t1, s1 = score_trend(closes)
        t2, s2 = score_rsi(closes, regime)
        t3, s3 = score_volume(closes, volumes)
        t4, s4 = score_macd(closes)
        t5, s5 = score_momentum(closes, universe_returns)
        t6, s6 = score_bollinger(closes, regime)
        t7, s7 = score_stoch_rsi(closes, regime)

        if regime == "BEAR":
            t1 *= BEAR_TREND_FACTOR
            t5 *= BEAR_MOMENTUM_FACTOR

        tech = t1 + t2 + t3 + t4 + t5 + t6 + t7

        fund = (SCORE_FUND_EPS_REVISIONS_MAX + SCORE_FUND_VALUATION_MAX +
                SCORE_FUND_BALANCE_SHEET_MAX + SCORE_FUND_GROWTH_MAX) * 0.3

        bonus, bonus_reason = 0.0, ""
        if regime == "BULL" and beta >= BULL_BETA_BONUS_THRESHOLD:
            bonus = REGIME_BONUS_PTS
            bonus_reason = f"High-beta BULL β={beta:.2f}"
        elif regime == "BEAR" and beta < BEAR_BETA_BONUS_THRESHOLD:
            bonus = REGIME_BONUS_PTS
            bonus_reason = f"Low-beta BEAR β={beta:.2f}"

        total   = min(100.0, tech + fund + bonus)
        atr_pct = compute_atr(highs, lows, closes) if highs and lows else None

        scores.append({
            "ticker":       ticker,
            "score":        total,
            "tech_score":   tech,
            "fund_score":   fund,
            "regime_bonus": bonus,
            "bonus_reason": bonus_reason,
            "signals_tech": s1 + s2 + s3 + s4 + s5 + s6 + s7,
            "signals_fund": ["Fundamental: Alpha Vantage free tier"],
            "beta":         beta,
            "atr_pct":      atr_pct,
            "error":        None,
            "breakdown":    {
                "trend": t1, "rsi": t2, "volume": t3, "macd": t4,
                "momentum": t5, "bollinger": t6, "stoch_rsi": t7,
            },
        })

    scores.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Scored {len(scores)} tickers successfully")
    return scores
