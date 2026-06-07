"""
utils.py -- Shared utilities: fees, beta, sector, portfolio formatting.
Beta calculation uses yfinance for all markets (EU vs ^FCHI, US vs ^GSPC, HK vs ^HSI).
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf
from config import DEGIRO_FEES, SECTOR_MAP, INDEX_BY_MARKET

logger = logging.getLogger(__name__)


# ── Market identification ─────────────────────────────────────────────────────

def market_of(ticker: str) -> str:
    """Return 'EU', 'US', or 'HK' based on ticker suffix."""
    if ticker.endswith((".PA", ".AS", ".MI")):
        return "EU"
    if ticker.endswith(".HK"):
        return "HK"
    return "US"


# ── Fee calculations ──────────────────────────────────────────────────────────

def calculate_fee(position_eur: float, ticker: str = None) -> float:
    """
    Return estimated DEGIRO fee for one leg of a trade.
    Uses market-adaptive rates if ticker is provided; defaults to EU otherwise.
    """
    market = market_of(ticker) if ticker else "EU"
    fees = DEGIRO_FEES[market]
    return fees["fixed"] + fees["variable"] * position_eur


def calculate_roundtrip_fee(position_eur: float, ticker: str = None) -> float:
    return calculate_fee(position_eur, ticker) * 2


def roundtrip_fee_pct(position_eur: float, ticker: str = None) -> float:
    if position_eur <= 0:
        return 1.0
    return calculate_roundtrip_fee(position_eur, ticker) / position_eur


# ── Sector ────────────────────────────────────────────────────────────────────

def sector_of(ticker: str) -> str:
    return SECTOR_MAP.get(ticker, "Unknown")


# ── Beta calculation (yfinance, all markets) ──────────────────────────────────

def _yf_closes(tickers: list, period: str = "3mo") -> pd.DataFrame:
    """
    Download closing prices for a list of tickers via yfinance.
    Returns a DataFrame with tickers as columns, oldest-first.
    """
    if not tickers:
        return pd.DataFrame()
    download_arg = tickers if len(tickers) > 1 else tickers[0]
    try:
        raw = yf.download(download_arg, period=period, auto_adjust=True, progress=False)
        if raw.empty:
            return pd.DataFrame()
        if isinstance(raw.columns, pd.MultiIndex):
            return raw["Close"]
        else:
            # Single ticker — wrap as DataFrame
            return raw[["Close"]].rename(columns={"Close": tickers[0]})
    except Exception as e:
        logger.warning(f"yfinance download error for {tickers}: {e}")
        return pd.DataFrame()


def _beta_from_returns(stock_ret: pd.Series, market_ret: pd.Series) -> float:
    """OLS beta: cov(stock, market) / var(market)."""
    df = pd.DataFrame({"s": stock_ret, "m": market_ret}).dropna()
    if len(df) < 20:
        return 1.0
    cov = np.cov(df["s"], df["m"])[0, 1]
    var = np.var(df["m"])
    return round(float(cov / var) if var != 0 else 1.0, 3)


def compute_betas(tickers: list) -> dict:
    """
    Compute beta for each ticker vs its market index:
      EU -> ^FCHI (CAC40)
      US -> ^GSPC (S&P 500)
      HK -> ^HSI  (Hang Seng)
    All data fetched via yfinance in one batch per market group.
    """
    logger.info(f"Computing betas for {len(tickers)} tickers (yfinance)...")

    # Group tickers by market
    by_market: dict[str, list] = {"EU": [], "US": [], "HK": []}
    for t in tickers:
        by_market[market_of(t)].append(t)

    betas: dict[str, float] = {}

    for mkt, mkt_tickers in by_market.items():
        if not mkt_tickers:
            continue
        index_ticker = INDEX_BY_MARKET[mkt]
        all_dl = mkt_tickers + [index_ticker]

        logger.info(f"  {mkt}: {len(mkt_tickers)} stocks + {index_ticker}")
        closes = _yf_closes(all_dl)

        if closes.empty or index_ticker not in closes.columns:
            logger.warning(f"  {mkt}: could not fetch index {index_ticker}, defaulting to 1.0")
            for t in mkt_tickers:
                betas[t] = 1.0
            continue

        market_ret = closes[index_ticker].pct_change().dropna()

        for t in mkt_tickers:
            if t not in closes.columns:
                betas[t] = 1.0
                continue
            stock_ret = closes[t].pct_change().dropna()
            betas[t] = _beta_from_returns(stock_ret, market_ret)

    logger.info(f"Betas computed: {betas}")
    return betas


# ── Portfolio analytics ───────────────────────────────────────────────────────

def portfolio_beta(positions: dict) -> float:
    total_val = sum(p.get("position_eur", 0) for p in positions.values())
    if total_val == 0:
        return 0.0
    w_beta = sum(p.get("beta", 1.0) * p.get("position_eur", 0) for p in positions.values())
    return round(w_beta / total_val, 3)


def sector_exposure(positions: dict) -> dict:
    total_val = sum(p.get("position_eur", 0) for p in positions.values())
    if total_val == 0:
        return {}
    sector_eur: dict[str, float] = {}
    for ticker, pos in positions.items():
        s = sector_of(ticker)
        sector_eur[s] = sector_eur.get(s, 0) + pos.get("position_eur", 0)
    return {s: round(v / total_val, 3) for s, v in sector_eur.items()}


def format_portfolio_snapshot(state: dict) -> str:
    positions = state.get("positions", {})
    cash_eur  = state.get("cash_eur", 0)
    total_pnl = state.get("performance", {}).get("total_pnl_eur", 0)
    initial   = state.get("initial_capital", 1890)
    regime    = state.get("current_regime", "?")
    pb        = portfolio_beta(positions)
    sectors   = sector_exposure(positions)

    lines = [
        f"Portfolio | Regime: {regime}",
        f"Beta: {pb:.2f} | Cash: {cash_eur:.0f} EUR",
        f"P&L: {total_pnl:+.2f} EUR ({total_pnl/initial:+.1%})",
        "---",
    ]
    for ticker, pos in positions.items():
        entry   = pos.get("entry_price", 0)
        price   = entry
        pnl_pct = (price - entry) / entry if entry else 0
        pnl_eur = (price - entry) * pos.get("nb_shares", 0)
        lines.append(
            f"{ticker}: {pos['nb_shares']}x {entry:.2f} | {price:.2f} "
            f"({pnl_pct:+.1%} | {pnl_eur:+.0f}EUR)"
        )
    lines.append("Sectors: " + " | ".join(f"{s} {w:.0%}" for s, w in sectors.items()))
    return "\n".join(lines)
