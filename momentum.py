"""
momentum.py — Production Portfolio Manager
Core + Satellite hybrid model
Config: core_n=5, ma=200, sat=33, atr=2.0, tp=0.28, hold=35d
"""
import json
import logging
import os
import requests
from datetime import datetime, date, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── CONFIG ──────────────────────────────────────────────────────────────────────
CAPITAL       = 1893.0
CORE_N        = 8
CORE_MOM_DAYS = 189
CORE_MA       = 200
REBAL_DAYS    = 42
SAT_THRESH    = 45
SAT_STOP_ATR  = 2.0
SAT_TP        = 0.13
SAT_HOLD_DAYS = 18
CORE_PCT      = 0.60
SAT_PCT       = 0.40
FEE           = 2.0
MAX_SAT       = 4
ROTATION_MIN_GAP = 0.08

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE     = "portfolio_state.json"

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}

CORE_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "AVGO", "ORCL", "CRM", "ADBE", "NOW", "PANW", "SNPS", "CDNS",
    "LMT", "RTX", "NOC", "GD",
    "LLY", "ABBV", "ISRG", "DXCM",
    "V", "MA", "GS", "MS", "JPM",
    "COST", "HD", "WMT", "PG",
    "XOM", "CVX",
    "INTC", "CSCO", "T", "VZ", "BAC", "F", "PYPL", "DIS", "NKE", "PFE",
    "KO", "PEP", "MRK", "ABT", "NEE", "PM", "UPS", "DHR",
    "SXR8.DE",
]

SATELLITE_UNIVERSE = [
    "SXRV.DE", "VVSM.DE", "SEC0.DE", "QDVE.DE",
    "NVDA", "AMD", "META", "TSLA",
    "COIN", "MSTR", "RIOT", "MARA",
    "PLTR", "SMCI", "HOOD", "SOFI",
    "HIMS", "DKNG", "AFRM",
    "CLSK", "HUT", "ASTS", "RKLB",
    "SOUN", "IONQ", "UPST",
    "0700.HK", "9988.HK", "3690.HK", "1810.HK", "0941.HK", "1299.HK",
    "1801.HK", "9866.HK", "2015.HK", "9618.HK", "1024.HK", "9888.HK",
    "9999.HK", "0285.HK", "6160.HK",
    "ASML.AS", "STM.PA", "CAP.PA", "DSY.PA",
    "ADYEN.AS", "BESI.AS", "ALO.PA", "RNO.PA",
    "CELH", "RDDT", "CAVA", "JOBY", "ACHR", "LUNR", "PONY",
    "RGTI", "QUBT", "KULR", "WULF", "CORZ", "BTBT", "CIFR", "EXPI", "GENIE",
    "9868.HK", "0020.HK", "9961.HK", "2382.HK", "2238.HK",
    "AIXA.DE", "EVT.DE", "MDXH.AS",
]

SATELLITE_BEAR = [
    "LMT", "RTX", "NOC", "GD",
    "XOM", "CVX",
    "WMT", "COST", "PG", "KO",
    "ABBV", "MRK",
    "SXR8.DE",
]

def market_of(ticker: str) -> str:
    if ticker.endswith(".HK"):
        return "🇭🇰 HK"
    if ticker.endswith((".PA", ".AS", ".MI", ".DE", ".F")):
        return "🇪🇺 EU"
    return "🇺🇸 US"

# ── TELEGRAM ────────────────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.info(f"TELEGRAM: {msg}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT,
            "text": msg,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        logger.error(f"Telegram error: {e}")


# ── YAHOO FINANCE ───────────────────────────────────────────────────────────────
def fetch_history(ticker, days=300):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        r = requests.get(url, headers=YF_HEADERS,
                         params={"interval": "1d", "range": "2y"}, timeout=15)
        if r.status_code != 200:
            return None
        data   = r.json()
        result = data.get("chart", {}).get("result")
        if not result:
            return None
        ts    = result[0]["timestamp"]
        quote = result[0]["indicators"]["quote"][0]
        closes  = quote.get("close", [])
        highs   = quote.get("high", [])
        lows    = quote.get("low", [])
        volumes = quote.get("volume", [])
        dates   = [datetime.utcfromtimestamp(t).date() for t in ts]
        data_clean = [(d,c,h,l,v) for d,c,h,l,v in
                      zip(dates,closes,highs,lows,volumes)
                      if c and h and l and v]
        if len(data_clean) < 50:
            return None
        dates_c   = [x[0] for x in data_clean]
        closes_c  = [x[1] for x in data_clean]
        highs_c   = [x[2] for x in data_clean]
        lows_c    = [x[3] for x in data_clean]
        volumes_c = [x[4] for x in data_clean]
        vol_last = volumes_c[-1] if volumes_c else 0
        logger.info(f"{ticker}: dernière date={dates_c[-1]}, close={closes_c[-1]:.2f}, vol={vol_last}")
        return {
            "dates":   dates_c,
            "closes":  closes_c,
            "highs":   highs_c,
            "lows":    lows_c,
            "volumes": volumes_c,
            "price":   closes_c[-1],
            "date":    dates_c[-1],
        }
    except Exception as e:
        logger.warning(f"{ticker}: {e}")
        return None


# ── INDICATEURS ─────────────────────────────────────────────────────────────────
def calc_momentum(closes, days):
    closes_r = list(reversed(closes))
    if len(closes_r) < days + 21:
        return None
    mom = (closes_r[21] - closes_r[days]) / closes_r[days]
    if len(closes_r) >= 63:
        rets = [(closes_r[j]-closes_r[j+1])/closes_r[j+1] for j in range(62)]
        vol  = (sum(r**2 for r in rets)/len(rets))**0.5 * (252**0.5)
        return mom / vol if vol > 0 else mom
    return mom


def calc_atr(highs, lows, closes, period=14):
    h = list(reversed(highs))
    l = list(reversed(lows))
    c = list(reversed(closes))
    if len(h) < period + 1:
        return 0.02
    tr_list = [max(h[i]-l[i],
                   abs(h[i]-c[i+1]),
                   abs(l[i]-c[i+1])) for i in range(period)]
    return sum(tr_list) / period / c[0] if c[0] > 0 else 0.02


def calc_rsi(closes, period=14):
    c = list(reversed(closes))
    if len(c) < period + 1:
        return 50.0
    deltas = [c[i] - c[i+1] for i in range(period)]
    gains  = sum(d for d in deltas if d > 0) / period
    losses = sum(-d for d in deltas if d < 0) / period
    rs     = gains / losses if losses != 0 else 100
    return 100 - 100 / (1 + rs)


def calc_ema(data, span):
    k = 2/(span+1)
    e = data[0]
    for p in data[1:]:
        e = p*k + e*(1-k)
    return e


def detect_regime(spy_data):
    closes = list(reversed(spy_data["closes"]))
    if len(closes) < 200:
        return "NEUTRAL"
    ma50  = sum(closes[:50]) / 50
    ma200 = sum(closes[:200]) / 200
    mom   = (closes[0]-closes[99])/closes[99] if len(closes) >= 100 else 0
    if ma50 > ma200*1.02 and mom > 0:
        return "BULL"
    elif ma50 < ma200*0.98 or mom < -0.05:
        return "BEAR"
    return "NEUTRAL"


def in_bear(spy_data):
    closes = list(reversed(spy_data["closes"]))
    if len(closes) < CORE_MA:
        return False
    ma = sum(closes[:CORE_MA]) / CORE_MA
    return closes[0] < ma


# ── SCORER SATELLITE HYBRIDE ────────────────────────────────────────────────────
def score_satellite(data, regime):
    closes  = list(reversed(data["closes"]))
    highs   = list(reversed(data["highs"]))
    lows    = list(reversed(data["lows"]))
    volumes = list(reversed(data["volumes"]))

    # Pour les marchés asiatiques fermés (volume J0 = 0), décaler sur J-1
    if data.get("dates") and len(volumes) > 1:
        last_date = data["dates"][-1]
        from datetime import date as _date
        days_since = (_date.today() - last_date).days
        if volumes[0] == 0 or days_since > 1:
            closes  = closes[1:]
            highs   = highs[1:]
            lows    = lows[1:]
            volumes = volumes[1:]

    if len(closes) < 50:
        return 0.0, 0.02, SAT_TP

    atr_pct = calc_atr(data["highs"], data["lows"], data["closes"])
    rsi     = calc_rsi(data["closes"])

    score = 0.0

    # ── 1. FILTRE TENDANCE LONG TERME — titre au-dessus MA200 (éliminatoire) ──
    if len(closes) >= 200:
        ma200 = sum(closes[:200]) / 200
        if closes[0] < ma200 * 0.95:
            return 0.0, atr_pct, SAT_TP

    # ── 2. REPLI DEPUIS SOMMET RÉCENT 20j (max 30 pts) ───────────────────────
    # Zone idéale : repli de 10-25% depuis le high récent
    if len(highs) >= 20:
        high_20j = max(highs[:20])
        if high_20j > 0:
            repli = (high_20j - closes[0]) / high_20j
            if 0.10 <= repli <= 0.25:
                score += 30.0   # zone idéale de pullback
            elif 0.05 <= repli < 0.10:
                score += 15.0   # repli léger — acceptable
            elif 0.25 < repli <= 0.35:
                score += 10.0   # repli fort — risqué mais potentiel
            elif repli > 0.35:
                return 0.0, atr_pct, SAT_TP  # effondrement — on évite
            # repli < 5% → pas encore retraité, trop proche du sommet → 0 pts

    # ── 3. RSI EN ZONE DE REBOND 35-55 (max 25 pts) ─────────────────────────
    if 35 <= rsi <= 45:
        score += 25.0   # oversold recovery — zone idéale
    elif 45 < rsi <= 55:
        score += 15.0   # neutre légèrement haussier
    elif 30 <= rsi < 35:
        score += 10.0   # très oversold — risque de continuation baissière
    elif rsi < 30:
        score += 5.0    # trop oversold — possible couteau qui tombe
    elif rsi > 70:
        score -= 15.0   # suracheté — trop tard

    # ── 4. VOLUME EN BAISSE SUR LE REPLI (max 20 pts) ───────────────────────
    # Signe que les vendeurs s'épuisent — accumulation silencieuse
    if len(volumes) >= 10:
        vol_recent = sum(volumes[:5]) / 5      # volume 5 derniers jours
        vol_older  = sum(volumes[5:10]) / 5    # volume 5 jours précédents
        if vol_older > 0:
            vol_ratio = vol_recent / vol_older
            if vol_ratio < 0.70:
                score += 20.0   # volume très en baisse sur repli → accumulation
            elif vol_ratio < 0.85:
                score += 12.0
            elif vol_ratio < 1.00:
                score += 6.0
            elif vol_ratio > 1.50:
                score -= 10.0   # volume en hausse sur baisse → distribution

    # ── 5. MACD QUI REMONTE DEPUIS LE BAS (max 15 pts) ──────────────────────
    if len(closes) >= 29:
        macd   = calc_ema(closes[:12], 12) - calc_ema(closes[:26], 26)
        sig    = calc_ema(closes[:9], 9)
        hist   = macd - sig
        macd_p = calc_ema(closes[3:15], 12) - calc_ema(closes[3:29], 26)
        sig_p  = calc_ema(closes[3:12], 9)
        hist_p = macd_p - sig_p
        if macd < 0 and hist > hist_p and hist > 0:
            score += 15.0   # croisement haussier depuis le bas — signal fort
        elif macd < 0 and hist > hist_p:
            score += 10.0   # histogramme qui remonte — début de retournement
        elif macd > 0 and hist > hist_p:
            score += 5.0    # tendance haussière confirmée
        elif macd < 0 and hist < hist_p:
            score -= 5.0    # momentum toujours baissier

    # ── 6. SUPPORT MA50 (max 10 pts) ─────────────────────────────────────────
    if len(closes) >= 50:
        ma50 = sum(closes[:50]) / 50
        dist_ma50 = (closes[0] - ma50) / ma50
        if -0.03 <= dist_ma50 <= 0.05:
            score += 10.0   # prix proche MA50 par le bas → support
        elif -0.08 <= dist_ma50 < -0.03:
            score += 6.0    # légèrement sous MA50 — zone de rebond possible
        elif dist_ma50 > 0.15:
            score -= 5.0    # trop au-dessus MA50 — extension

    # ── Calcul TP dynamique — retour au sommet récent + 10% ──────────────────
    price = closes[0]
    tp_dynamic = None

    if len(highs) >= 20:
        high_20j = max(highs[:20])
        if price < high_20j:
            tp_dynamic = (high_20j * 1.10 - price) / price

    if tp_dynamic is None:
        tp_dynamic = SAT_TP
    tp_dynamic = max(0.08, min(0.35, tp_dynamic))

    return min(100.0, max(0.0, score)), atr_pct, tp_dynamic


# ── STATE ───────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            if "last_rebal" in data:
                if "positions" not in data:
                    data["positions"] = {}
                return data
        except:
            pass
    return {
        "core":       {},
        "satellite":  {},
        "positions":  {},
        "last_rebal": None,
        "capital":    CAPITAL,
        "core_cash":  CAPITAL * CORE_PCT,
        "sat_cash":   CAPITAL * SAT_PCT,
        "last_run":   None,
    }


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ── CORE MOMENTUM ─────────────────────────────────────────────────────────
def run_core(state, spy_data):
    regime  = detect_regime(spy_data)
    bear    = in_bear(spy_data)
    today   = str(date.today())

    if bear and state["core"]:
        msg = f"🔴 <b>CORE — BEAR EXIT</b>\n"
        msg += f"📉 Régime : BEAR — SPY sous MA{CORE_MA}\n\n"
        msg += f"❌ <b>VENDRE IMMÉDIATEMENT :</b>\n"
        for ticker, pos in state["core"].items():
            msg += f"→ {ticker} (acheté à {pos['entry_price']:.2f}$)\n"
        msg += f"\n💰 Mettre le capital CORE en cash"
        send_telegram(msg)
        state["core"]      = {}
        state["last_rebal"] = today
        save_state(state)
        return

    if bear:
        return

    needs_rebal = False
    if not state["last_rebal"]:
        needs_rebal = True
    else:
        last = date.fromisoformat(state["last_rebal"])
        if (date.today() - last).days >= REBAL_DAYS:
            needs_rebal = True

    if not needs_rebal:
        logger.info(f"Core: pas de rebalancement (dernier: {state['last_rebal']})")
        return

    scores = []
    for ticker in CORE_UNIVERSE:
        data = fetch_history(ticker)
        if not data:
            continue
        closes = data["closes"]
        if len(closes) >= 200:
            closes_r = list(reversed(closes))
            ma200    = sum(closes_r[:200]) / 200
            if closes_r[0] < ma200 * 0.95:
                continue
        mom = calc_momentum(closes, CORE_MOM_DAYS)
        if mom is not None:
            scores.append((ticker, mom, data["price"]))

    scores.sort(key=lambda x: x[1], reverse=True)
    target = [s[0] for s in scores[:CORE_N]]

    current  = set(state["core"].keys())
    to_sell  = current - set(target)
    to_buy   = set(target) - current
    to_keep  = current & set(target)

    if not to_sell and not to_buy:
        logger.info("Core: aucun changement nécessaire")
        state["last_rebal"] = today
        save_state(state)
        return

    slot_size = (CAPITAL * CORE_PCT) / CORE_N

    msg = f"🔵 <b>CORE — Rotation (tous les {REBAL_DAYS} jours)</b>\n"
    msg += f"📊 Régime : {regime}\n\n"

    if to_sell:
        msg += f"❌ <b>VENDRE :</b>\n"
        for ticker in to_sell:
            pos = state["core"][ticker]
            msg += f"→ {ticker} (acheté à {pos['entry_price']:.2f}$)\n"
        msg += "\n"

    if to_keep:
        msg += f"✅ <b>GARDER (rien à faire) :</b>\n"
        for ticker in to_keep:
            msg += f"→ {ticker}\n"
        msg += "\n"

    if to_buy:
        cash_available = state.get("cash_eur", 0)
        msg += f"📈 <b>ACHETER :</b>\n"
        for ticker in to_buy:
            data = next((s for s in scores if s[0] == ticker), None)
            if data:
                price    = data[2]
                shares   = int(slot_size / price)
                invest   = shares * price
                msg += f"→ <b>{ticker}</b>\n"
                msg += f"   Prix : {price:.2f}$\n"
                msg += f"   Shares : {shares}\n"
                msg += f"   Investir : {invest:.0f}€\n"
                if cash_available < invest + 140:
                    sat_positions = state.get("satellite", {})
                    if sat_positions:
                        worst_ticker = min(
                            sat_positions.keys(),
                            key=lambda t: (
                                (fetch_history(t) or {}).get("price", sat_positions[t]["entry_price"])
                                - sat_positions[t]["entry_price"]
                            ) / sat_positions[t]["entry_price"]
                        )
                        msg += f"   ⚠️ Cash insuffisant ({cash_available:.0f}€)\n"
                        msg += f"   💡 Vends satellite <b>{worst_ticker}</b> pour financer\n"
                        msg += f"   👉 /sold {worst_ticker} {sat_positions[worst_ticker]['shares']} [prix]\n"
        msg += "\n"

    msg += f"💰 Budget par position : ~{slot_size:.0f}€\n"
    msg += f"📅 Prochain rebalancement dans {REBAL_DAYS} jours"

    send_telegram(msg)
    state["last_rebal"] = today
    save_state(state)


# ── SATELLITE SCAN ────────────────────────────────────────────────────────
def run_satellite(state, spy_data):
    regime   = detect_regime(spy_data)
    bear     = in_bear(spy_data)
    today    = str(date.today())
    universe = SATELLITE_BEAR if bear else SATELLITE_UNIVERSE

    core_tickers = set(state.get("core", {}).keys())
    MAX_PRICE    = 150

    # ── 1. SCAN DES CANDIDATS EN PREMIER ─────────────────────────────
    sat_scores = []
    for ticker in universe:
        if ticker in state.get("satellite", {}):
            continue
        if ticker in state.get("positions", {}):
            continue
        data = fetch_history(ticker)
        if not data:
            continue
        if data["price"] > MAX_PRICE:
            continue
        score, atr_pct, tp_dynamic = score_satellite(data, regime)
        if score >= SAT_THRESH:
            sat_scores.append((ticker, score, data["price"], atr_pct, tp_dynamic))
            logger.info(f"  {ticker}: score={score:.1f}")

    sat_scores.sort(key=lambda x: x[1], reverse=True)
    best_candidate = sat_scores[0] if sat_scores else None

    # ── 2. STOP-LOSS / TAKE-PROFIT / RELATIVE STRENGTH sur positions actives ──
    all_sat_tickers = [t for t in state.get("positions", {}).keys()
                       if t not in core_tickers]

    for ticker in all_sat_tickers:
        pos  = state["positions"][ticker]
        data = fetch_history(ticker)
        if not data:
            continue
        price     = data["price"]
        entry     = pos.get("entry_price", price)
        pnl       = (price - entry) / entry if entry else 0
        days_held = (date.today() - date.fromisoformat(pos.get("entry_date", today))).days
        currency  = pos.get("currency", "EUR")
        eur_usd   = pos.get("eur_usd", 1.12)
        price_eur = price / eur_usd if currency == "USD" else price
        entry_eur = entry / eur_usd if currency == "USD" else entry
        pnl_eur   = (price_eur - entry_eur) * pos.get("nb_shares", pos.get("shares", 1))

        sell = False; reason = ""
        if pnl >= SAT_TP:
            sell = True; reason = f"🎯 Take-profit ({pnl*100:.1f}%)"
        elif days_held >= SAT_HOLD_DAYS:
            sell = True; reason = f"⏱ Timeout ({days_held}j)"

        if not sell:
            # Faiblesse relative vs secteur sur 5 jours
            try:
                from config import SECTOR_MAP, SECTOR_ETF
                sector = SECTOR_MAP.get(ticker, "")
                sector_etf = SECTOR_ETF.get(sector, "SPY")
                hist_closes = list(reversed(data["closes"]))
                if len(hist_closes) >= 6:
                    perf_titre_5j = (hist_closes[0] - hist_closes[5]) / hist_closes[5]
                    etf_data = fetch_history(sector_etf)
                    if etf_data:
                        etf_closes = list(reversed(etf_data["closes"]))
                        if len(etf_closes) >= 6:
                            perf_etf_5j = (etf_closes[0] - etf_closes[5]) / etf_closes[5]
                            perf_relative = perf_titre_5j - perf_etf_5j
                            if perf_relative < -0.10:
                                sell = True
                                reason = (
                                    f"📉 Faiblesse relative vs {sector_etf} "
                                    f"({perf_titre_5j*100:+.1f}% vs secteur {perf_etf_5j*100:+.1f}%, "
                                    f"écart {perf_relative*100:+.1f}%)"
                                )
            except Exception as e:
                logger.warning(f"Relative strength calc failed for {ticker}: {e}")

        if sell:
            emoji = "🟢" if pnl_eur > 0 else "🔴"
            sym   = "$" if currency == "USD" else "€"
            nb    = pos.get("nb_shares", pos.get("shares", 1))
            msg   = f"{emoji} <b>SATELLITE — SUGGÈRE VENTE {ticker}</b> {market_of(ticker)}\n"
            msg  += f"Raison : {reason}\n"
            msg  += f"Prix entrée : {entry:.2f}{sym} → Prix actuel : {price:.2f}{sym}\n"
            msg  += f"P&L : {pnl*100:+.1f}% ({pnl_eur:+.0f}€)\n"
            msg  += f"Jours tenus : {days_held}j\n"
            msg  += f"👉 /sold {ticker} {nb} [prix_exec]"
            send_telegram(msg)

    # ── 3. CASH ET MESSAGE CANDIDATS ─────────────────────────────────
    cash_available  = state.get("cash_eur", 0)
    CASH_RESERVE    = 140
    cash_deployable = max(0, cash_available - CASH_RESERVE)
    has_cash        = cash_deployable >= 140

    if not sat_scores:
        logger.info("Satellite: aucun candidat éligible")
        return

    if not has_cash and not state.get("positions", {}):
        logger.info(f"Satellite: cash insuffisant ({cash_available:.0f}€) — pas de nouvel achat")
        return

    msg  = f"🟢 <b>SIGNAUX SATELLITE — Candidats classés par score</b>\n"
    msg += f"💰 Cash disponible : {cash_available:.0f}€ (déployable : {cash_deployable:.0f}€)\n\n"

    for ticker, score, price, atr_pct, tp_dynamic in sat_scores[:12]:
        shares   = int(cash_deployable / price) if cash_deployable > 0 else 0
        stop_p   = price * (1 - atr_pct * SAT_STOP_ATR)
        tp_p     = price * (1 + tp_dynamic)
        stop_pct = atr_pct * SAT_STOP_ATR * 100
        market   = market_of(ticker)

        if shares > 0:
            invest = shares * price
            msg += f"✅ <b>{ticker}</b> {market} — Score {score:.0f}/100\n"
            msg += f"   Prix : {price:.2f} | {shares} actions = {invest:.0f}€\n"
            msg += f"   Stop : {stop_p:.2f} (-{stop_pct:.1f}%) | TP : {tp_p:.2f} (+{tp_dynamic*100:.0f}%)\n\n"
        else:
            msg += f"🔒 <b>{ticker}</b> {market} — Score {score:.0f}/100 (prix : {price:.2f}, cash insuffisant)\n\n"

    # ── 4. LOGIQUE DE ROTATION ────────────────────────────────────────
    ROTATION_SCORE_THRESHOLD = SAT_THRESH + 10  # = 55

    weak_positions = []
    for sat_ticker, sat_pos in state.get("positions", {}).items():
        if sat_ticker in core_tickers:
            continue
        sat_data = fetch_history(sat_ticker)
        if not sat_data:
            continue
        sat_price     = sat_data["price"]
        sat_entry     = sat_pos.get("entry_price", sat_price)
        sat_pnl       = (sat_price - sat_entry) / sat_entry if sat_entry else 0
        sat_cur_score, _, sat_tp_dynamic = score_satellite(sat_data, regime)

        if sat_cur_score == 0 and sat_pnl > 0:
            continue

        if sat_cur_score < ROTATION_SCORE_THRESHOLD:
            weak_positions.append((sat_ticker, sat_pnl, sat_cur_score, sat_tp_dynamic))

    weak_positions.sort(key=lambda x: x[2])

    if weak_positions and sat_scores:
        rotation_lines = []
        seen_pairs     = set()

        for worst_ticker, worst_pnl, worst_score, worst_potential in weak_positions[:2]:
            for cand_ticker, cand_score, cand_price, cand_atr, cand_tp in sat_scores[:3]:
                if cand_ticker in state.get("positions", {}):
                    continue
                if cand_price > MAX_PRICE:
                    continue
                pair = (worst_ticker, cand_ticker)
                if pair in seen_pairs:
                    continue
                if cand_score > worst_score + 15:
                    seen_pairs.add(pair)
                    worst_pos      = state.get("positions", {}).get(worst_ticker, {})
                    worst_shares   = worst_pos.get("nb_shares", worst_pos.get("shares", 0))
                    worst_price    = worst_pos.get("current_price", 0)
                    worst_eur_usd  = worst_pos.get("eur_usd", 1.12)
                    worst_currency = worst_pos.get("currency", "EUR")
                    cash_freed     = worst_shares * (worst_price / worst_eur_usd if worst_currency == "USD" else worst_price)
                    total_cash     = cash_freed + cash_deployable
                    cand_shares    = int(total_cash / cand_price) if cand_price > 0 else 0
                    cand_invest    = cand_shares * cand_price

                    rotation_lines.append(
                        f"⚠️ <b>{worst_ticker}</b> {market_of(worst_ticker)} "
                        f"(P&L {worst_pnl*100:+.1f}%, score {worst_score:.0f}/100) "
                        f"vs <b>{cand_ticker}</b> {market_of(cand_ticker)} "
                        f"(score {cand_score:.0f}/100, potentiel +{cand_tp*100:.0f}%)\n"
                        f"💡 Vends {worst_ticker} ({worst_shares} actions) → "
                        f"achète {cand_ticker} : ~{cand_shares} actions = {cand_invest:.0f}€\n"
                    )

        if rotation_lines:
            msg += f"\n🔄 <b>SUGGESTIONS DE ROTATION</b>\n"
            msg += "\n".join(rotation_lines)
            msg += "\n"

    msg += f"\n⏱ Hold max : {SAT_HOLD_DAYS}j\n"
    msg += f"📌 Régime : {regime}"
    send_telegram(msg)


# ── MAIN ────────────────────────────────────────────────────────────────────────
def main():
    logger.info("=== Portfolio Manager — Production Run ===")

    spy_data = fetch_history("SPY")
    if not spy_data:
        send_telegram("❌ Erreur : impossible de charger SPY")
        return

    regime = detect_regime(spy_data)
    bear   = in_bear(spy_data)
    logger.info(f"Régime: {regime} | Bear: {bear}")

    state = load_state()

    run_core(state, spy_data)
    run_satellite(state, spy_data)

    core_tickers = set(state.get("core", {}).keys())
    all_positions = state.get("positions", {})
    core_count = len([t for t in all_positions if t in core_tickers])
    sat_count  = len([t for t in all_positions if t not in core_tickers])
    logger.info(f"Core: {core_count}/{CORE_N} | Satellite: {sat_count}/{MAX_SAT}")

    send_telegram(
        f"✅ <b>Run terminé</b>\n"
        f"📊 Régime : {regime}\n"
        f"🏦 Core : {core_count}/8 positions\n"
        f"🛰 Satellite : {sat_count}/4 positions\n"
        f"🕐 {datetime.now().strftime('%H:%M %d/%m/%Y')}"
    )


if __name__ == "__main__":
    main()
