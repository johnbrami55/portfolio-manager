"""
signals.py — Buy/sell signal generation and trailing stop management.
Evaluates all open positions for sell triggers each run.
"""

import logging
import yfinance as yf
from datetime import datetime
from config import (
    REGIME_PARAMS, SCORE_DEGRADATION_CONSECUTIVE, BEAR_SELL_BETA_THRESHOLD,
    MOMENTUM_SIGNAL_MIN_GAIN_PER_RUN, MOMENTUM_SIGNAL_HISTORY_RUNS,
    MOMENTUM_SIGNAL_MAX_PTS_FROM_THRESHOLD, MOMENTUM_SIGNAL_POSITION_FACTOR,
)

logger = logging.getLogger(__name__)


def _current_price(ticker: str) -> float | None:
    """Fetch latest closing price for a ticker."""
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Could not fetch price for {ticker}: {e}")
        return None


def check_stop_loss(ticker: str, position: dict, regime: str) -> dict | None:
    """
    Check if position has hit stop-loss level.
    Returns sell signal dict or None.
    """
    price = _current_price(ticker)
    if price is None:
        return None

    entry = position.get("entry_price", price)
    stop  = position.get("stop_loss") or (entry * (1 + REGIME_PARAMS[regime]["stop_loss_pct"]))
    pnl_pct = (price - entry) / entry

    if price <= stop:
        return {
            "ticker":  ticker,
            "reason":  "stop_loss",
            "price":   price,
            "pnl_pct": pnl_pct,
            "pnl_eur": (price - entry) * position.get("nb_shares", 0),
            "shares":  position.get("nb_shares", 0),
        }
    return None


def check_take_profit(ticker: str, position: dict, regime: str) -> dict | None:
    """
    Check if position has hit take-profit.
    In BULL with trailing stop active: check trail instead of fixed level.
    Returns sell signal dict or None.
    """
    price = _current_price(ticker)
    if price is None:
        return None

    entry   = position.get("entry_price", price)
    pnl_pct = (price - entry) / entry
    params  = REGIME_PARAMS[regime]

    # Update trailing high if in BULL
    trailing_high = position.get("trailing_high", entry)
    if price > trailing_high:
        trailing_high = price
        position["trailing_high"] = trailing_high  # mutate for state persistence

    # Check trailing stop (BULL only, activates after +15%)
    trailing_trigger = params.get("trailing_stop_trigger")
    trailing_pct     = params.get("trailing_stop_pct")

    if trailing_trigger and trailing_pct:
        peak_gain = (trailing_high - entry) / entry
        if peak_gain >= trailing_trigger:
            trail_stop = trailing_high * (1 + trailing_pct)
            if price <= trail_stop:
                return {
                    "ticker":  ticker,
                    "reason":  "trailing_stop",
                    "price":   price,
                    "pnl_pct": pnl_pct,
                    "pnl_eur": (price - entry) * position.get("nb_shares", 0),
                    "shares":  position.get("nb_shares", 0),
                    "detail":  f"Peak +{peak_gain:.1%}, trail stop at {trail_stop:.2f}",
                }

    # Fixed take-profit
    tp = position.get("take_profit") or (entry * (1 + params["take_profit_pct"]))
    if price >= tp:
        return {
            "ticker":  ticker,
            "reason":  "take_profit",
            "price":   price,
            "pnl_pct": pnl_pct,
            "pnl_eur": (price - entry) * position.get("nb_shares", 0),
            "shares":  position.get("nb_shares", 0),
        }
    return None


def check_score_degradation(ticker: str, position: dict, score_history: dict, regime: str) -> dict | None:
    """
    Sell if score below threshold for SCORE_DEGRADATION_CONSECUTIVE consecutive runs.
    """
    threshold = REGIME_PARAMS[regime]["score_threshold"]
    history   = score_history.get(ticker, [])

    if len(history) < SCORE_DEGRADATION_CONSECUTIVE:
        return None

    recent_scores = [h["score"] for h in history[-SCORE_DEGRADATION_CONSECUTIVE:]]
    if all(s < threshold for s in recent_scores):
        price = _current_price(ticker) or position.get("entry_price", 0)
        entry = position.get("entry_price", price)
        return {
            "ticker":  ticker,
            "reason":  f"score_degradation (scores: {recent_scores})",
            "price":   price,
            "pnl_pct": (price - entry) / entry if entry else 0,
            "pnl_eur": (price - entry) * position.get("nb_shares", 0),
            "shares":  position.get("nb_shares", 0),
            "scores":  recent_scores,
        }
    return None


def check_regime_change_sells(positions: dict, old_regime: str, new_regime: str) -> list[dict]:
    """
    On BEAR entry: sell all positions with beta > BEAR_SELL_BETA_THRESHOLD.
    Returns list of sell signals.
    """
    sell_signals = []

    if new_regime != "BEAR":
        return sell_signals

    for ticker, pos in positions.items():
        beta  = pos.get("beta", 1.0)
        if beta > BEAR_SELL_BETA_THRESHOLD:
            price = _current_price(ticker) or pos.get("entry_price", 0)
            entry = pos.get("entry_price", price)
            sell_signals.append({
                "ticker":  ticker,
                "reason":  f"regime_change ({old_regime}→{new_regime}, beta={beta:.2f}>{BEAR_SELL_BETA_THRESHOLD})",
                "price":   price,
                "pnl_pct": (price - entry) / entry if entry else 0,
                "pnl_eur": (price - entry) * pos.get("nb_shares", 0),
                "shares":  pos.get("nb_shares", 0),
            })
            logger.info(f"Regime-change sell: {ticker} (beta={beta:.2f})")

    return sell_signals


def evaluate_sells(state: dict, regime: str, score_history: dict) -> list[dict]:
    """
    Evaluate all open positions for sell triggers.
    Returns list of sell signal dicts (may be empty).
    """
    positions = state.get("positions", {})
    sells     = []

    for ticker, pos in positions.items():
        # Stop-loss
        sig = check_stop_loss(ticker, pos, regime)
        if sig:
            logger.info(f"SELL — {ticker}: stop-loss hit at {sig['price']:.2f}")
            sells.append(sig); continue

        # Take-profit / trailing
        sig = check_take_profit(ticker, pos, regime)
        if sig:
            logger.info(f"SELL — {ticker}: {sig['reason']} at {sig['price']:.2f}")
            sells.append(sig); continue

        # Score degradation
        sig = check_score_degradation(ticker, pos, score_history, regime)
        if sig:
            logger.info(f"SELL — {ticker}: {sig['reason']}")
            sells.append(sig)

    return sells


def generate_buy_signals(
    portfolio_result: dict,
    current_positions: dict,
    regime: str,
) -> list[dict]:
    """
    Generate BUY signals for new positions (not already held).
    Attaches stop/target levels from regime params.
    """
    params       = REGIME_PARAMS[regime]
    proposed_buys = portfolio_result.get("proposed_buys", [])
    buy_signals   = []

    for p in proposed_buys:
        ticker = p["ticker"]
        if ticker in current_positions:
            continue  # already held

        price = p.get("last_close") or p.get("metrics", {}).get("last_close") or 0.0
        if price <= 0:
            logger.warning(f"No price for {ticker}, skipping buy signal")
            continue

        stop_price = price * (1 + params["stop_loss_pct"])
        tp_price   = price * (1 + params["take_profit_pct"])

        buy_signals.append({
            "ticker":       ticker,
            "score":        p.get("score", 0),
            "tech_score":   p.get("tech_score", 0),
            "fund_score":   p.get("fund_score", 0),
            "beta":         p.get("beta", 1.0),
            "weight":       p.get("weight", 0),
            "position_eur": p.get("position_eur", 0),
            "nb_shares":    p.get("nb_shares", 0),
            "model_price":  price,
            "stop_loss":    round(stop_price, 2),
            "take_profit":  round(tp_price, 2),
            "signals_tech": p.get("signals_tech", []),
            "signals_fund": p.get("signals_fund", []),
            "regime_bonus": p.get("regime_bonus", 0),
            "bonus_reason": p.get("bonus_reason", ""),
        })

    return buy_signals


def generate_momentum_signals(
    scored: list[dict],
    score_history: dict,
    regime: str,
    current_positions: dict,
) -> list[dict]:
    """
    Generate anticipatory BUY signals based on score trajectory.
    Qualifies when score is within MOMENTUM_SIGNAL_MAX_PTS_FROM_THRESHOLD of the regime
    threshold AND the last MOMENTUM_SIGNAL_HISTORY_RUNS data points (2 prior + current)
    each show a gain >= MOMENTUM_SIGNAL_MIN_GAIN_PER_RUN.
    """
    params    = REGIME_PARAMS[regime]
    threshold = params["score_threshold"]
    signals   = []

    for s in scored:
        ticker = s["ticker"]
        if ticker in current_positions:
            continue

        score = s["score"]

        # Classic BUY handles score >= threshold
        if score >= threshold:
            continue

        # Score must be within momentum window [threshold-8, threshold)
        if score < threshold - MOMENTUM_SIGNAL_MAX_PTS_FROM_THRESHOLD:
            continue

        # Need HISTORY_RUNS-1 prior runs in score_history, plus current = HISTORY_RUNS total
        history = score_history.get(ticker, [])
        if len(history) < MOMENTUM_SIGNAL_HISTORY_RUNS - 1:
            continue

        recent     = history[-(MOMENTUM_SIGNAL_HISTORY_RUNS - 1):]
        all_scores = [h["score"] for h in recent] + [score]
        gains      = [all_scores[i + 1] - all_scores[i] for i in range(len(all_scores) - 1)]

        if not all(g >= MOMENTUM_SIGNAL_MIN_GAIN_PER_RUN for g in gains):
            continue

        price = s.get("last_close") or 0.0
        if price <= 0:
            logger.warning(f"No price for {ticker}, skipping momentum signal")
            continue

        stop_price = price * (1 + params["stop_loss_pct"])
        tp_price   = price * (1 + params["take_profit_pct"])

        signals.append({
            "ticker":             ticker,
            "score":              score,
            "tech_score":         s.get("tech_score", 0),
            "fund_score":         s.get("fund_score", 0),
            "beta":               s.get("beta", 1.0),
            "model_price":        price,
            "stop_loss":          round(stop_price, 2),
            "take_profit":        round(tp_price, 2),
            "position_factor":    MOMENTUM_SIGNAL_POSITION_FACTOR,
            "score_history":      all_scores,
            "score_gains":        gains,
            "threshold":          threshold,
            "pts_from_threshold": threshold - score,
            "signals_tech":       s.get("signals_tech", []),
        })
        logger.info(
            f"Momentum signal: {ticker} score={score:.1f} "
            f"({threshold - score:.1f} below threshold), gains={gains}"
        )

    return signals
