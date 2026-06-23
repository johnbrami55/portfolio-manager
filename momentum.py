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

# ── CONFIG ────────────────────────────────────────────────────────────────────
CAPITAL       = 1893.0
CORE_N        = 8
CORE_MOM_DAYS = 189
CORE_MA       = 200
REBAL_DAYS    = 42
SAT_THRESH    = 26
SAT_STOP_ATR  = 2.0
SAT_TP        = 0.13
SAT_HOLD_DAYS = 18
CORE_PCT      = 0.60
SAT_PCT       = 0.40
FEE           = 2.0
MAX_SAT       = 4
ROTATION_MIN_GAP = 0.08  # écart minimum de potentiel pour suggérer une rotation

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE     = "portfolio_state.json"

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}

CORE_UNIVERSE = [
    # Large caps US accessibles <200$
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "AVGO", "ORCL", "CRM", "ADBE", "NOW", "PANW", "SNPS", "CDNS",
    "LMT", "RTX", "NOC", "GD",
    "LLY", "ABBV", "ISRG", "DXCM",
    "V", "MA", "GS", "MS", "JPM",
    "COST", "HD", "WMT", "PG",
    "XOM", "CVX",
    # Titres <150$ pour remplir les slots
    "INTC", "CSCO", "T", "VZ", "BAC", "F", "PYPL", "DIS", "NKE", "PFE",
    "KO", "PEP", "MRK", "ABT", "NEE", "PM", "UPS", "DHR",
    # ETF UCITS
    "SXR8.DE",
]

SATELLITE_UNIVERSE = [
    # ETFs UCITS Xetra
    "SXRV.DE", "VVSM.DE", "SEC0.DE", "QDVE.DE",
    # Actions US high beta
    "NVDA", "AMD", "META", "TSLA",
    "COIN", "MSTR", "RIOT", "MARA",
    "PLTR", "SMCI", "HOOD", "SOFI",
    "HIMS", "DKNG", "AFRM",
    "CLSK", "HUT", "ASTS", "RKLB",
    "SOUN", "IONQ", "UPST",
    # Hong Kong — high beta / volatile
    "0700.HK", "9988.HK", "3690.HK", "1810.HK", "0941.HK", "1299.HK",
    "1801.HK",  # Innovent Biologics
    "9866.HK",  # NIO
    "2015.HK",  # Li Auto
    "9618.HK",  # JD.com
    "1024.HK",  # Kuaishou
    "9888.HK",  # Baidu
    "9999.HK",  # NetEase
    "0285.HK",  # BYD Electronic
    "6160.HK",  # BeiGene
    # Europe — volatile / growth
    "ASML.AS", "STM.PA", "CAP.PA", "DSY.PA",
    "ADYEN.AS", # Adyen
    "BESI.AS",  # BE Semiconductor
    "ALO.PA",   # Alstom
    "RNO.PA",   # Renault
]
SATELLITE_BEAR = [
    # Défensif en régime BEAR — secteurs résilients, faible beta
    "LMT", "RTX", "NOC", "GD",      # Défense
    "XOM", "CVX",                    # Énergie
    "WMT", "COST", "PG", "KO",       # Consommation de base
    "ABBV", "MRK",                   # Santé
    "SXR8.DE",                       # ETF S&P 500 UCITS — défensif
]

def market_of(ticker: str) -> str:
    if ticker.endswith(".HK"):
        return "🇭🇰 HK"
    if ticker.endswith((".PA", ".AS", ".MI", ".DE")):
        return "🇪🇺 EU"
    return "🇺🇸 US"
    
# ── TELEGRAM ──────────────────────────────────────────────────────────────────
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


# ── YAHOO FINANCE ─────────────────────────────────────────────────────────────
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
        logger.info(f"{ticker}: dernière date={dates_c[-1]}, close={closes_c[-1]:.2f}")
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


# ── INDICATEURS ───────────────────────────────────────────────────────────────
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


# ── SCORER SATELLITE HYBRIDE ──────────────────────────────────────────────────
def score_satellite(data, regime):
    closes  = list(reversed(data["closes"]))
    highs   = list(reversed(data["highs"]))
    lows    = list(reversed(data["lows"]))
    volumes = list(reversed(data["volumes"]))

    if len(closes) < 50:
        return 0.0, 0.02, SAT_TP

    atr_pct = calc_atr(data["highs"], data["lows"], data["closes"])
    rsi     = calc_rsi(data["closes"])

    # Filtre tendance 6 mois (hors BULL)
    if regime != "BULL" and len(closes) >= 126:
        perf_6m = (closes[0] - closes[125]) / closes[125]
        if perf_6m < -0.20:
            return 0.0, atr_pct, SAT_TP

    score = 0.0

    if regime == "BULL":
        # ── MODE BULL : BREAKOUT MOMENTUM ────────────────────────────────
        # Filtre variation journalière — pas d'achat si +5% dans la journée
        if len(closes) >= 2:
            daily_change = (closes[0] - closes[1]) / closes[1]
            if daily_change > 0.05:
                return 0.0, atr_pct, SAT_TP

        # RSI fort (max 20 pts)
        if 55 <= rsi <= 75:    score += 20.0
        elif 50 <= rsi < 55:   score += 12.0
        elif rsi > 75:         score += 5.0
        elif rsi < 40:         score += 0.0
        else:                  score += 8.0

        # MAs alignées (max 20 pts)
        if len(closes) >= 200:
            ma20  = sum(closes[:20]) / 20
            ma50  = sum(closes[:50]) / 50
            ma200 = sum(closes[:200]) / 200
            if closes[0] > ma20 > ma50 > ma200:  score += 20.0
            elif closes[0] > ma50 > ma200:        score += 15.0
            elif closes[0] > ma200:               score += 8.0

        # Momentum récent (max 20 pts)
        if len(closes) >= 63:
            ret_1m = (closes[0] - closes[20]) / closes[20]
            ret_3m = (closes[0] - closes[62]) / closes[62]
            if ret_1m > 0.10:   score += 10.0
            elif ret_1m > 0.05: score += 6.0
            elif ret_1m > 0:    score += 3.0
            if ret_3m > 0.20:   score += 10.0
            elif ret_3m > 0.10: score += 6.0
            elif ret_3m > 0:    score += 3.0

        # Volume fort (max 15 pts)
        if len(volumes) >= 21:
            avg_vol   = sum(volumes[1:21]) / 20
            vol_ratio = volumes[0] / avg_vol if avg_vol > 0 else 1.0
            if vol_ratio >= 2.0:   score += 15.0
            elif vol_ratio >= 1.5: score += 10.0
            elif vol_ratio >= 1.2: score += 6.0

        # MACD positif (max 15 pts)
        if len(closes) >= 29:
            macd   = calc_ema(closes[:12],12) - calc_ema(closes[:26],26)
            sig    = calc_ema(closes[:9],9)
            hist   = macd - sig
            macd_p = calc_ema(closes[3:15],12) - calc_ema(closes[3:29],26)
            sig_p  = calc_ema(closes[3:12],9)
            hist_p = macd_p - sig_p
            if macd > 0 and hist > 0 and hist > hist_p: score += 15.0
            elif macd > 0 and hist > 0:                  score += 10.0
            elif macd > 0:                               score += 5.0

        # ATH — bonus si cassure avec volume, pénalité si essoufflé
        if len(closes) >= 252 and highs:
            high_52w = max(highs[:252])
            dist_ath = (high_52w - closes[0]) / high_52w
            if len(volumes) >= 21:
                avg_vol   = sum(volumes[1:21]) / 20
                vol_ratio = volumes[0] / avg_vol if avg_vol > 0 else 1.0
            else:
                vol_ratio = 1.0
            if dist_ath <= 0.05:
                if vol_ratio >= 1.5:   score += 10.0
                elif vol_ratio >= 1.2: score += 4.0
                else:                  score -= 10.0
            elif dist_ath <= 0.10:
                if vol_ratio >= 1.2:   score += 6.0
                else:                  score += 2.0
            elif dist_ath <= 0.20:     score += 3.0

    else:
        # ── MODE NEUTRAL/BEAR : CONTRARIAN RETRACEMENT ───────────────────

        # RSI oversold (max 25 pts)
        if rsi < 25:        score += 25.0
        elif rsi < 30:      score += 20.0
        elif rsi < 35:      score += 15.0
        elif rsi < 40:      score += 8.0
        elif rsi > 70:      score += 0.0
        elif rsi > 60:      score += 2.0
        else:               score += 5.0

        # Retracement depuis sommet (max 20 pts)
        if len(closes) >= 63:
            high_63 = max(highs[:63]) if highs else max(closes[:63])
            retrace = (high_63 - closes[0]) / high_63
            if retrace >= 0.30:   score += 20.0
            elif retrace >= 0.20: score += 15.0
            elif retrace >= 0.15: score += 10.0
            elif retrace >= 0.10: score += 5.0

        # Support MA200 (max 15 pts)
        if len(closes) >= 200:
            ma200      = sum(closes[:200]) / 200
            dist_ma200 = (closes[0] - ma200) / ma200
            if -0.05 <= dist_ma200 <= 0.05:   score += 15.0
            elif -0.10 <= dist_ma200 <= 0.10: score += 10.0
            elif dist_ma200 < -0.10:          score += 5.0

        # Fibonacci (max 15 pts)
        if len(closes) >= 126 and highs and lows:
            high_126 = max(highs[:126])
            low_126  = min(lows[:126])
            swing    = high_126 - low_126
            if swing > 0:
                tol = swing * 0.05
                fib_382 = high_126 - swing * 0.382
                fib_500 = high_126 - swing * 0.500
                fib_618 = high_126 - swing * 0.618
                price   = closes[0]
                if abs(price - fib_618) <= tol:   score += 15.0
                elif abs(price - fib_500) <= tol: score += 12.0
                elif abs(price - fib_382) <= tol: score += 10.0

        # Volume sur creux (max 15 pts)
        if len(volumes) >= 21:
            avg_vol   = sum(volumes[1:21]) / 20
            vol_ratio = volumes[0] / avg_vol if avg_vol > 0 else 1.0
            rsi_low   = rsi < 40
            if rsi_low and vol_ratio >= 1.5:   score += 15.0
            elif rsi_low and vol_ratio >= 1.2: score += 10.0
            elif rsi_low and vol_ratio >= 0.8: score += 5.0
            elif vol_ratio >= 1.5:             score += 5.0

        # MACD remonte depuis bas (max 10 pts)
        if len(closes) >= 29:
            macd   = calc_ema(closes[:12],12) - calc_ema(closes[:26],26)
            sig    = calc_ema(closes[:9],9)
            hist   = macd - sig
            macd_p = calc_ema(closes[3:15],12) - calc_ema(closes[3:29],26)
            sig_p  = calc_ema(closes[3:12],9)
            hist_p = macd_p - sig_p
            if macd < 0 and hist > hist_p:   score += 10.0
            elif macd < 0 and hist > 0:      score += 7.0
            elif macd > 0 and hist > hist_p: score += 3.0

        # Qualité du creux (max 30 pts)
        if len(highs) >= 10 and len(lows) >= 10:
            range_recent = sum(highs[i]-lows[i] for i in range(5)) / 5
            range_older  = sum(highs[i]-lows[i] for i in range(5,10)) / 5
            if range_older > 0:
                if range_recent < range_older * 0.7:   score += 10.0
                elif range_recent > range_older * 1.3: score -= 10.0

        if len(closes) >= 22:
            rsi_now  = calc_rsi(list(reversed(closes[:15])))
            rsi_prev = calc_rsi(list(reversed(closes[7:22])))
            if closes[0] < closes[7] and rsi_now > rsi_prev:
                score += 12.0
            elif closes[0] < closes[7] and rsi_now < rsi_prev:
                score -= 5.0

        if len(volumes) >= 10 and len(closes) >= 10:
            down_days_vol = []
            for j in range(min(10, len(closes)-1)):
                if closes[j] < closes[j+1]:
                    down_days_vol.append(volumes[j])
            if len(down_days_vol) >= 3:
                vol_trend = down_days_vol[0] / down_days_vol[-1] if down_days_vol[-1] > 0 else 1.0
                if vol_trend < 0.7:   score += 8.0
                elif vol_trend > 1.5: score -= 8.0

        # Filtre BEAR strict
        if regime == "BEAR":
            if len(closes) >= 200:
                ma200 = sum(closes[:200]) / 200
                if closes[0] < ma200:
                    return 0.0, atr_pct, SAT_TP
            score *= 0.5
            if rsi > 35:
                return 0.0, atr_pct, SAT_TP

    # ── Calcul du TP dynamique basé sur la structure technique ──────────
    price = closes[0]
    tp_dynamic = None

    if regime == "BULL":
        if len(closes) >= 252 and highs:
            high_52w = max(highs[:252])
            if price < high_52w:
                tp_dynamic = (high_52w * 1.10 - price) / price
            else:
                tp_dynamic = 0.15
    else:
        if len(closes) >= 63 and highs:
            high_63 = max(highs[:63])
            if price < high_63:
                tp_dynamic = (high_63 - price) / price

    if tp_dynamic is None:
        tp_dynamic = SAT_TP
    tp_dynamic = max(0.08, min(0.35, tp_dynamic))

    return min(100.0, score), atr_pct, tp_dynamic


# ── STATE ─────────────────────────────────────────────────────────────────────
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


# ── CORE MOMENTUM ─────────────────────────────────────────────────────────────
def run_core(state, spy_data):
    regime  = detect_regime(spy_data)
    bear    = in_bear(spy_data)
    today   = str(date.today())

    # Bear exit — vendre tout le core
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


# ── SATELLITE SCAN ────────────────────────────────────────────────────────────
def run_satellite(state, spy_data):
    regime  = detect_regime(spy_data)
    bear    = in_bear(spy_data)
    today   = str(date.today())
    universe = SATELLITE_BEAR if bear else SATELLITE_UNIVERSE

    # ── Stop-loss & take-profit sur positions actives ─────────────────────
    for ticker in list(state["satellite"].keys()):
        pos  = state["satellite"][ticker]
        data = fetch_history(ticker)
        if not data:
            continue
        price     = data["price"]
        entry     = pos["entry_price"]
        pnl       = (price - entry) / entry
        days_held = (date.today() - date.fromisoformat(pos["entry_date"])).days
        stop      = -pos["atr_pct"] * SAT_STOP_ATR
        pnl_eur   = pnl * pos["invested"]
        sell = False; reason = ""
        if pnl <= stop:
            sell = True; reason = f"🛑 Stop-loss ({pnl*100:.1f}%)"
        elif pnl >= SAT_TP:
            sell = True; reason = f"🎯 Take-profit ({pnl*100:.1f}%)"
        elif days_held >= SAT_HOLD_DAYS:
            sell = True; reason = f"⏱ Timeout ({days_held}j)"
        if sell:
            emoji = "🟢" if pnl > 0 else "🔴"
            msg   = f"{emoji} <b>SATELLITE — VENDRE {ticker}</b> {market_of(ticker)}\n"
            msg  += f"Raison : {reason}\n"
            msg  += f"Prix entrée : {entry:.2f} → Prix actuel : {price:.2f}\n"
            msg  += f"P&L : {pnl*100:+.1f}% ({pnl_eur:+.0f}€)\n"
            msg  += f"Jours tenus : {days_held}j"
            send_telegram(msg)
            del state["satellite"][ticker]
            save_state(state)

    # ── Nouvelles entrées ─────────────────────────────────────────────────
    active         = len(state["positions"])
    cash_available = state.get("cash_eur", 0)
    CASH_RESERVE   = 140
    cash_deployable = max(0, cash_available - CASH_RESERVE)

    # Même si pas assez de cash pour acheter, on continue pour la rotation
    has_cash = cash_deployable >= 140

    sat_scores = []
    for ticker in universe:
        if ticker in state["satellite"]:
            continue
        if ticker in state["positions"]:
            continue
        data = fetch_history(ticker)
        if not data:
            continue
        score, atr_pct, tp_dynamic = score_satellite(data, regime)
        if score >= SAT_THRESH:
            sat_scores.append((ticker, score, data["price"], atr_pct, tp_dynamic))
            logger.info(f"  {ticker}: score={score:.1f}")

    sat_scores.sort(key=lambda x: x[1], reverse=True)

    if not sat_scores:
        logger.info("Satellite: aucun candidat éligible")
        return

    # Si pas de cash ET pas de positions satellite à évaluer pour rotation → on sort
    if not has_cash and not state["satellite"]:
        logger.info(f"Satellite: cash insuffisant ({cash_available:.0f}€) — pas de nouvel achat")
        return

    # Construire un message unique avec TOUS les candidats triés par score
    msg  = f"🟢 <b>SIGNAUX SATELLITE — Candidats classés par score</b>\n"
    msg += f"💰 Cash disponible : {cash_available:.0f}€ (déployable : {cash_deployable:.0f}€)\n\n"

    locked_candidates = []  # candidats trop chers — utilisés pour la rotation

    for ticker, score, price, atr_pct, tp_dynamic in sat_scores[:12]:
        shares   = int(cash_deployable / price)
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
            msg += f"🔒 <b>{ticker}</b> {market} — Score {score:.0f}/100 (trop cher : {price:.2f}, besoin de {price-cash_deployable:.0f}€ de plus)\n\n"
            locked_candidates.append((ticker, score, price, atr_pct, tp_dynamic))

    # ── Logique de rotation ───────────────────────────────────────────────
    ROTATION_SCORE_THRESHOLD = SAT_THRESH + 10

    if locked_candidates and state["satellite"]:
        weak_positions = []
        for sat_ticker, sat_pos in state["satellite"].items():
            sat_data = fetch_history(sat_ticker)
            if not sat_data:
                continue
            sat_price = sat_data["price"]
            sat_entry = sat_pos["entry_price"]
            sat_pnl   = (sat_price - sat_entry) / sat_entry
            sat_cur_score, _, sat_tp_dynamic = score_satellite(sat_data, regime)

            if sat_cur_score < ROTATION_SCORE_THRESHOLD:
                weak_positions.append((sat_ticker, sat_pnl, sat_cur_score, sat_tp_dynamic))

        weak_positions.sort(key=lambda x: x[2])

        if weak_positions:
            worst_ticker, worst_pnl, worst_score, worst_potential = weak_positions[0]
            rotation_lines = []
            seen_candidates = set()
            for cand_ticker, cand_score, cand_price, cand_atr, cand_tp in locked_candidates[:3]:
                if cand_ticker in seen_candidates:
                    continue
                if cand_score > worst_score + 15:
                    seen_candidates.add(cand_ticker)
                    rotation_lines.append(
                        f"⚠️ <b>{worst_ticker}</b> {market_of(worst_ticker)} "
                        f"(P&L {worst_pnl*100:+.1f}%, score retombé à {worst_score:.0f}/100, "
                        f"potentiel restant +{worst_potential*100:.0f}%) montre des signes d'essoufflement\n"
                        f"   vs <b>{cand_ticker}</b> {market_of(cand_ticker)} "
                        f"(score {cand_score:.0f}/100, potentiel +{cand_tp*100:.0f}%)\n"
                        f"💡 Envisage de vendre {worst_ticker} pour libérer du cash et acheter {cand_ticker}\n"
                    )

            if rotation_lines:
                msg += f"\n🔄 <b>SUGGESTIONS DE ROTATION</b>\n"
                msg += "\n".join(rotation_lines)
                msg += "\n"

    msg += f"\n⏱ Hold max : {SAT_HOLD_DAYS}j\n"
    msg += f"📌 Régime : {regime}"
    send_telegram(msg)

# ── MAIN ──────────────────────────────────────────────────────────────────────
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

    # Core rotation
    run_core(state, spy_data)

    # Satellite scan
    run_satellite(state, spy_data)

    # Résumé
    core_count = len(state.get("core", {}))
    sat_count = len(state.get("satellite", {}))
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
