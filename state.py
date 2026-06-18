"""
state.py — Portfolio state persistence via portfolio_state.json.
Read and write operations for the entire portfolio state.
"""

import json
import logging
import os
from datetime import datetime
from config import STATE_FILE, INITIAL_CAPITAL, SCORE_HISTORY_FILE, SCORE_HISTORY_MAX_RUNS

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
    state["last_run"] = datetime.utcnow().isoformat()
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
        logger.info(f"State saved to {STATE_FILE}")
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def _get_eur_usd() -> float:
    """Fetch current EUR/USD rate from Yahoo Finance."""
    try:
        import requests
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            params={"interval": "1d", "range": "5d"},
            timeout=10
        )
        if r.status_code == 200:
            result = r.json().get("chart", {}).get("result")
            if result:
                closes = result[0]["indicators"]["quote"][0].get("close", [])
                closes = [c for c in closes if c]
                if closes:
                    return closes[-1]
    except Exception:
        pass
    return 1.12


def _is_usd_ticker(ticker: str) -> bool:
    """Returns True if ticker is a USD-denominated asset."""
    eur_suffixes = ('.DE', '.PA', '.AS', '.MI', '.L', '.BR', '.MC')
    return not any(ticker.endswith(s) for s in eur_suffixes)


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
    execution_price is in the native currency of the ticker (USD for US stocks).
    Cash deduction is always in EUR.
    """
    slippage_pct = (execution_price - model_price) / model_price if model_price else 0

    # Convert to EUR for cash deduction
    if _is_usd_ticker(ticker):
        eur_usd = _get_eur_usd()
        execution_price_eur = execution_price / eur_usd
    else:
        eur_usd = 1.0
        execution_price_eur = execution_price

    position_eur = nb_shares * execution_price_eur

    state["positions"][ticker] = {
        "entry_price":   execution_price,        # prix natif (USD ou EUR)
        "entry_date":    datetime.utcnow().isoformat()[:10],
        "nb_shares":     nb_shares,
        "weight":        weight,
        "model_price":   model_price,
        "slippage":      slippage_pct,
        "stop_loss":     stop_loss,
        "take_profit":   take_profit,
        "trailing_high": execution_price,
        "beta":          beta,
        "position_eur":  round(position_eur, 2),
        "currency":      "USD" if _is_usd_ticker(ticker) else "EUR",
        "eur_usd":       round(eur_usd, 4) if _is_usd_ticker(ticker) else 1.0,
    }

    state["cash_eur"] = max(0, state.get("cash_eur", 0) - position_eur)
    logger.info(f"Recorded BUY {ticker}: {nb_shares} shares @ {execution_price:.2f} "
                f"({execution_price_eur:.2f} EUR) — cash remaining: {state['cash_eur']:.2f} EUR")

    return {
        "ticker":          ticker,
        "nb_shares":       nb_shares,
        "execution_price": execution_price,
        "execution_price_eur": execution_price_eur,
        "model_price":     model_price,
        "slippage_pct":    slippage_pct,
        "stop_loss":       stop_loss,
        "take_profit":     take_profit,
    }


def record_sell(
    state: dict,
    ticker: str,
    nb_shares: int,
    execution_price: float,
) -> dict:
    """
    Record a confirmed sell execution.
    execution_price is in the native currency of the ticker.
    """
    pos = state["positions"].get(ticker)
    if not pos:
        logger.warning(f"Sell recorded for {ticker} not in positions — tracking only")
        return {"ticker": ticker, "pnl_eur": 0, "pnl_pct": 0, "shares": nb_shares,
                "entry_price": 0, "execution_price": execution_price}

    entry  = pos["entry_price"]
    shares = min(nb_shares, pos["nb_shares"])

    # Convert to EUR
    if _is_usd_ticker(ticker):
        eur_usd = _get_eur_usd()
        execution_price_eur = execution_price / eur_usd
        entry_eur = entry / eur_usd
    else:
        execution_price_eur = execution_price
        entry_eur = entry

    pnl_eur = (execution_price_eur - entry_eur) * shares
    pnl_pct = (execution_price - entry) / entry if entry else 0

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
            state["positions"][ticker]["nb_shares"] * execution_price_eur
        )

    state["cash_eur"] = state.get("cash_eur", 0) + execution_price_eur * shares

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
    today = datetime.utcnow().isoformat()[:10]
    history = state.setdefault("score_history", {})
    for s in scores:
        ticker = s["ticker"]
        entry  = {"score": s["score"], "date": today}
        if ticker not in history:
            history[ticker] = []
        history[ticker].append(entry)
        history[ticker] = history[ticker][-3:]


def load_score_history() -> dict:
    if not os.path.exists(SCORE_HISTORY_FILE):
        return {}
    try:
        with open(SCORE_HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load {SCORE_HISTORY_FILE}: {e}")
        return {}


def save_score_history(history: dict) -> None:
    try:
        with open(SCORE_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
        logger.info(f"Score history saved to {SCORE_HISTORY_FILE}")
    except Exception as e:
        logger.error(f"Failed to save score history: {e}")


def append_to_score_history(history: dict, scores: list[dict]) -> dict:
    today = datetime.utcnow().isoformat()[:10]
    for s in scores:
        ticker = s["ticker"]
        entry  = {"score": s["score"], "date": today}
        if ticker not in history:
            history[ticker] = []
        history[ticker].append(entry)
        history[ticker] = history[ticker][-SCORE_HISTORY_MAX_RUNS:]
    return history
