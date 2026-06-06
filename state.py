"""
state.py — Portfolio state persistence via portfolio_state.json.
Read and write operations for the entire portfolio state.
"""

import json
import logging
import os
from datetime import datetime
from config import STATE_FILE, INITIAL_CAPITAL

logger = logging.getLogger(__name__)

DEFAULT_STATE = {
    "current_regime":  "NEUTRAL",
    "last_run":        None,
    "positions":       {},
    "score_history":   {},
    "last_scores":     [],
    "trade_history":   [],
    "price_alerts":    {},
    "signals_paused":  False,
    "performance": {
        "total_pnl_eur":   0.0,
        "total_pnl_pct":   0.0,
        "weekly_returns":  [],
        "vs_cac40":        [],
    },
    "initial_capital": INITIAL_CAPITAL,
    "cash_eur":        INITIAL_CAPITAL,
}


def load_state() -> dict:
    """Load portfolio state from JSON file. Returns default state if not found."""
    if not os.path.exists(STATE_FILE):
        logger.info(f"{STATE_FILE} not found — initializing default state")
        return dict(DEFAULT_STATE)

    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        logger.info(f"State loaded: {len(state.get('positions', {}))} positions, "
                    f"cash={state.get('cash_eur', 0):.0f} EUR")
        return state
    except Exception as e:
        logger.error(f"Failed to load {STATE_FILE}: {e} — using defaults")
        return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    """Persist portfolio state to JSON file."""
    state["last_run"] = datetime.utcnow().isoformat()
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
        logger.info(f"State saved to {STATE_FILE}")
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def record_buy(
    state: dict,
    ticker: str,
    nb_shares: int,
    execution_price: float,
    model_price: float,
    stop_loss: float,
    take_profit: float,
    weight: float,
    beta: float,
) -> dict:
    """
    Record a confirmed buy execution in the state.
    Returns slippage info.
    """
    slippage_pct = (execution_price - model_price) / model_price if model_price else 0
    position_eur = nb_shares * execution_price

    state["positions"][ticker] = {
        "entry_price":   execution_price,
        "entry_date":    datetime.utcnow().isoformat()[:10],
        "nb_shares":     nb_shares,
        "weight":        weight,
        "model_price":   model_price,
        "slippage":      slippage_pct,
        "stop_loss":     stop_loss,
        "take_profit":   take_profit,
        "trailing_high": execution_price,
        "beta":          beta,
        "position_eur":  position_eur,
    }

    state["cash_eur"] = max(0, state.get("cash_eur", 0) - position_eur)
    logger.info(f"Recorded BUY {ticker}: {nb_shares} shares @ {execution_price:.2f} "
                f"(slippage {slippage_pct:.2%})")

    return {
        "ticker":         ticker,
        "nb_shares":      nb_shares,
        "execution_price": execution_price,
        "model_price":    model_price,
        "slippage_pct":   slippage_pct,
        "stop_loss":      stop_loss,
        "take_profit":    take_profit,
    }


def record_sell(
    state: dict,
    ticker: str,
    nb_shares: int,
    execution_price: float,
) -> dict:
    """
    Record a confirmed sell execution.
    Returns P&L info.
    """
    pos = state["positions"].get(ticker)
    if not pos:
        logger.warning(f"Sell recorded for {ticker} not in positions — tracking only")
        return {"ticker": ticker, "pnl_eur": 0, "pnl_pct": 0}

    entry     = pos["entry_price"]
    shares    = min(nb_shares, pos["nb_shares"])
    pnl_eur   = (execution_price - entry) * shares
    pnl_pct   = (execution_price - entry) / entry

    # Update performance
    state["performance"]["total_pnl_eur"] += pnl_eur
    initial = state.get("initial_capital", INITIAL_CAPITAL)
    state["performance"]["total_pnl_pct"] = state["performance"]["total_pnl_eur"] / initial

    # Remove or reduce position
    if shares >= pos["nb_shares"]:
        del state["positions"][ticker]
    else:
        state["positions"][ticker]["nb_shares"] -= shares
        state["positions"][ticker]["position_eur"] = (
            state["positions"][ticker]["nb_shares"] * execution_price
        )

    state["cash_eur"] = state.get("cash_eur", 0) + execution_price * shares

    state.setdefault("trade_history", []).append({
        "ticker":      ticker,
        "side":        "SELL",
        "nb_shares":   shares,
        "entry_price": entry,
        "exit_price":  execution_price,
        "pnl_eur":     round(pnl_eur, 2),
        "pnl_pct":     round(pnl_pct, 4),
        "entry_date":  pos.get("entry_date", ""),
        "exit_date":   datetime.utcnow().isoformat()[:10],
    })

    logger.info(f"Recorded SELL {ticker}: {shares} shares @ {execution_price:.2f} "
                f"P&L {pnl_eur:+.2f} EUR ({pnl_pct:+.1%})")

    return {
        "ticker":          ticker,
        "shares":          shares,
        "entry_price":     entry,
        "execution_price": execution_price,
        "pnl_eur":         pnl_eur,
        "pnl_pct":         pnl_pct,
    }


def update_score_history(state: dict, scores: list[dict]) -> None:
    """Update rolling score history (keep last 3 runs per ticker)."""
    today = datetime.utcnow().isoformat()[:10]
    history = state.setdefault("score_history", {})

    for s in scores:
        ticker = s["ticker"]
        entry  = {"score": s["score"], "date": today}
        if ticker not in history:
            history[ticker] = []
        history[ticker].append(entry)
        # Keep only last 3
        history[ticker] = history[ticker][-3:]
