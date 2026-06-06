import logging
import sys
from datetime import datetime

import config
from regime import detect_regime
from universe import get_liquid_universe
from portfolio import build_portfolio
from signals import evaluate_sells, generate_buy_signals, check_regime_change_sells
from state import load_state, save_state, update_score_history
from utils import compute_betas, portfolio_beta
from report import generate_report
from telegram_bot import (
    send_message, send_buy_alert, send_sell_alert,
    send_regime_change_alert, send_weekly_summary,
    poll_and_handle_commands,
)

logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)


def is_monday():
    return datetime.utcnow().weekday() == 0

def is_rebalance_week(state):
    week = datetime.utcnow().isocalendar().week
    return week % 2 == 0

def check_price_alerts(state, liquid):
    """Send Telegram notification when a ticker's price crosses a user-defined alert."""
    alerts = state.get("price_alerts", {})
    if not alerts:
        return
    prices = {item["ticker"]: item["metrics"]["last_close"] for item in liquid}
    triggered = []
    for ticker, target in list(alerts.items()):
        current = prices.get(ticker)
        if current is None:
            continue
        if abs(current - target) / target <= 0.01:
            triggered.append((ticker, current, target))
            del alerts[ticker]
    state["price_alerts"] = alerts
    for ticker, current, target in triggered:
        try:
            send_message(
                f"🔔 *ALERTE PRIX — {ticker}*\n"
                f"Prix actuel : {current:.2f} €\n"
                f"Cible : {target:.2f} €"
            )
        except Exception as e:
            logger.error(f"Price alert send failed: {e}")

def run():
    logger.info("=" * 60)
    logger.info("Portfolio Manager — starting run")
    logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
    logger.info("=" * 60)

    state = load_state()
    prev_regime = state.get("current_regime", "NEUTRAL")

    regime_info = detect_regime()
    regime = regime_info["regime"]
    cac_data = regime_info["cac40"]
    state["current_regime"] = regime
    logger.info(f"Regime: {regime} (prev: {prev_regime})")

    logger.info("Polling Telegram for commands...")
    try:
        poll_and_handle_commands()
    except Exception as e:
        logger.warning(f"Telegram poll failed: {e}")

    regime_changed = regime != prev_regime
    if regime_changed:
        logger.info(f"REGIME CHANGE: {prev_regime} -> {regime}")
        positions = state.get("positions", {})
        regime_sells = check_regime_change_sells(positions, prev_regime, regime)
        tickers_to_sell = [s["ticker"] for s in regime_sells]
        try:
            send_regime_change_alert(prev_regime, regime, cac_data, tickers_to_sell)
        except Exception as e:
            logger.error(f"Regime change alert failed: {e}")

    liquid = get_liquid_universe(regime)
    tickers = [item["ticker"] for item in liquid]
    logger.info(f"Liquid universe: {len(tickers)} tickers")

    if not tickers:
        logger.error("Empty universe — aborting")
        save_state(state)
        send_message("Portfolio Manager: univers vide, run annule.")
        sys.exit(1)

    betas = {item["ticker"]: 1.0 for item in liquid}

    from scorer import score_universe_from_cache
    scored = score_universe_from_cache(liquid, regime, betas)
    logger.info(f"Scored {len(scored)} tickers")

    update_score_history(state, scored)
    state["last_scores"] = scored  # stored for /top5 and /explain Telegram commands

    # Write scores_latest.json for the public dashboard
    try:
        import json as _json
        with open("scores_latest.json", "w") as _f:
            _json.dump({
                "generated_at": datetime.utcnow().isoformat(),
                "regime": regime,
                "tickers": scored,
            }, _f, indent=2, default=str)
    except Exception as e:
        logger.error(f"scores_latest.json write failed: {e}")

    check_price_alerts(state, liquid)

    sell_signals = evaluate_sells(state, regime, state.get("score_history", {}))
    logger.info(f"Sell signals: {len(sell_signals)}")

    for s in scored:
        idx = tickers.index(s["ticker"]) if s["ticker"] in tickers else 0
        s["last_close"] = liquid[idx]["metrics"].get("last_close")
        s["beta"] = betas.get(s["ticker"], 1.0)

    portfolio_result = build_portfolio(scored, state, regime, betas)
    buy_signals = generate_buy_signals(portfolio_result, state.get("positions", {}), regime)
    logger.info(f"Buy signals: {len(buy_signals)}")

    paused = state.get("signals_paused", False)
    if paused:
        logger.info("Signals paused — skipping buy/sell alerts")
    else:
        for sig in sell_signals:
            try:
                send_sell_alert(sig)
            except Exception as e:
                logger.error(f"Sell alert error: {e}")

        for sig in buy_signals:
            try:
                state.setdefault("pending_signals", {})[sig["ticker"]] = {
                    "model_price": sig["model_price"],
                    "stop_loss":   sig["stop_loss"],
                    "take_profit": sig["take_profit"],
                    "weight":      sig["weight"],
                    "beta":        sig["beta"],
                }
                send_buy_alert(sig, state, regime)
            except Exception as e:
                logger.error(f"Buy alert error: {e}")

    if is_monday():
        try:
            send_weekly_summary(state, regime, 0.0)
        except Exception as e:
            logger.error(f"Weekly summary failed: {e}")

    pb = portfolio_beta(state.get("positions", {}))
    logger.info(f"Portfolio beta: {pb:.2f} | Positions: {len(state.get('positions', {}))}")
    save_state(state)

    try:
        generate_report(state)
        logger.info("Excel report generated")
    except Exception as e:
        logger.error(f"Report generation failed: {e}")

    send_message(
        f"Run terminé | Régime: {regime} | Tickers: {len(tickers)} | "
        f"Scores: {len(scored)} | Buys: {len(buy_signals)}"
        + (" | ⏸ Paused" if paused else "")
    )

    logger.info("Run complete.")


if __name__ == "__main__":
    run()
