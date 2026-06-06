"""
scorer.py — Scoring model: 0-100 composite score (50 tech + 50 fundamental).
Gracefully handles missing data (0 pts for that sub-component, never crash).
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from config import (
    SCORE_TECH_TREND_MAX, SCORE_TECH_RSI_MAX, SCORE_TECH_VOLUME_MAX,
    SCORE_TECH_MACD_MAX, SCORE_TECH_MOMENTUM_MAX,
    SCORE_FUND_EPS_REVISIONS_MAX, SCORE_FUND_VALUATION_MAX,
    SCORE_FUND_BALANCE_SHEET_MAX, SCORE_FUND_GROWTH_MAX,
    RSI_THRESHOLDS, VOLUME_HIGH_MULT, VOLUME_MED_MULT,
    MOMENTUM_LOOKBACK_MONTHS, EPS_REVISION_LOOKBACK_DAYS,
    PEG_GOOD_BULL, DEBT_EBITDA_MAX,
    BULL_BETA_BONUS_THRESHOLD, BEAR_BETA_BONUS_THRESHOLD, REGIME_BONUS_PTS,
)

logger = logging.getLogger(__name__)


# ─── Technical Helpers ────────────────────────────────────────────────────────

def _rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI for a price series."""
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return float(100 - 100 / (1 + rs.iloc[-1]))


def _macd_signal(closes: pd.Series) -> str:
    """Returns 'bullish', 'neutral', or 'bearish' MACD signal."""
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal
    if len(hist) < 2:
        return "neutral"
    # Bullish: histogram turned positive (crossover)
    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        return "bullish_crossover"
    if hist.iloc[-1] > 0:
        return "bullish"
    if hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
        return "bearish_crossover"
    return "bearish"


# ─── Technical Block ──────────────────────────────────────────────────────────

def score_trend(closes: pd.Series) -> tuple[float, list[str]]:
    """MA20 > MA50 > MA200 alignment. Max 15 pts (5 per level confirmed)."""
    signals = []
    pts = 0.0
    if len(closes) < 200:
        return 0.0, ["Insufficient history for trend scoring"]

    ma20  = float(closes.rolling(20).mean().iloc[-1])
    ma50  = float(closes.rolling(50).mean().iloc[-1])
    ma200 = float(closes.rolling(200).mean().iloc[-1])
    last  = float(closes.iloc[-1])

    if last > ma20:
        pts += 5; signals.append(f"Price > MA20 ({ma20:.2f})")
    if ma20 > ma50:
        pts += 5; signals.append(f"MA20 > MA50 ({ma50:.2f})")
    if ma50 > ma200:
        pts += 5; signals.append(f"MA50 > MA200 ({ma200:.2f})")

    return pts, signals


def score_rsi(closes: pd.Series, regime: str) -> tuple[float, list[str]]:
    """RSI 14d scoring based on regime thresholds. Max 10 pts."""
    signals = []
    try:
        rsi = _rsi(closes)
        th  = RSI_THRESHOLDS[regime]
        pts = 0.0

        if th["min"] <= rsi <= th["max"]:
            pts = SCORE_TECH_RSI_MAX
            signals.append(f"RSI {rsi:.1f} in ideal range [{th['min']}-{th['max']}]")
        elif rsi < th["oversold"]:
            pts = SCORE_TECH_RSI_MAX * 0.8  # oversold bonus
            signals.append(f"RSI {rsi:.1f} oversold (bonus)")
        elif rsi > th["overbought"]:
            pts = 0.0
            signals.append(f"RSI {rsi:.1f} overbought (penalty)")
        else:
            # Partial credit for being somewhat in range
            dist_from_ideal = min(abs(rsi - th["min"]), abs(rsi - th["max"]))
            pts = max(0, SCORE_TECH_RSI_MAX * (1 - dist_from_ideal / 20))
            signals.append(f"RSI {rsi:.1f} partial")

        return pts, signals
    except Exception as e:
        logger.debug(f"RSI scoring error: {e}")
        return 0.0, ["RSI unavailable"]


def score_volume(hist: pd.DataFrame) -> tuple[float, list[str]]:
    """Volume vs 20d average. Max 10 pts."""
    try:
        avg_vol = hist["Volume"].tail(20).mean()
        cur_vol = float(hist["Volume"].iloc[-1])
        ratio   = cur_vol / avg_vol if avg_vol > 0 else 0

        if ratio >= VOLUME_HIGH_MULT:
            return SCORE_TECH_VOLUME_MAX, [f"Volume {ratio:.1f}x avg (strong confirmation)"]
        elif ratio >= VOLUME_MED_MULT:
            pts = SCORE_TECH_VOLUME_MAX * 0.6
            return pts, [f"Volume {ratio:.1f}x avg (moderate confirmation)"]
        else:
            return 0.0, [f"Volume {ratio:.1f}x avg (weak)"]
    except Exception as e:
        logger.debug(f"Volume scoring error: {e}")
        return 0.0, ["Volume data unavailable"]


def score_macd(closes: pd.Series) -> tuple[float, list[str]]:
    """MACD signal scoring. Max 8 pts."""
    signal = _macd_signal(closes)
    if signal == "bullish_crossover":
        return SCORE_TECH_MACD_MAX, ["MACD bullish crossover"]
    elif signal == "bullish":
        return SCORE_TECH_MACD_MAX * 0.6, ["MACD bullish (above signal)"]
    elif signal == "bearish_crossover":
        return 0.0, ["MACD bearish crossover (penalty)"]
    else:
        return 0.0, ["MACD bearish"]


def score_momentum(ticker: str, closes: pd.Series, universe_returns: list[float]) -> tuple[float, list[str]]:
    """3M momentum vs universe median. Top quartile = full pts. Max 7 pts."""
    try:
        lookback = MOMENTUM_LOOKBACK_MONTHS * 21  # approx trading days
        if len(closes) < lookback + 1:
            return 0.0, ["Insufficient history for momentum"]

        ret_3m = float(closes.iloc[-1] / closes.iloc[-lookback] - 1)
        median  = float(np.median(universe_returns)) if universe_returns else 0
        q75     = float(np.percentile(universe_returns, 75)) if universe_returns else 0

        if ret_3m >= q75:
            return SCORE_TECH_MOMENTUM_MAX, [f"3M return {ret_3m:.1%} top quartile"]
        elif ret_3m >= median:
            pts = SCORE_TECH_MOMENTUM_MAX * 0.5
            return pts, [f"3M return {ret_3m:.1%} above median"]
        else:
            return 0.0, [f"3M return {ret_3m:.1%} below median"]
    except Exception as e:
        logger.debug(f"Momentum scoring error: {e}")
        return 0.0, ["Momentum unavailable"]


# ─── Fundamental Block ────────────────────────────────────────────────────────

def score_eps_revisions(ticker_obj) -> tuple[float, list[str]]:
    """Analyst EPS upgrades in last 30 days. Max 15 pts."""
    try:
        recs = ticker_obj.recommendations
        if recs is None or recs.empty:
            return 0.0, ["No analyst recommendations data"]

        cutoff = datetime.now() - timedelta(days=EPS_REVISION_LOOKBACK_DAYS)
        # Filter recent recommendations
        if hasattr(recs.index, "to_pydatetime"):
            recent = recs[recs.index >= cutoff]
        else:
            recent = recs.tail(10)

        if recent.empty:
            return 0.0, ["No recent analyst activity"]

        # Count upgrades vs downgrades
        upgrade_actions   = {"upgrade", "initiated", "resumed", "reiterated"}
        downgrade_actions = {"downgrade", "lowered"}

        upgrades   = 0
        downgrades = 0
        for col in ["Action", "action"]:
            if col in recent.columns:
                actions = recent[col].str.lower().fillna("")
                upgrades   = actions.str.contains("|".join(upgrade_actions)).sum()
                downgrades = actions.str.contains("|".join(downgrade_actions)).sum()
                break

        net_score = upgrades - downgrades
        if net_score >= 3:
            pts = SCORE_FUND_EPS_REVISIONS_MAX
        elif net_score == 2:
            pts = SCORE_FUND_EPS_REVISIONS_MAX * 0.8
        elif net_score == 1:
            pts = SCORE_FUND_EPS_REVISIONS_MAX * 0.5
        elif net_score == 0:
            pts = SCORE_FUND_EPS_REVISIONS_MAX * 0.3
        else:
            pts = 0.0

        return pts, [f"Analyst net revisions: {net_score:+d} ({upgrades} up, {downgrades} down)"]
    except Exception as e:
        logger.debug(f"EPS revision scoring error: {e}")
        return 0.0, ["EPS revision data unavailable"]


def score_valuation(info: dict, regime: str) -> tuple[float, list[str]]:
    """P/E vs sector, PEG ratio. Max 15 pts."""
    try:
        pe  = info.get("trailingPE") or info.get("forwardPE")
        peg = info.get("pegRatio")

        if pe is None:
            return 0.0, ["P/E not available"]

        pts     = 0.0
        signals = []

        if regime == "BULL":
            # Growth premium accepted; use PEG
            if peg is not None:
                if peg < 1.0:
                    pts = SCORE_FUND_VALUATION_MAX
                    signals.append(f"PEG {peg:.2f} — excellent value for growth")
                elif peg < PEG_GOOD_BULL:
                    pts = SCORE_FUND_VALUATION_MAX * 0.7
                    signals.append(f"PEG {peg:.2f} — acceptable growth premium")
                elif peg < 2.5:
                    pts = SCORE_FUND_VALUATION_MAX * 0.4
                    signals.append(f"PEG {peg:.2f} — elevated but tolerable in BULL")
                else:
                    pts = 0.0
                    signals.append(f"PEG {peg:.2f} — too expensive")
            else:
                # Fallback to P/E
                if pe < 15:
                    pts = SCORE_FUND_VALUATION_MAX
                elif pe < 25:
                    pts = SCORE_FUND_VALUATION_MAX * 0.6
                elif pe < 40:
                    pts = SCORE_FUND_VALUATION_MAX * 0.3
                else:
                    pts = 0.0
                signals.append(f"P/E {pe:.1f} (PEG unavailable)")
        else:
            # BEAR/NEUTRAL: value focus, P/E discount required
            if pe < 12:
                pts = SCORE_FUND_VALUATION_MAX
                signals.append(f"P/E {pe:.1f} — deep value")
            elif pe < 18:
                pts = SCORE_FUND_VALUATION_MAX * 0.7
                signals.append(f"P/E {pe:.1f} — reasonable value")
            elif pe < 25:
                pts = SCORE_FUND_VALUATION_MAX * 0.3
                signals.append(f"P/E {pe:.1f} — fair")
            else:
                pts = 0.0
                signals.append(f"P/E {pe:.1f} — too expensive for {regime} regime")

        return pts, signals
    except Exception as e:
        logger.debug(f"Valuation scoring error: {e}")
        return 0.0, ["Valuation data unavailable"]


def score_balance_sheet(info: dict) -> tuple[float, list[str]]:
    """Debt/EBITDA + FCF yield. Max 10 pts."""
    try:
        pts     = 0.0
        signals = []

        total_debt  = info.get("totalDebt", 0) or 0
        ebitda      = info.get("ebitda", 1) or 1
        free_cf     = info.get("freeCashflow", 0) or 0
        market_cap  = info.get("marketCap", 1) or 1

        debt_ebitda = total_debt / ebitda if ebitda != 0 else 99
        fcf_yield   = free_cf / market_cap if market_cap != 0 else 0

        # Debt/EBITDA scoring (5 pts)
        if debt_ebitda < 1.0:
            pts += 5; signals.append(f"D/EBITDA {debt_ebitda:.1f} — strong balance sheet")
        elif debt_ebitda < DEBT_EBITDA_MAX:
            pts += 3; signals.append(f"D/EBITDA {debt_ebitda:.1f} — acceptable")
        else:
            signals.append(f"D/EBITDA {debt_ebitda:.1f} — leveraged")

        # FCF yield scoring (5 pts)
        if fcf_yield > 0.06:
            pts += 5; signals.append(f"FCF yield {fcf_yield:.1%} — excellent")
        elif fcf_yield > 0.03:
            pts += 3; signals.append(f"FCF yield {fcf_yield:.1%} — good")
        elif fcf_yield > 0:
            pts += 1; signals.append(f"FCF yield {fcf_yield:.1%} — positive")
        else:
            signals.append(f"FCF yield {fcf_yield:.1%} — negative/unavailable")

        return pts, signals
    except Exception as e:
        logger.debug(f"Balance sheet scoring error: {e}")
        return 0.0, ["Balance sheet data unavailable"]


def score_growth(ticker_obj, info: dict) -> tuple[float, list[str]]:
    """Revenue + EPS 3Y CAGR. Max 10 pts."""
    try:
        pts     = 0.0
        signals = []

        # Revenue growth from financials
        try:
            fin = ticker_obj.financials
            if fin is not None and not fin.empty and "Total Revenue" in fin.index:
                revenues = fin.loc["Total Revenue"].dropna().sort_index()
                if len(revenues) >= 3:
                    rev_cagr = float((revenues.iloc[-1] / revenues.iloc[-3]) ** (1/3) - 1)
                    if rev_cagr > 0.15:
                        pts += 5; signals.append(f"Revenue 3Y CAGR {rev_cagr:.1%} — strong")
                    elif rev_cagr > 0.07:
                        pts += 3; signals.append(f"Revenue 3Y CAGR {rev_cagr:.1%} — good")
                    elif rev_cagr > 0:
                        pts += 1; signals.append(f"Revenue 3Y CAGR {rev_cagr:.1%} — modest")
                    else:
                        signals.append(f"Revenue declining: {rev_cagr:.1%}")
        except Exception:
            signals.append("Revenue CAGR unavailable")

        # EPS growth proxy from info
        eps_fwd = info.get("forwardEps")
        eps_ttm = info.get("trailingEps")
        if eps_fwd and eps_ttm and eps_ttm > 0:
            eps_growth = (eps_fwd - eps_ttm) / abs(eps_ttm)
            if eps_growth > 0.15:
                pts += 5; signals.append(f"EPS fwd growth {eps_growth:.1%} — strong")
            elif eps_growth > 0.07:
                pts += 3; signals.append(f"EPS fwd growth {eps_growth:.1%} — good")
            elif eps_growth > 0:
                pts += 1; signals.append(f"EPS fwd growth {eps_growth:.1%} — modest")
            else:
                signals.append(f"EPS declining: {eps_growth:.1%}")
        else:
            signals.append("EPS growth unavailable")

        return min(pts, SCORE_FUND_GROWTH_MAX), signals
    except Exception as e:
        logger.debug(f"Growth scoring error: {e}")
        return 0.0, ["Growth data unavailable"]


# ─── Main Scorer ──────────────────────────────────────────────────────────────

def score_ticker(
    ticker: str,
    regime: str,
    universe_returns: list[float],
    beta: float,
) -> dict:
    """
    Compute full 0-100 score for a single ticker.
    Returns dict with score, breakdown, and signal lists.
    """
    result = {
        "ticker":     ticker,
        "score":      0.0,
        "tech_score": 0.0,
        "fund_score": 0.0,
        "regime_bonus": 0.0,
        "signals_tech": [],
        "signals_fund": [],
        "beta": beta,
        "error": None,
    }

    try:
        stock = yf.Ticker(ticker)
        hist  = stock.history(period="2y")
        info  = stock.info or {}

        if hist.empty or len(hist) < 50:
            result["error"] = "Insufficient price history"
            return result

        closes = hist["Close"].dropna()

        # ── Technical Block ──────────────────────────────────────────────────
        t_trend, s1 = score_trend(closes)
        t_rsi,   s2 = score_rsi(closes, regime)
        t_vol,   s3 = score_volume(hist)
        t_macd,  s4 = score_macd(closes)
        t_mom,   s5 = score_momentum(ticker, closes, universe_returns)

        tech_total = t_trend + t_rsi + t_vol + t_macd + t_mom
        tech_sigs  = s1 + s2 + s3 + s4 + s5

        # ── Fundamental Block ─────────────────────────────────────────────────
        f_eps,  s6 = score_eps_revisions(stock)
        f_val,  s7 = score_valuation(info, regime)
        f_bs,   s8 = score_balance_sheet(info)
        f_grow, s9 = score_growth(stock, info)

        fund_total = f_eps + f_val + f_bs + f_grow
        fund_sigs  = s6 + s7 + s8 + s9

        # ── Regime Bonus ──────────────────────────────────────────────────────
        bonus        = 0.0
        bonus_reason = ""
        if regime == "BULL" and beta >= BULL_BETA_BONUS_THRESHOLD:
            bonus        = REGIME_BONUS_PTS
            bonus_reason = f"High-beta stock in BULL regime (beta={beta:.2f})"
        elif regime == "BEAR" and beta < BEAR_BETA_BONUS_THRESHOLD:
            bonus        = REGIME_BONUS_PTS
            bonus_reason = f"Low-beta stock in BEAR regime (beta={beta:.2f})"

        total = min(100.0, tech_total + fund_total + bonus)

        result.update({
            "score":        total,
            "tech_score":   tech_total,
            "fund_score":   fund_total,
            "regime_bonus": bonus,
            "bonus_reason": bonus_reason,
            "signals_tech": tech_sigs,
            "signals_fund": fund_sigs,
            "breakdown": {
                "trend":     t_trend,
                "rsi":       t_rsi,
                "volume":    t_vol,
                "macd":      t_macd,
                "momentum":  t_mom,
                "eps_rev":   f_eps,
                "valuation": f_val,
                "balance":   f_bs,
                "growth":    f_grow,
            },
        })

    except Exception as e:
        logger.error(f"Scoring failed for {ticker}: {e}")
        result["error"] = str(e)

    return result


def score_universe(liquid_tickers: list[dict], regime: str, betas: dict) -> list[dict]:
    """
    Score all liquid tickers. Compute universe returns first for momentum ranking.
    Returns list of score dicts, sorted descending by score.
    """
    logger.info(f"Scoring {len(liquid_tickers)} tickers (regime={regime})...")

    # Pre-compute 3M returns for all tickers (for momentum percentile ranking)
    universe_returns = []
    returns_map      = {}

    for item in liquid_tickers:
        t = item["ticker"]
        try:
            hist = yf.download(t, period="6mo", progress=False, auto_adjust=True)
            if not hist.empty and len(hist) >= 63:
                closes = hist["Close"].dropna()
                ret    = float(closes.iloc[-1] / closes.iloc[-63] - 1)
                universe_returns.append(ret)
                returns_map[t] = ret
        except Exception:
            pass

    # Score each ticker
    scores = []
    for item in liquid_tickers:
        t    = item["ticker"]
        beta = betas.get(t, 1.0)
        res  = score_ticker(t, regime, universe_returns, beta)
        if res["error"] is None:
            scores.append(res)
        else:
            logger.debug(f"Skipping {t}: {res['error']}")

    scores.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"Scored {len(scores)} tickers successfully")
    return scores
