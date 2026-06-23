"""
telegram_bot.py — Telegram alert sender + command handler.
Sends buy/sell/regime alerts. handle_command() is called by telegram_listener.
Polling is handled exclusively by telegram_listener.py.
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

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


_NYSE_TICKERS = {
    "BRK-B","JPM","UNH","XOM","JNJ","WMT","MA","PG","LLY","HD",
    "MRK","ABBV","PEP","KO","COST","TMO","MCD","ACN","BAC","NEE",
    "RTX","HON","UPS","PM","V","LIN","ABT","DHR","AVGO",
}

def exchange_of(ticker: str) -> str:
    if ticker.endswith(".PA"): return "Euronext Paris"
    if ticker.endswith(".AS"): return "Euronext Amsterdam"
    if ticker.endswith(".MI"): return "Borsa Italiana"
    if ticker.endswith(".HK"): return "HKEX"
    if ticker in _NYSE_TICKERS: return "NYSE"
    return "Nasdaq"


def _get_credentials() -> tuple[str, str]:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
    return token, chat_id


def send_message(text: str) -> bool:
    try:
        token, chat_id = _get_credentials()
        url  = TELEGRAM_API.format(token=token, method="sendMessage")
        resp = requests.post(url, json={
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown",
        }, timeout=15)
        ok = resp.status_code == 200
        if not ok:
            logger.error(f"Telegram send failed: {resp.text}")
        return ok
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


# ─── Alert Formatters ─────────────────────────────────────────────

def send_buy_alert(signal: dict, portfolio: dict, regime: str) -> None:
    t      = signal
    params = REGIME_PARAMS[regime]
    positions = portfolio.get("positions", {})
    pb     = portfolio_beta(positions)
    sectors = sector_exposure(positions)
    n_pos  = len(positions)

    tech_lines = "\n".join(f"  ✓ {s}" for s in t.get("signals_tech", [])[:3])
    fund_lines = "\n".join(f"  ✓ {s}" for s in t.get("signals_fund", [])[:3])

    from config import MAX_SECTOR_PCT
    from utils import sector_of
    sector = sector_of(t["ticker"])
    sec_wt = sectors.get(sector, 0)
    sec_ok = "✓" if sec_wt < MAX_SECTOR_PCT else "⚠"
    beta_ok = "✓" if t["beta"] <= params["max_beta_per_stock"] else "⚠"

    bonus_line = f"\nRegime bonus: +{t['regime_bonus']:.0f} pts ({t.get('bonus_reason','')})" if t.get("regime_bonus", 0) > 0 else ""
    trailing_line = f"\nTrailing stop: activated above +{params['trailing_stop_trigger']:.0%}" if params.get("trailing_stop_pct") else ""

    exchange  = exchange_of(t["ticker"])
    atr_pct   = t.get("atr_pct")
    atr_line  = f"\nATR(14): {atr_pct:.1%} (volatilité)" if atr_pct else ""
    dyn_stop  = t["model_price"] * (1 - 1.5 * atr_pct) if atr_pct else None
    stop_final = max(t["stop_loss"], dyn_stop) if dyn_stop else t["stop_loss"]

    from utils import calculate_fee, market_of as _mof
    _fee = calculate_fee(t["position_eur"], t["ticker"])
    fee_line = f"\nFrais DEGIRO ({_mof(t['ticker'])}): ~{_fee:.2f}€/leg | ~{_fee*2:.2f}€ aller-retour"

    text = (
        f"\U0001f7e2 *SIGNAL BUY — {regime} REGIME*\n"
        f"\U0001f4c8 *{t['ticker']}* — {exchange}\n"
        f"Score: {t['score']:.0f}/100 | Beta: {t['beta']:.2f}\n"
        f"Suggested weight: {t['weight']:.0%} (~{t['position_eur']:.0f} EUR)\n"
        f"Nb shares: {t['nb_shares']} shares @ ~{t['model_price']:.2f} EUR{fee_line}\n"
        f"\n*Technical: {t['tech_score']:.0f}/50*\n{tech_lines}"
        f"\n*Fundamental: {t['fund_score']:.0f}/50*\n{fund_lines}"
        f"{bonus_line}\n"
        f"\nStop-loss: {stop_final:.2f} EUR ({params['stop_loss_pct']:.0%}){atr_line}"
        f"\nTake-profit: {t['take_profit']:.2f} EUR (+{params['take_profit_pct']:.0%}){trailing_line}\n"
        f"\n*Portfolio after trade:*\n"
        f"Regime: {regime}\nGlobal beta: {pb:.2f} {beta_ok}\n"
        f"Sector {sector}: {sec_wt:.0%} {sec_ok}\n"
        f"Active positions: {n_pos}/{params['max_lines']}\n"
        f"Est. annual target: 15%\n"
        f"\n_Confirm: /bought {t['ticker']} {t['nb_shares']} <exec_price>_"
    )
    send_message(text)


def send_momentum_alert(signal: dict, portfolio: dict, regime: str) -> None:
    t         = signal
    params    = REGIME_PARAMS[regime]
    positions = portfolio.get("positions", {})
    n_pos     = len(positions)

    history_str = " → ".join(f"{s:.0f}" for s in t.get("score_history", []))
    gains_str   = ", ".join(f"+{g:.1f}" for g in t.get("score_gains", []))

    text = (
        f"\U0001f535 *SIGNAL ANTICIPÉ (momentum) — {regime} REGIME*\n"
        f"\U0001f4c8 *{t['ticker']}* — Trajectoire haussière\n"
        f"Score actuel: {t['score']:.1f} | Seuil: {t['threshold']:.0f} "
        f"(−{t['pts_from_threshold']:.1f} pts)\n"
        f"Trajectoire: {history_str}\n"
        f"Gains/run: {gains_str}\n"
        f"\n⚠️ *Position réduite −30%*\n"
        f"Taille: ×{t['position_factor']:.0%} vs signal classique\n"
        f"Prix indicatif: ~{t['model_price']:.2f} EUR\n"
        f"Stop-loss: {t['stop_loss']:.2f} EUR ({params['stop_loss_pct']:.0%})\n"
        f"Take-profit: {t['take_profit']:.2f} EUR (+{params['take_profit_pct']:.0%})\n"
        f"\nPositions actives: {n_pos}/{params['max_lines']}\n"
        f"\n_Signal anticipé — attendre confirmation ou franchissement du seuil._"
    )
    send_message(text)


def send_sell_alert(signal: dict) -> None:
    text = (
        f"\U0001f534 *SIGNAL SELL*\n"
        f"\U0001f4c9 *{signal['ticker']}*\n"
        f"Reason: {signal['reason']}\n"
        f"Performance since entry: {signal['pnl_pct']:+.1%} ({signal['pnl_eur']:+.0f} EUR)\n"
        f"Action: SELL {signal['shares']} shares\n"
        f"\n_Confirm: /sold {signal['ticker']} {signal['shares']} <exec_price>_"
    )
    send_message(text)


def send_regime_change_alert(old: str, new: str, cac_data: dict, tickers_to_sell: list[str]) -> None:
    params   = REGIME_PARAMS[new]
    sell_str = "\n".join(f"  - {t}" for t in tickers_to_sell) if tickers_to_sell else "  (none)"
    text = (
        f"⚠️ *REGIME CHANGE: {old} → {new}*\n"
        f"CAC40: {cac_data.get('last_close',0):.0f} | MA50: {cac_data.get('ma50',0):.0f} | MA200: {cac_data.get('ma200',0):.0f}\n"
        f"\n*Required actions:*\n"
        f"- Raise cash to {params['cash_pct_min']:.0%}–{params['cash_pct_max']:.0%}\n"
        f"- New score threshold: {params['score_threshold']}\n"
        f"- Max beta per stock: {params['max_beta_per_stock']}\n"
        f"- Positions to sell (beta too high):\n{sell_str}"
    )
    send_message(text)


def send_weekly_summary(state: dict, regime: str, cac_weekly_return: float = 0.0) -> None:
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
    ann_pace = week_ret * 52
    pace_str = "on track" if ann_pace >= 0.12 else ("ahead" if ann_pace >= 0.15 else "behind")
    sectors  = sector_exposure(positions)
    sec_str  = " | ".join(f"{s}: {w:.0%}" for s, w in sectors.items())
    today    = datetime.utcnow().strftime("%Y-%m-%d")

    send_message(
        f"\U0001f4ca *WEEKLY PORTFOLIO SUMMARY*\n"
        f"Week ending: {today}\n"
        f"Portfolio return: {week_ret:+.1%} ({week_ret*total_val:+.0f} EUR)\n"
        f"vs CAC40: {vs_cac:+.1%}\nRegime: {regime}\nGlobal beta: {pb:.2f}\n"
        f"Active positions: {len(positions)}/{REGIME_PARAMS[regime]['max_lines']}\n"
        f"Cash: {cash/total_val:.0%} (~{cash:.0f} EUR)\n"
        f"Top performer: {best[0]} {best[1]:+.1%}\n"
        f"Worst performer: {worst[0]} {worst[1]:+.1%}\n"
        f"Target pace: {pace_str} for {ann_pace:.0%} annual\n"
        f"Sectors: {sec_str}\nTotal P&L: {total_pnl:+.0f} EUR ({pnl_pct:+.1%})"
    )


# ─── Command Handler ──────────────────────────────────────────────

def handle_command(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return "Empty command."
    cmd   = parts[0].lower()
    state = load_state()

    if cmd == "/bought":
        if len(parts) < 4:
            return "Usage: /bought <TICKER> <NB_SHARES> <EXECUTION_PRICE>"
        ticker, nb_str, price_str = parts[1].upper(), parts[2], parts[3]
        try:
            nb_shares  = int(nb_str)
            exec_price = float(price_str)
        except ValueError:
            return "Invalid nb_shares or price. Example: /bought AIR.PA 3 145.50"
        from config import FULL_UNIVERSE
        flag = "" if ticker in FULL_UNIVERSE else "\n⚠ Position outside model universe — tracking P&L only"
        model_price = exec_price
        stop_loss   = exec_price * 0.90
        take_profit = exec_price * 1.18
        weight = beta = 0.0
        pending = state.get("pending_signals", {}).get(ticker, {})
        if pending:
            model_price = pending.get("model_price", exec_price)
            stop_loss   = pending.get("stop_loss", stop_loss)
            take_profit = pending.get("take_profit", take_profit)
            weight      = pending.get("weight", 0.0)
            beta        = pending.get("beta", 1.0)
        info = record_buy(state, ticker, nb_shares, exec_price, model_price, stop_loss, take_profit, weight, beta)
        save_state(state)
        slippage_str = f"{info['slippage_pct']:+.2%}" if model_price != exec_price else "N/A"
        return (
            f"✅ *BUY recorded: {ticker}*\n"
            f"{nb_shares} shares @ {exec_price:.2f}\n"
            f"Stop-loss: {stop_loss:.2f}\nTake-profit: {take_profit:.2f}\n"
            f"Slippage vs model: {slippage_str}{flag}"
        )

    elif cmd == "/boughtcore":
        if len(parts) < 4:
            return "Usage: /boughtcore <TICKER> <NB_SHARES> <EXECUTION_PRICE>"
        ticker, nb_str, price_str = parts[1].upper(), parts[2], parts[3]
        try:
            nb_shares  = int(nb_str)
            exec_price = float(price_str)
        except ValueError:
            return "Invalid nb_shares or price."
        model_price = exec_price
        stop_loss   = exec_price * 0.93   # stop -7% pour le core
        take_profit = exec_price * 1.20   # TP +20%
        weight = beta = 0.0
        info = record_buy(state, ticker, nb_shares, exec_price, model_price, stop_loss, take_profit, weight, beta)
        # Enregistrer aussi dans core
        state.setdefault("core", {})[ticker] = {
            "shares":      nb_shares,
            "entry_price": exec_price,
            "entry_date":  datetime.utcnow().isoformat()[:10],
        }
        save_state(state)
        return (
            f"✅ *CORE BUY recorded: {ticker}*\n"
            f"{nb_shares} shares @ {exec_price:.2f}\n"
            f"Stop-loss: {stop_loss:.2f}\n"
            f"Take-profit: {take_profit:.2f}\n"
            f"Layer: CORE 🏦"
        )

    elif cmd == "/sold":
        if len(parts) < 4:
            return "Usage: /sold <TICKER> <NB_SHARES> <EXECUTION_PRICE>"
        ticker, nb_str, price_str = parts[1].upper(), parts[2], parts[3]
        try:
            nb_shares  = int(nb_str)
            exec_price = float(price_str)
        except ValueError:
            return "Invalid nb_shares or price."
        # Retirer aussi du core si présent
        if ticker in state.get("core", {}):
            del state["core"][ticker]
        # Retirer du satellite si présent
        if ticker in state.get("satellite", {}):
            del state["satellite"][ticker]
        info     = record_sell(state, ticker, nb_shares, exec_price)
        save_state(state)
        pnl_sign = "✅" if info["pnl_eur"] >= 0 else "\U0001f534"
        return (
            f"{pnl_sign} *SELL recorded: {ticker}*\n"
            f"{info['shares']} shares @ {exec_price:.2f}\n"
            f"Entry: {info['entry_price']:.2f}\n"
            f"P&L: {info['pnl_eur']:+.2f} EUR ({info['pnl_pct']:+.1%})\n"
            f"\n{format_portfolio_snapshot(state)}"
        )

    elif cmd == "/portfolio":
        positions = state.get("positions", {})
        cash      = state.get("cash_eur", 0)
        initial   = state.get("initial_capital", INITIAL_CAPITAL)
        regime    = state.get("current_regime", "?")
        pb        = portfolio_beta(positions)
        total_pnl = state.get("performance", {}).get("total_pnl_eur", 0)
        if not positions:
            return f"\U0001f4c2 *Portefeuille vide*\nRégime: {regime} | Cash: {cash:.0f} EUR\nCapital initial: {initial:.0f} EUR"
        from datetime import date as _date
        today  = _date.today()
        core_tickers = set(state.get("core", {}).keys())
        lines  = [f"\U0001f4c2 *Portefeuille — {regime}*\n"]
        latent = 0.0
        for ticker, pos in positions.items():
            entry  = pos.get("entry_price", 0)
            stop   = pos.get("stop_loss", 0)
            tp     = pos.get("take_profit", 0)
            shares = pos.get("nb_shares", 0)
            price  = pos.get("current_price", entry)
            currency = pos.get("currency", "EUR")
            eur_usd  = pos.get("eur_usd", 1.12)
            price_eur = price / eur_usd if currency == "USD" else price
            entry_eur = entry / eur_usd if currency == "USD" else entry
            pnl_eur = (price_eur - entry_eur) * shares
            pnl_pct = (price - entry) / entry if entry else 0
            sym = "$" if currency == "USD" else "€"
            latent += pnl_eur
            try:
                days = (_date.today() - _date.fromisoformat(pos.get("entry_date", str(today)))).days
            except Exception:
                days = 0
            sign  = "\U0001f7e2" if pnl_eur >= 0 else "\U0001f534"
            layer = "🏦" if ticker in core_tickers else "🛰"
            lines.append(
                f"{sign} {layer} *{ticker}* ({days}j)\n"
                f"   Entrée: {entry:.2f}{sym} → Actuel: {price:.2f}{sym}\n"
                f"   P&L: {pnl_eur:+.0f}€ ({pnl_pct:+.1%})\n"
                f"   Stop: {stop:.2f}{sym}   TP: {tp:.2f}{sym}"
            )
        pos_val = sum(p.get("position_eur", 0) for p in positions.values())
        lines.append(
            f"\n*P&L latent: {latent:+.2f} EUR*\n"
            f"Beta: {pb:.2f} | Positions: {pos_val:.0f}€ | Cash: {cash:.0f}€\n"
            f"P&L clôturé: {total_pnl:+.2f} EUR\n\n"
            f"📊 [Dashboard](https://johnbrami55.github.io/portfolio-dashboard/)"
        )
        return "\n\n".join(lines)
       
    elif cmd == "/check":
        if len(parts) < 2:
            return "Usage: /check <TICKER>"
        ticker = parts[1].upper()
        try:
            import requests as req
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            r = req.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                        params={"interval": "1d", "range": "2y"}, timeout=15)
            if r.status_code != 200:
                return f"❌ Impossible de charger {ticker}"
            result = r.json().get("chart", {}).get("result")
            if not result:
                return f"❌ Données indisponibles pour {ticker}"
            
            from momentum import fetch_history, score_satellite, detect_regime, in_bear
            import yfinance as yf
            spy_hist = yf.Ticker("SPY").history(period="1y")
            spy_closes = list(spy_hist["Close"])
            spy_highs  = list(spy_hist["High"])
            spy_lows   = list(spy_hist["Low"])
            spy_vols   = list(spy_hist["Volume"])
            spy_data = {"closes": spy_closes, "highs": spy_highs, "lows": spy_lows, "volumes": spy_vols, "price": spy_closes[-1]}
            regime = detect_regime(spy_data)
            
            data = fetch_history(ticker)
            if not data:
                return f"❌ Historique insuffisant pour {ticker}"
            
            score, atr_pct, tp_dynamic = score_satellite(data, regime)
            price = data["price"]
            
            # Position actuelle si détenue
            pos = state.get("positions", {}).get(ticker)
            pos_info = ""
            if pos:
                entry = pos.get("entry_price", price)
                pnl_pct = (price - entry) / entry * 100
                stop = pos.get("stop_loss", 0)
                tp = pos.get("take_profit", 0)
                currency = pos.get("currency", "EUR")
                sym = "$" if currency == "USD" else "€"
                pos_info = (
                    f"\n📊 <b>Position détenue :</b>\n"
                    f"   Entrée : {entry:.2f}{sym} → Actuel : {price:.2f}{sym}\n"
                    f"   P&L : {pnl_pct:+.1f}%\n"
                    f"   Stop : {stop:.2f}{sym} | TP : {tp:.2f}{sym}\n"
                )
            
            # Verdict
            if score >= 60:
                verdict = "🟢 Signal fort — tendance saine"
            elif score >= 40:
                verdict = "🟡 Signal modéré — surveiller"
            elif score >= 26:
                verdict = "🟠 Signal faible — proche du seuil"
            else:
                verdict = "🔴 Signal éteint — essoufflement technique"
            
            stop_p = price * (1 - atr_pct * 2.0)
            tp_p   = price * (1 + tp_dynamic)
            
            return (
                f"🔍 <b>Analyse — {ticker}</b> | Régime {regime}\n"
                f"{pos_info}\n"
                f"📈 Score technique : <b>{score:.0f}/100</b>\n"
                f"   {verdict}\n\n"
                f"📐 Volatilité (ATR) : {atr_pct*100:.1f}%\n"
                f"🛑 Stop suggéré : {stop_p:.2f} (-{atr_pct*2*100:.1f}%)\n"
                f"🎯 TP suggéré : {tp_p:.2f} (+{tp_dynamic*100:.0f}%)\n\n"
                f"💡 {'Conserver' if score >= 26 else 'Envisager une sortie — signal sous le seuil minimum'}"
            )
        except Exception as e:
            return f"❌ Erreur analyse {ticker} : {e}"
    elif cmd == "/status":
        from config import FULL_UNIVERSE
        core_count = len(state.get("core", {}))
        sat_count  = len(state.get("satellite", {}))
        return (
            f"⚙️ *System Status*\n"
            f"Regime: {state.get('current_regime','?')}\n"
            f"Last run: {state.get('last_run','never')}\n"
            f"Core positions: {core_count}\n"
            f"Satellite positions: {sat_count}\n"
            f"Total positions: {len(state.get('positions',{}))}\n"
            f"Cash: {state.get('cash_eur',0):.0f} EUR\n"
            f"Universe size: {len(FULL_UNIVERSE)} tickers"
        )

    elif cmd == "/regime":
        from regime import detect_regime
        r      = detect_regime()
        cac    = r["cac40"]
        stoxx  = r["stoxx600"]
        cur    = r["regime"]
        params = REGIME_PARAMS[cur]
        return (
            f"\U0001f4ca *Regime Analysis*\nCurrent regime: *{cur}*\n\n"
            f"CAC40: {cac['last_close']:.0f} | MA50: {cac['ma50']:.0f} | MA200: {cac['ma200']:.0f}\n"
            f"Detail: {cac['detail']}\n\nStoxx600: {stoxx['regime']} | {stoxx['detail']}\n\n"
            f"*Parameters for {cur}:*\n"
            f"- Score threshold: {params['score_threshold']}\n"
            f"- Beta target: {params['beta_target_min']}–{params['beta_target_max']}\n"
            f"- Max lines: {params['max_lines']}\n"
            f"- Stop: {params['stop_loss_pct']:.0%} | TP: {params['take_profit_pct']:.0%}\n"
            f"- Cash: {params['cash_pct_min']:.0%}–{params['cash_pct_max']:.0%}"
        )

    elif cmd == "/sensi":
        positions = state.get("positions", {})
        pb        = portfolio_beta(positions)
        sectors   = sector_exposure(positions)
        total_val = sum(p.get("position_eur", 0) for p in positions.values()) + state.get("cash_eur", 0)
        var_99    = total_val * 2.33 * pb * 0.01 if pb > 0 else 0
        sec_str   = "\n".join(f"  {s}: {w:.1%}" for s, w in sectors.items()) or "  (aucune position)"
        return (
            f"\U0001f4d0 *Sensibilité du portefeuille*\n"
            f"Beta global: {pb:.2f}\nVaR 99% journalière: {var_99:.0f} EUR\n"
            f"Valeur totale: {total_val:.0f} EUR\n\n*Exposition par secteur:*\n{sec_str}"
        )

    elif cmd == "/perf":
        positions = state.get("positions", {})
        perf      = state.get("performance", {})
        initial   = state.get("initial_capital", INITIAL_CAPITAL)
        cash      = state.get("cash_eur", 0)
        total_pnl = perf.get("total_pnl_eur", 0)
        pnl_pct   = perf.get("total_pnl_pct", 0)
        lines     = ["\U0001f4ca *Performance du portefeuille*\n"]
        for ticker, pos in positions.items():
            entry    = pos.get("entry_price", 0)
            price    = pos.get("current_price", entry)
            currency = pos.get("currency", "EUR")
            eur_usd  = pos.get("eur_usd", 1.12)
            price_eur = price / eur_usd if currency == "USD" else price
            entry_eur = entry / eur_usd if currency == "USD" else entry
            pnl_eur   = (price_eur - entry_eur) * pos.get("nb_shares", 0)
            pnl_pct_pos = (price - entry) / entry if entry else 0
            sign = "✅" if pnl_eur >= 0 else "\U0001f534"
            lines.append(f"{sign} {ticker}: {pnl_eur:+.0f}€ ({pnl_pct_pos:+.1%})")
        lines.append(f"\n*Total P&L:* {total_pnl:+.2f} EUR ({pnl_pct:+.1%})")
        lines.append(f"Capital initial: {initial:.0f} EUR\nCash: {cash:.0f} EUR")
        return "\n".join(lines)

    elif cmd == "/risk":
        positions = state.get("positions", {})
        if not positions:
            return "Aucune position ouverte."
        lines = ["⚠️ *Distances stop/TP par position*\n"]
        for ticker, pos in positions.items():
            entry    = pos.get("entry_price", 0)
            stop     = pos.get("stop_loss", 0)
            tp       = pos.get("take_profit", 0)
            price    = pos.get("current_price", entry)
            currency = pos.get("currency", "EUR")
            eur_usd  = pos.get("eur_usd", 1.12)
            sym      = "$" if currency == "USD" else "€"
            price_eur = price / eur_usd if currency == "USD" else price
            stop_eur  = stop / eur_usd if currency == "USD" else stop
            tp_eur    = tp / eur_usd if currency == "USD" else tp
            dist_stop = (price - stop) / price if price else 0
            dist_tp   = (tp - price) / price if price else 0
            eur_risk  = (price_eur - stop_eur) * pos.get("nb_shares", 0)
            eur_tp    = (tp_eur - price_eur) * pos.get("nb_shares", 0)
            lines.append(
                f"*{ticker}*\n  Prix: {price:.2f}{sym} | Stop: {stop:.2f}{sym} | TP: {tp:.2f}{sym}\n"
                f"  Dist. stop: -{dist_stop:.1%} ({-eur_risk:.0f}€)\n"
                f"  Dist. TP:   +{dist_tp:.1%} (+{eur_tp:.0f}€)"
            )
        return "\n\n".join(lines)

    elif cmd == "/top5":
        last_scores = state.get("last_scores", [])
        if not last_scores:
            return "Aucun score disponible. Lancez d'abord un run."
        lines  = ["\U0001f3c6 *Top 5 tickers du dernier run*\n"]
        medals = ["\U0001f947","\U0001f948","\U0001f949","4️⃣","5️⃣"]
        for i, s in enumerate(last_scores[:5]):
            bd = s.get("breakdown", {})
            lines.append(
                f"{medals[i]} *{s['ticker']}* — Score: {s['score']:.1f}/100\n"
                f"   Tech: {s['tech_score']:.1f} | Beta: {s['beta']:.2f}\n"
                f"   Trend:{bd.get('trend',0):.0f} RSI:{bd.get('rsi',0):.0f} "
                f"MACD:{bd.get('macd',0):.0f} Boll:{bd.get('bollinger',0):.0f} StRSI:{bd.get('stoch_rsi',0):.0f}"
            )
        return "\n\n".join(lines)

    elif cmd == "/explain":
        if len(parts) < 2:
            return "Usage: /explain <TICKER>"
        target      = parts[1].upper()
        last_scores = state.get("last_scores", [])
        match       = next((s for s in last_scores if s["ticker"] == target), None)
        if not match:
            return f"{target} non trouvé dans le dernier run."
        bd   = match.get("breakdown", {})
        sigs = match.get("signals_tech", [])
        return (
            f"\U0001f50d *Analyse complète — {target}*\n"
            f"Score global: *{match['score']:.1f}/100*\n"
            f"Tech: {match['tech_score']:.1f} | Fund: {match['fund_score']:.1f} | Bonus: {match.get('regime_bonus',0):.0f}\n\n"
            f"*Détail technique:*\n"
            f"  Trend: {bd.get('trend',0):.1f}/10  RSI: {bd.get('rsi',0):.1f}/6\n"
            f"  Volume: {bd.get('volume',0):.1f}/8  MACD: {bd.get('macd',0):.1f}/6\n"
            f"  Momentum: {bd.get('momentum',0):.1f}/6  Bollinger: {bd.get('bollinger',0):.1f}/8\n"
            f"  StochRSI: {bd.get('stoch_rsi',0):.1f}/6  ATR: {(match.get('atr_pct') or 0)*100:.2f}%\n\n"
            f"*Signaux:*\n" + "\n".join(f"  • {s}" for s in sigs[:6])
        )

    elif cmd == "/cash":
        cash     = state.get("cash_eur", 0)
        initial  = state.get("initial_capital", INITIAL_CAPITAL)
        pos_val  = sum(p.get("position_eur", 0) for p in state.get("positions", {}).values())
        total    = pos_val + cash
        core_n   = len(state.get("core", {}))
        sat_n    = len(state.get("satellite", {}))
        return (
            f"\U0001f4b0 *Capital disponible*\n"
            f"Cash: {cash:.2f} EUR ({cash/total:.1%})\n"
            f"Positions: {pos_val:.2f} EUR ({pos_val/total:.1%})\n"
            f"Total estimé: {total:.2f} EUR\nCapital initial: {initial:.2f} EUR\n"
            f"Core: {core_n} positions | Satellite: {sat_n} positions"
        )

    elif cmd == "/alert":
        if len(parts) < 3:
            return "Usage: /alert <TICKER> <PRIX>"
        ticker_a = parts[1].upper()
        try:
            target_price = float(parts[2])
        except ValueError:
            return "Prix invalide."
        state.setdefault("price_alerts", {})[ticker_a] = target_price
        save_state(state)
        return f"\U0001f514 Alerte créée : *{ticker_a}* @ {target_price:.2f} EUR"

    elif cmd == "/pause":
        state["signals_paused"] = True
        save_state(state)
        return "⏸ Signaux automatiques *mis en pause*."

    elif cmd == "/resume":
        state["signals_paused"] = False
        save_state(state)
        return "▶️ Signaux automatiques *repris*."

    else:
        return (
            "Commandes disponibles:\n"
            "/run — déclencher un run immédiat\n"
            "/bought <TICKER> <NB> <PRICE> — satellite\n"
            "/boughtcore <TICKER> <NB> <PRICE> — core\n"
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
            "/check <TICKER> — analyse technique complète d'un titre"
            "/pause — suspendre les signaux\n"
            "/resume — reprendre les signaux"
        )
