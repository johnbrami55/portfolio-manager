"""
main.py — Orchestrator: runs the full pipeline on each GitHub Actions trigger.

Pipeline:
  1. Load state
  2. Detect market regime (+ check for regime change)
  3. Poll Telegram for user commands
  4. Get liquid universe
  5. Compute betas
  6. Score universe
  7. Update score history
  8. Evaluate sell signals on open positions
  9. Build portfolio / generate buy signals
 10. Send alerts (sells first, then buys)
 11. Save state
 12. Weekly summary (Mondays)
"""

import logging
import sys
from datetime import datetime

import config
from regime   import detect_regime
from universe import get_liquid_universe
from scorer   import score_universe
from portfolio import build_portfolio
from signals  import evaluate_sells, generate_buy_signals, check_regime_change_sells
from state    import load_state, save_state, update_score_history
from utils    import compute_betas, portfolio_beta
from telegram_bot import (
    send_message, send_buy_alert, send_sell_alert,
    send_regime_change_alert, send_weekly_summary,
    poll_and_handle_commands,
)

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)


def is_monday() -> bool:
    return datetime.utcnow().weekday() == 0  # 0 = Monday


def is_rebalance_week(state: dict) -> bool:
    """
    Returns True every 2 weeks based on ISO week number.
    Rebalances on even ISO weeks (biweekly).
    """
    week = datetime.utcnow().isocalendar().week
    return week % 2 == 0


def run():
    logger.info("=" * 60)
    logger.info("Portfolio Manager — starting run")
    logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
    logger.info("=" * 60)

    # ── 1. Load state ──────────────────────────────────────────────────────────
    state = load_state()
    prev_regime = state.get("current_regime", "NEUTRAL")

    # ── 2. Detect regime ───────────────────────────────────────────────────────
    regime_info = detect_regime()
    regime      = regime_info["regime"]
    cac_data    = regime_info["cac40"]
    state["current_regime"] = regime

    logger.info(f"Regime: {regime} (prev: {prev_regime})")

    # ── 3. Poll Telegram commands ──────────────────────────────────────────────
    logger.info("Polling Telegram for commands...")
    try:
        poll_and_handle_commands()
    except Exception as e:
        logger.warning(f"Telegram poll failed (non-fatal): {e}")

    # ── 4. Regime change detection ─────────────────────────────────────────────
    regime_changed = regime != prev_regime
    if regime_changed:
        logger.info(f"⚠ REGIME CHANGE: {prev_regime} → {regime}")
        positions = state.get("positions", {})

        # Identify positions to sell due to regime change
        regime_sells = check_regime_change_sells(positions, prev_regime, regime)
        tickers_to_sell = [s["ticker"] for s in regime_sells]

        try:
            send_regime_change_alert(prev_regime, regime, cac_data, tickers_to_sell)
        except Exception as e:
            logger.error(f"Regime change alert failed: {e}")

        # Send individual sell alerts
        for sig in regime_sells:
            try:
                send_sell_alert(sig)
            except Exception as e:
                logger.error(f"Sell alert failed for {sig['ticker']}: {e}")

    # ── 5. Universe + liquidity filter ────────────────────────────────────────
    liquid = get_liquid_universe(regime)
    tickers = [item["ticker"] for item in liquid]
    logger.info(f"Liquid universe: {len(tickers)} tickers")

    if not tickers:
        logger.error("Empty universe — aborting")
        save_state(state)
        sys.exit(1)

    # ── 6. Compute betas ───────────────────────────────────────────────────────
    betas = compute_betas(tickers)

    # ── 7. Score universe ──────────────────────────────────────────────────────
    scored = score_universe(liquid, regime, betas)
    logger.info(f"Scored {len(scored)} tickers")

    # ── 8. Update score history ────────────────────────────────────────────────
    update_score_history(state, scored)

    # ── 9. Evaluate sell signals ───────────────────────────────────────────────
    sell_signals = evaluate_sells(state, regime, state.get("score_history", {}))
    logger.info(f"Sell signals: {len(sell_signals)}")

    # ── 10. Build portfolio / buy signals ──────────────────────────────────────
    # Rebalance check
    do_rebalance = is_monday() and is_rebalance_week(state)

    # Attach last_close to scored items for portfolio builder
    liquid_map = {item["ticker"]: item.get("metrics", {}) for item in liquid}
    for s in scored:
        s["last_close"] = liquid_map.get(s["ticker"], {}).get("last_close")
        s["beta"]       = betas.get(s["ticker"], 1.0)

    portfolio_result = build_portfolio(scored, state, regime, betas)
    buy_signals      = generate_buy_signals(
        portfolio_result, state.get("positions", {}), regime
    )

    logger.info(f"Buy signals: {len(buy_signals)}")

    # ── 11. Send alerts ────────────────────────────────────────────────────────
    for sig in sell_signals:
        try:
            send_sell_alert(sig)
        except Exception as e:
            logger.error(f"Sell alert send error for {sig['ticker']}: {e}")

    for sig in buy_signals:
        try:
            # Store pending signal in state for /bought confirmation
            state.setdefault("pending_signals", {})[sig["ticker"]] = {
                "model_price": sig["model_price"],
                "stop_loss":   sig["stop_loss"],
                "take_profit": sig["take_profit"],
                "weight":      sig["weight"],
                "beta":        sig["beta"],
            }
            send_buy_alert(sig, state, regime)
        except Exception as e:
            logger.error(f"Buy alert send error for {sig['ticker']}: {e}")

    # ── 12. Weekly summary (Mondays) ───────────────────────────────────────────
    if is_monday():
        try:
            # Compute CAC40 weekly return
            import yfinance as yf
            cac_hist     = yf.download(config.CAC40_TICKER, period="10d", progress=False, auto_adjust=True)
            cac_week_ret = float(cac_hist["Close"].pct_change(5).iloc[-1]) if len(cac_hist) >= 6 else 0.0
            send_weekly_summary(state, regime, cac_week_ret)
        except Exception as e:
            logger.error(f"Weekly summary failed: {e}")

    # ── 13. Save state ─────────────────────────────────────────────────────────
    pb = portfolio_beta(state.get("positions", {}))
    logger.info(f"Portfolio beta: {pb:.2f} | Positions: {len(state.get('positions', {}))}")
    save_state(state)

    logger.info("Run complete.")


if __name__ == "__main__":
    run()
