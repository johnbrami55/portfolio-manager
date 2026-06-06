"""
telegram_bot.py — Telegram alert sender + bidirectional command handler.
Sends buy/sell/regime alerts and processes /bought, /sold, /portfolio, /status, /regime.
"""

import logging
import os
import json
import requests
from datetime import datetime
from config import REGIME_PARAMS, INITIAL_CAPITAL
from state import load_state, save_state, record_buy, record_sell
from utils import format_portfolio_snapshot, portfolio_beta, sector_exposure

logger = logging.getLogger(__name__)

# Telegram API base
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def exchange_of(ticker: str) -> str:
    if ticker.endswith(".PA"):
        return "Euronext Paris"
    if ticker.endswith(".AS"):
        return "Euronext Amsterdam"
    if ticker.endswith(".MI"):
        return "Borsa Italiana (Milan)"
    return "Euronext"


def _get_credentials() -> tuple[str, str]:
    """Retrieve bot token and chat ID from environment."""
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
    return token, chat_id


def send_message(text: str) -> bool:
    """Send a plain text message to the configured chat."""
    try:
        token, chat_id = _get_credentials()
        url  = TELEGRAM_API.format(token=token, method="sendMessage")
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }, timeout=15)
        ok = resp.status_code == 200
        if not ok:
            logger.error(f"Telegram send failed: {resp.text}")
        return ok
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


# ─── Alert Formatters ─────────────────────────────────────────────────────────

def send_buy_alert(signal: dict, portfolio: dict, regime: str) -> None:
    """Format and send a BUY alert."""
    t         = signal
    params    = REGIME_PARAMS[regime]
    positions = portfolio.get("positions", {})
    pb        = portfolio_beta(positions)
    sectors   = sector_exposure(positions)
    n_pos     = len(positions)

    # Build technical signal summary
    tech_lines = "\n".join(f"  ✓ {s}" for s in t.get("signals_tech", [])[:3])
    fund_lines = "\n".join(f"  ✓ {s}" for s in t.get("signals_fund", [])[:3])

    # Sector check
    from config import MAX_SECTOR_PCT
    from utils import sector_of
    sector = sector_of(t["ticker"])
    sec_wt = sectors.get(sector, 0)
    sec_ok = "✓" if sec_wt < MAX_SECTOR_PCT else "⚠"

    # Beta check
    beta_max = params["max_beta_per_stock"]
    beta_ok  = "✓" if t["beta"] <= beta_max else "⚠"

    bonus_line = ""
    if t.get("regime_bonus", 0) > 0:
        bonus_line = f"\nRegime bonus: +{t['regime_bonus']:.0f} pts ({t.get('bonus_reason','')})"

    trailing_line = ""
    if params.get("trailing_stop_pct"):
        trailing_line = f"\nTrailing stop: activated above +{params['trailing_stop_trigger']:.0%}"

    exchange   = exchange_of(t["ticker"])
    atr_pct    = t.get("atr_pct")
    atr_line   = f"\nATR(14): {atr_pct:.1%} (volatilité)" if atr_pct else ""
    dyn_stop   = t["model_price"] * (1 - 1.5 * atr_pct) if atr_pct else None
    stop_final = max(t["stop_loss"], dyn_stop) if dyn_stop else t["stop_loss"]

    text = (
        f"🟢 *SIGNAL BUY — {regime} REGIME*\n"
        f"📈 *{t['ticker']}* — {exchange}\n"
        f"Score: {t['score']:.0f}/100 | Beta: {t['beta']:.2f}\n"
        f"Suggested weight: {t['weight']:.0%} (~{t['position_eur']:.0f} EUR)\n"
        f"Nb shares: {t['nb_shares']} shares @ ~{t['model_price']:.2f} EUR\n"
        f"\n*Technical: {t['tech_score']:.0f}/50*\n{tech_lines}"
        f"\n*Fundamental: {t['fund_score']:.0f}/50*\n{fund_lines}"
        f"{bonus_line}\n"
        f"\nStop-loss: {stop_final:.2f} EUR ({params['stop_loss_pct']:.0%})"
        f"{atr_line}"
        f"\nTake-profit: {t['take_profit']:.2f} EUR (+{params['take_profit_pct']:.0%})"
        f"{trailing_line}\n"
        f"\n*Portfolio after trade:*\n"
        f"Regime: {regime}\n"
        f"Global beta: {pb:.2f} {beta_ok}\n"
        f"Sector {sector}: {sec_wt:.0%} {sec_ok}\n"
        f"Active positions: {n_pos}/{params['max_lines']}\n"
        f"Est. annual target: 15%\n"
        f"\n_Confirm: /bought {t['ticker']} {t['nb_shares']} <exec_price>_"
    )
    send_message(text)


def send_sell_alert(signal: dict) -> None:
    """Format and send a SELL alert."""
    pnl_sign = "📈" if signal.get("pnl_eur", 0) >= 0 else "📉"
    text = (
        f"🔴 *SIGNAL SELL*\n"
        f"📉 *{signal['ticker']}*\n"
        f"Reason: {signal['reason']}\n"
        f"Performance since entry: {signal['pnl_pct']:+.1%} ({signal['pnl_eur']:+.0f} EUR)\n"
        f"Action: SELL {signal['shares']} shares\n"
        f"\n_Confirm: /sold {signal['ticker']} {signal['shares']} <exec_price>_"
    )
    send_message(text)


def send_regime_change_alert(old: str, new: str, cac_data: dict, tickers_to_sell: list[str]) -> None:
    """Format and send a REGIME CHANGE alert."""
    ma50  = cac_data.get("ma50", 0)
    ma200 = cac_data.get("ma200", 0)
    close = cac_data.get("last_close", 0)
    params = REGIME_PARAMS[new]

    sell_str = "\n".join(f"  - {t}" for t in tickers_to_sell) if tickers_to_sell else "  (none)"

    text = (
        f"⚠️ *REGIME CHANGE: {old} → {new}*\n"
        f"CAC40: {close:.0f} | MA50: {ma50:.0f} | MA200: {ma200:.0f}\n"
        f"\n*Required actions:*\n"
        f"- Raise cash to {params['cash_pct_min']:.0%}–{params['cash_pct_max']:.0%}\n"
        f"- New score threshold: {params['score_threshold']}\n"
        f"- Max beta per stock: {params['max_beta_per_stock']}\n"
        f"- Positions to sell (beta too high):\n{sell_str}"
    )
    send_message(text)


def send_weekly_summary(state: dict, regime: str, cac_weekly_return: float = 0.0) -> None:
    """Send Monday weekly portfolio summary."""
    positions = state.get("positions", {})
    perf      = state.get("performance", {})
    cash      = state.get("cash_eur", 0)
    initial   = state.get("initial_capital", INITIAL_CAPITAL)
    pb        = portfolio_beta(positions)

    total_val = sum(p.get("position_eur", 0) for p in positions.values()) + cash
    total_pnl = perf.get("total_pnl_eur", 0)
    pnl_pct   = total_pnl / initial

    weekly_rets = perf.get("weekly_returns", [])
    week_ret    = weekly_rets[-1] if weekly_rets else 0.0
    vs_cac      = week_ret - cac_weekly_return

    # Find best/worst performers
    performers = []
    for ticker, pos in positions.items():
        try:
            import yfinance as yf
            hist  = yf.Ticker(ticker).history(period="7d")
            price = float(hist["Close"].iloc[-1]) if not hist.empty else pos.get("entry_price", 0)
            entry = pos.get("entry_price", price)
            performers.append((ticker, (price - entry) / entry if entry else 0))
        except Exception:
            pass

    performers.sort(key=lambda x: x[1])
    worst = performers[0]  if performers else ("N/A", 0)
    best  = performers[-1] if performers else ("N/A", 0)

    # Annual pace
    ann_pace = week_ret * 52
    pace_str = "on track" if ann_pace >= 0.12 else ("ahead" if ann_pace >= 0.15 else "behind")

    sectors = sector_exposure(positions)
    sec_str = " | ".join(f"{s}: {w:.0%}" for s, w in sectors.items())

    today = datetime.utcnow().strftime("%Y-%m-%d")
    text = (
        f"📊 *WEEKLY PORTFOLIO SUMMARY*\n"
        f"Week ending: {today}\n"
        f"Portfolio return: {week_ret:+.1%} ({week_ret*total_val:+.0f} EUR)\n"
        f"vs CAC40: {vs_cac:+.1%}\n"
        f"Regime: {regime}\n"
        f"Global beta: {pb:.2f}\n"
        f"Active positions: {len(positions)}/{REGIME_PARAMS[regime]['max_lines']}\n"
        f"Cash: {cash/total_val:.0%} (~{cash:.0f} EUR)\n"
        f"Top performer: {best[0]} {best[1]:+.1%}\n"
        f"Worst performer: {worst[0]} {worst[1]:+.1%}\n"
        f"Target pace: {pace_str} for {ann_pace:.0%} annual\n"
        f"Sectors: {sec_str}\n"
        f"Total P&L: {total_pnl:+.0f} EUR ({pnl_pct:+.1%})"
    )
    send_message(text)


# ─── Command Handler ──────────────────────────────────────────────────────────

def get_updates(offset: int = 0) -> list[dict]:
    """Fetch new Telegram updates."""
    try:
        token, _ = _get_credentials()
        url  = TELEGRAM_API.format(token=token, method="getUpdates")
        resp = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("result", [])
        return []
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
        return []


def handle_command(text: str) -> str:
    """
    Process a Telegram command and return a reply string.
    Commands: /bought, /sold, /portfolio, /status, /regime
    """
    parts = text.strip().split()
    if not parts:
        return "Empty command."

    cmd = parts[0].lower()
    state = load_state()

    # ── /bought <TICKER> <NB_SHARES> <PRICE> ────────────────────────────────
    if cmd == "/bought":
        if len(parts) < 4:
            return "Usage: /bought <TICKER> <NB_SHARES> <EXECUTION_PRICE>"
        ticker, nb_str, price_str = parts[1].upper(), parts[2], parts[3]
        try:
            nb_shares = int(nb_str)
            exec_price = float(price_str)
        except ValueError:
            return "Invalid nb_shares or price. Example: /bought AIR.PA 3 145.50"

        # Check if ticker is in model universe
        from config import FULL_UNIVERSE
        in_universe = ticker in FULL_UNIVERSE
        flag = "" if in_universe else "\n⚠ Position outside model universe — tracking P&L only, no score monitoring"

        # Find model signal data if available (from score_history)
        score_hist = state.get("score_history", {})
        model_price = exec_price  # fallback
        stop_loss   = exec_price * 0.90
        take_profit = exec_price * 1.18
        weight      = 0.0
        beta        = 1.0

        # Look up pending signal context if stored
        pending = state.get("pending_signals", {}).get(ticker, {})
        if pending:
            model_price = pending.get("model_price", exec_price)
            stop_loss   = pending.get("stop_loss", stop_loss)
            take_profit = pending.get("take_profit", take_profit)
            weight      = pending.get("weight", 0.0)
            beta        = pending.get("beta", 1.0)

        info = record_buy(
            state, ticker, nb_shares, exec_price, model_price,
            stop_loss, take_profit, weight, beta
        )
        save_state(state)

        slippage_str = f"{info['slippage_pct']:+.2%}" if model_price != exec_price else "N/A"
        return (
            f"✅ *BUY recorded: {ticker}*\n"
            f"{nb_shares} shares @ {exec_price:.2f} EUR\n"
            f"Stop-loss: {stop_loss:.2f} EUR\n"
            f"Take-profit: {take_profit:.2f} EUR\n"
            f"Slippage vs model: {slippage_str}"
            f"{flag}"
        )

    # ── /sold <TICKER> <NB_SHARES> <PRICE> ──────────────────────────────────
    elif cmd == "/sold":
        if len(parts) < 4:
            return "Usage: /sold <TICKER> <NB_SHARES> <EXECUTION_PRICE>"
        ticker, nb_str, price_str = parts[1].upper(), parts[2], parts[3]
        try:
            nb_shares  = int(nb_str)
            exec_price = float(price_str)
        except ValueError:
            return "Invalid nb_shares or price."

        info = record_sell(state, ticker, nb_shares, exec_price)
        save_state(state)

        pnl_sign = "✅" if info["pnl_eur"] >= 0 else "🔴"
        return (
            f"{pnl_sign} *SELL recorded: {ticker}*\n"
            f"{info['shares']} shares @ {exec_price:.2f} EUR\n"
            f"Entry: {info['entry_price']:.2f} EUR\n"
            f"P&L: {info['pnl_eur']:+.2f} EUR ({info['pnl_pct']:+.1%})\n"
            f"\n{format_portfolio_snapshot(state)}"
        )

    # ── /portfolio ────────────────────────────────────────────────────────────
    elif cmd == "/portfolio":
        positions = state.get("positions", {})
        cash      = state.get("cash_eur", 0)
        initial   = state.get("initial_capital", INITIAL_CAPITAL)
        regime    = state.get("current_regime", "?")
        pb        = portfolio_beta(positions)
        perf      = state.get("performance", {})
        total_pnl = perf.get("total_pnl_eur", 0)

        if not positions:
            return (
                f"📂 *Portefeuille vide*\n"
                f"Régime: {regime} | Cash: {cash:.0f} EUR\n"
                f"Capital initial: {initial:.0f} EUR"
            )

        from datetime import date as _date
        today     = _date.today()
        lines     = [f"📂 *Portefeuille — {regime}*\n"]
        latent    = 0.0

        for ticker, pos in positions.items():
            entry  = pos.get("entry_price", 0)
            stop   = pos.get("stop_loss", 0)
            tp     = pos.get("take_profit", 0)
            shares = pos.get("nb_shares", 0)
            try:
                import yfinance as yf
                hist  = yf.Ticker(ticker).history(period="5d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else entry
            except Exception:
                price = entry

            pnl_eur  = (price - entry) * shares
            pnl_pct  = (price - entry) / entry if entry else 0
            dist_stop_pct = (price - stop) / price if price else 0
            dist_tp_pct   = (tp - price) / price if price else 0
            dist_stop_eur = (price - stop) * shares
            dist_tp_eur   = (tp - price) * shares
            latent  += pnl_eur

            try:
                entry_d = _date.fromisoformat(pos.get("entry_date", str(today)))
                days    = (today - entry_d).days
            except Exception:
                days = 0

            sign = "🟢" if pnl_eur >= 0 else "🔴"
            lines.append(
                f"{sign} *{ticker}* ({days}j)\n"
                f"   Entrée: {entry:.2f} → Actuel: {price:.2f}\n"
                f"   P&L: {pnl_eur:+.0f}€ ({pnl_pct:+.1%})\n"
                f"   Stop: {stop:.2f} (−{dist_stop_pct:.1%} / {-dist_stop_eur:.0f}€)\n"
                f"   TP:   {tp:.2f} (+{dist_tp_pct:.1%} / +{dist_tp_eur:.0f}€)"
            )

        pos_val = sum(p.get("position_eur", 0) for p in positions.values())
        lines.append(
            f"\n*P&L latent total: {latent:+.2f} EUR*\n"
            f"Beta: {pb:.2f} | Positions: {pos_val:.0f}€ | Cash: {cash:.0f}€\n"
            f"P&L clôturé: {total_pnl:+.2f} EUR"
        )
        return "\n\n".join(lines)

    # ── /status ───────────────────────────────────────────────────────────────
    elif cmd == "/status":
        from config import FULL_UNIVERSE
        regime    = state.get("current_regime", "?")
        last_run  = state.get("last_run", "never")
        n_pos     = len(state.get("positions", {}))
        cash      = state.get("cash_eur", 0)
        return (
            f"⚙️ *System Status*\n"
            f"Regime: {regime}\n"
            f"Last run: {last_run}\n"
            f"Positions: {n_pos}\n"
            f"Cash: {cash:.0f} EUR\n"
            f"Universe size: {len(FULL_UNIVERSE)} tickers"
        )

    # ── /regime ───────────────────────────────────────────────────────────────
    elif cmd == "/regime":
        from regime import detect_regime
        r     = detect_regime()
        cac   = r["cac40"]
        stoxx = r["stoxx600"]
        cur   = r["regime"]
        params = REGIME_PARAMS[cur]
        return (
            f"📊 *Regime Analysis*\n"
            f"Current regime: *{cur}*\n\n"
            f"CAC40: {cac['last_close']:.0f} | MA50: {cac['ma50']:.0f} | MA200: {cac['ma200']:.0f}\n"
            f"Detail: {cac['detail']}\n\n"
            f"Stoxx600: {stoxx['regime']} | {stoxx['detail']}\n\n"
            f"*Parameters for {cur}:*\n"
            f"- Score threshold: {params['score_threshold']}\n"
            f"- Beta target: {params['beta_target_min']}–{params['beta_target_max']}\n"
            f"- Max lines: {params['max_lines']}\n"
            f"- Stop: {params['stop_loss_pct']:.0%} | TP: {params['take_profit_pct']:.0%}\n"
            f"- Cash: {params['cash_pct_min']:.0%}–{params['cash_pct_max']:.0%}"
        )

    # ── /sensi ────────────────────────────────────────────────────────────────
    elif cmd == "/sensi":
        positions = state.get("positions", {})
        pb        = portfolio_beta(positions)
        sectors   = sector_exposure(positions)
        total_val = sum(p.get("position_eur", 0) for p in positions.values()) + state.get("cash_eur", 0)
        # VaR 99% simplifié : valeur × 2.33 × beta × vol marché journalière (1%)
        var_99 = total_val * 2.33 * pb * 0.01 if pb > 0 else 0
        sec_str = "\n".join(f"  {s}: {w:.1%}" for s, w in sectors.items()) or "  (aucune position)"
        return (
            f"📐 *Sensibilité du portefeuille*\n"
            f"Beta global: {pb:.2f}\n"
            f"VaR 99% journalière (simplifiée): {var_99:.0f} EUR\n"
            f"Valeur totale: {total_val:.0f} EUR\n\n"
            f"*Exposition par secteur:*\n{sec_str}"
        )

    # ── /perf ─────────────────────────────────────────────────────────────────
    elif cmd == "/perf":
        positions = state.get("positions", {})
        perf      = state.get("performance", {})
        initial   = state.get("initial_capital", INITIAL_CAPITAL)
        cash      = state.get("cash_eur", 0)
        total_pnl = perf.get("total_pnl_eur", 0)
        pnl_pct   = perf.get("total_pnl_pct", 0)
        lines     = [f"📊 *Performance du portefeuille*\n"]
        for ticker, pos in positions.items():
            entry = pos.get("entry_price", 0)
            try:
                import yfinance as yf
                hist  = yf.Ticker(ticker).history(period="5d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else entry
            except Exception:
                price = entry
            pnl_eur = (price - entry) * pos.get("nb_shares", 0)
            pnl_pct_pos = (price - entry) / entry if entry else 0
            sign = "✅" if pnl_eur >= 0 else "🔴"
            lines.append(f"{sign} {ticker}: {pnl_eur:+.0f}€ ({pnl_pct_pos:+.1%})")
        lines.append(f"\n*Total P&L:* {total_pnl:+.2f} EUR ({pnl_pct:+.1%})")
        lines.append(f"Capital initial: {initial:.0f} EUR")
        lines.append(f"Cash: {cash:.0f} EUR")
        return "\n".join(lines)

    # ── /risk ─────────────────────────────────────────────────────────────────
    elif cmd == "/risk":
        positions = state.get("positions", {})
        if not positions:
            return "Aucune position ouverte."
        lines = ["⚠️ *Distances stop/TP par position*\n"]
        for ticker, pos in positions.items():
            entry = pos.get("entry_price", 0)
            stop  = pos.get("stop_loss", 0)
            tp    = pos.get("take_profit", 0)
            try:
                import yfinance as yf
                hist  = yf.Ticker(ticker).history(period="5d")
                price = float(hist["Close"].iloc[-1]) if not hist.empty else entry
            except Exception:
                price = entry
            dist_stop = (price - stop) / price if price else 0
            dist_tp   = (tp - price) / price if price else 0
            eur_risk  = (price - stop) * pos.get("nb_shares", 0)
            eur_tp    = (tp - price) * pos.get("nb_shares", 0)
            lines.append(
                f"*{ticker}*\n"
                f"  Prix: {price:.2f} | Stop: {stop:.2f} | TP: {tp:.2f}\n"
                f"  Dist. stop: -{dist_stop:.1%} ({-eur_risk:.0f}€)\n"
                f"  Dist. TP:   +{dist_tp:.1%} (+{eur_tp:.0f}€)"
            )
        return "\n\n".join(lines)

    # ── /top5 ─────────────────────────────────────────────────────────────────
    elif cmd == "/top5":
        last_scores = state.get("last_scores", [])
        if not last_scores:
            return "Aucun score disponible. Lancez d'abord un run."
        top = last_scores[:5]
        lines = ["🏆 *Top 5 tickers du dernier run*\n"]
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, s in enumerate(top):
            bd   = s.get("breakdown", {})
            lines.append(
                f"{medals[i]} *{s['ticker']}* — Score: {s['score']:.1f}/100\n"
                f"   Tech: {s['tech_score']:.1f} | Beta: {s['beta']:.2f}\n"
                f"   Trend:{bd.get('trend',0):.0f} RSI:{bd.get('rsi',0):.0f} "
                f"MACD:{bd.get('macd',0):.0f} Boll:{bd.get('bollinger',0):.0f} StRSI:{bd.get('stoch_rsi',0):.0f}"
            )
        return "\n\n".join(lines)

    # ── /explain <TICKER> ─────────────────────────────────────────────────────
    elif cmd == "/explain":
        if len(parts) < 2:
            return "Usage: /explain <TICKER>  (ex: /explain AIR.PA)"
        target      = parts[1].upper()
        last_scores = state.get("last_scores", [])
        match       = next((s for s in last_scores if s["ticker"] == target), None)
        if not match:
            return f"{target} non trouvé dans le dernier run."
        bd    = match.get("breakdown", {})
        sigs  = match.get("signals_tech", [])
        return (
            f"🔍 *Analyse complète — {target}*\n"
            f"Score global: *{match['score']:.1f}/100*\n"
            f"Tech: {match['tech_score']:.1f} | Fund: {match['fund_score']:.1f} | Bonus régime: {match.get('regime_bonus',0):.0f}\n\n"
            f"*Détail technique:*\n"
            f"  Trend:    {bd.get('trend',0):.1f}/10\n"
            f"  RSI:      {bd.get('rsi',0):.1f}/6\n"
            f"  Volume:   {bd.get('volume',0):.1f}/8\n"
            f"  MACD:     {bd.get('macd',0):.1f}/6\n"
            f"  Momentum: {bd.get('momentum',0):.1f}/6\n"
            f"  Bollinger:{bd.get('bollinger',0):.1f}/8\n"
            f"  StochRSI: {bd.get('stoch_rsi',0):.1f}/6\n"
            f"  ATR:      {(match.get('atr_pct') or 0)*100:.2f}%\n\n"
            f"*Signaux:*\n" + "\n".join(f"  • {s}" for s in sigs[:6])
        )

    # ── /cash ─────────────────────────────────────────────────────────────────
    elif cmd == "/cash":
        cash      = state.get("cash_eur", 0)
        initial   = state.get("initial_capital", INITIAL_CAPITAL)
        positions = state.get("positions", {})
        pos_val   = sum(p.get("position_eur", 0) for p in positions.values())
        total     = pos_val + cash
        return (
            f"💰 *Capital disponible*\n"
            f"Cash: {cash:.2f} EUR ({cash/total:.1%} du portefeuille)\n"
            f"Positions: {pos_val:.2f} EUR ({pos_val/total:.1%})\n"
            f"Total estimé: {total:.2f} EUR\n"
            f"Capital initial: {initial:.2f} EUR\n"
            f"Nb positions: {len(positions)}"
        )

    # ── /alert <TICKER> <PRIX> ────────────────────────────────────────────────
    elif cmd == "/alert":
        if len(parts) < 3:
            return "Usage: /alert <TICKER> <PRIX>  (ex: /alert AIR.PA 150.00)"
        ticker_a = parts[1].upper()
        try:
            target_price = float(parts[2])
        except ValueError:
            return "Prix invalide. Exemple: /alert AIR.PA 150.00"
        state.setdefault("price_alerts", {})[ticker_a] = target_price
        save_state(state)
        return f"🔔 Alerte créée : *{ticker_a}* @ {target_price:.2f} EUR\nVous serez notifié quand le prix croise ce niveau (±1%)."

    # ── /run ──────────────────────────────────────────────────────────────────
    elif cmd == "/run":
        try:
            resp = requests.post(
                "https://api.github.com/repos/johnbrami55/portfolio-manager/actions/workflows/portfolio_manager.yml/dispatches",
                headers={
                    "Authorization": f"token {os.environ.get('GITHUB_PAT', '')}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"ref": "main"},
                timeout=10,
            )
            if resp.status_code == 204:
                return "🚀 Run lancé immédiatement ! Signaux dans ~20 minutes."
            else:
                return f"❌ Erreur lancement run : {resp.status_code} — {resp.text[:200]}"
        except Exception as e:
            return f"❌ Erreur lancement run : {e}"

    # ── /pause ────────────────────────────────────────────────────────────────
    elif cmd == "/pause":
        state["signals_paused"] = True
        save_state(state)
        return "⏸ Signaux automatiques *mis en pause*.\nLes runs s'exécutent toujours mais n'envoient plus d'alertes buy/sell.\nUtilisez /resume pour reprendre."

    # ── /resume ───────────────────────────────────────────────────────────────
    elif cmd == "/resume":
        state["signals_paused"] = False
        save_state(state)
        return "▶️ Signaux automatiques *repris*.\nLes alertes buy/sell seront envoyées au prochain run."

    else:
        return (
            "Commandes disponibles:\n"
            "/bought <TICKER> <NB> <PRICE>\n"
            "/sold <TICKER> <NB> <PRICE>\n"
            "/portfolio — positions + P&L\n"
            "/status — état du système\n"
            "/regime — analyse du régime\n"
            "/sensi — beta, secteurs, VaR\n"
            "/perf — performance par position\n"
            "/risk — distances stop/TP\n"
            "/top5 — top 5 tickers scorés\n"
            "/explain <TICKER> — détail complet\n"
            "/cash — capital disponible\n"
            "/alert <TICKER> <PRIX> — alerte prix\n"
            "/pause — suspendre les signaux\n"
            "/resume — reprendre les signaux\n"
            "/run — déclencher un run immédiat"
        )


def poll_and_handle_commands(max_updates: int = 10) -> None:
    """
    Poll Telegram for new messages and dispatch commands.
    Only processes messages from TELEGRAM_CHAT_ID.

    Anti-boucle :
    1. On récupère tous les updates en attente.
    2. On ACK immédiatement côté Telegram (getUpdates offset+1) AVANT tout traitement.
       => même si le run est annulé ensuite, les messages ne seront plus jamais revus.
    3. /run ne peut être dispatché qu'UNE seule fois par batch, peu importe combien
       de fois l'utilisateur l'a envoyé.
    """
    try:
        _, authorized_chat_id = _get_credentials()
    except EnvironmentError as e:
        logger.error(str(e))
        return

    state = load_state()
    last_update_id: int = state.get("last_telegram_update_id", 0)

    updates = get_updates(offset=last_update_id + 1 if last_update_id else 0)
    if not updates:
        return

    highest_update_id = max(u.get("update_id", 0) for u in updates)

    # ── ÉTAPE 1 : ACK immédiat côté Telegram ─────────────────────────────────
    # Marque tous les messages comme lus sur les serveurs Telegram AVANT de les
    # traiter. Empêche toute boucle même si le run est annulé mid-flight.
    get_updates(offset=highest_update_id + 1)
    state["last_telegram_update_id"] = highest_update_id
    save_state(state)
    logger.info(f"ACK Telegram updates up to {highest_update_id}")

    # ── ÉTAPE 2 : Traitement des commandes ───────────────────────────────────
    run_already_dispatched = False  # /run ne se déclenche qu'une seule fois

    for update in updates[-max_updates:]:
        msg     = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()

        if not text.startswith("/"):
            continue
        if chat_id != str(authorized_chat_id):
            logger.warning(f"Unauthorized command from chat_id={chat_id}")
            continue

        cmd = text.split()[0].lower()

        # /run : un seul dispatch par batch, quelle que soit la quantité de messages
        if cmd == "/run":
            if run_already_dispatched:
                logger.info("/run ignoré : déjà dispatché dans ce batch")
                continue
            run_already_dispatched = True

        logger.info(f"Handling command: {text}")
        reply = handle_command(text)
        send_message(reply)
