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
        rs = gains / losses
