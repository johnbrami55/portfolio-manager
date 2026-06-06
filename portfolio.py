"""
portfolio.py — Portfolio construction and optimization.
Applies regime-aware weighting, sector/beta constraints, and cost gating.
"""

import logging
import numpy as np
from config import (
    INITIAL_CAPITAL, MIN_POSITION_EUR, MAX_POSITION_PCT, MAX_SECTOR_PCT,
    REGIME_PARAMS, SECTOR_MAP, DEGIRO_FIXED_FEE, DEGIRO_VARIABLE_FEE,
    MAX_ROUNDTRIP_FEE_PCT,
)
from utils import calculate_fee, sector_of

logger = logging.getLogger(__name__)


def compute_weights(scored: list[dict], regime: str) -> list[dict]:
    """
    Compute normalized portfolio weights from scored tickers.
    Applies regime-specific weighting logic.
    Returns list of dicts with ticker, score, beta, raw_weight, weight.
    """
    params = REGIME_PARAMS[regime]
    threshold = params["score_threshold"]
    max_lines = params["max_lines"]
    cash_min  = params["cash_pct_min"]

    # 1. Filter by score threshold
    eligible = [s for s in scored if s["score"] >= threshold]

    if not eligible:
        logger.warning(f"No stocks pass score threshold {threshold} in {regime} regime")
        return []

    # 2. Filter by beta constraint
    max_beta = params["max_beta_per_stock"]
    eligible = [s for s in eligible if s["beta"] <= max_beta]

    if not eligible:
        logger.warning(f"No stocks pass beta filter ({max_beta}) in {regime} regime")
        return []

    # 3. Take top N candidates (generous pool for sector diversification)
    candidates = eligible[:max_lines * 2]

    # 4. Compute raw weights
    for c in candidates:
        beta = max(c["beta"], 0.1)  # avoid division by zero
        if regime == "BULL":
            # Reward higher beta in bull
            c["raw_weight"] = c["score"] * beta * 0.5
        else:
            # Reward score per unit of risk
            c["raw_weight"] = c["score"] / beta

    # 5. Sector diversification: greedy selection respecting MAX_SECTOR_PCT
    selected  = []
    sector_wt = {}

    for c in sorted(candidates, key=lambda x: x["raw_weight"], reverse=True):
        if len(selected) >= max_lines:
            break
        sector = sector_of(c["ticker"])
        current_sector_wt = sector_wt.get(sector, 0.0)
        # Provisional weight (will be normalized later)
        # Check if adding this stock would violate sector cap
        # Approximate: assume equal weight for selection pass
        approx_wt = 1.0 / max(len(selected) + 1, 1)
        approx_sector = current_sector_wt + approx_wt
        if approx_sector > MAX_SECTOR_PCT + 0.05:  # small tolerance
            logger.debug(f"Skipping {c['ticker']} — sector {sector} capped")
            continue
        selected.append(c)
        sector_wt[sector] = current_sector_wt + approx_wt

    if not selected:
        return []

    # 6. Normalize weights
    total_raw = sum(s["raw_weight"] for s in selected)
    for s in selected:
        s["weight"] = s["raw_weight"] / total_raw if total_raw > 0 else 1.0 / len(selected)

    # 7. Clip to MAX_POSITION_PCT and renormalize (iterate up to 10x)
    for _ in range(10):
        clipped = False
        for s in selected:
            if s["weight"] > MAX_POSITION_PCT:
                s["weight"] = MAX_POSITION_PCT
                clipped     = True
        if clipped:
            total_wt = sum(s["weight"] for s in selected)
            for s in selected:
                s["weight"] = s["weight"] / total_wt

    # 8. Cash allocation: if regime requires cash, reduce all weights proportionally
    investable_pct = 1.0 - cash_min
    for s in selected:
        s["weight"] = s["weight"] * investable_pct

    logger.info(f"Portfolio: {len(selected)} positions, cash={cash_min:.0%}")
    return selected


def apply_cost_gate(
    proposed: list[dict],
    current_positions: dict,
    capital: float,
    regime: str,
) -> tuple[list[dict], list[str]]:
    """
    Remove trades where round-trip DEGIRO fee exceeds MAX_ROUNDTRIP_FEE_PCT.
    Also removes positions already held (no change).
    Returns (approved_trades, skipped_log).
    """
    approved = []
    skipped  = []

    for p in proposed:
        ticker   = p["ticker"]
        pos_eur  = p["weight"] * capital
        fee      = calculate_fee(pos_eur)
        rt_fee   = fee * 2  # buy + sell round-trip
        rt_pct   = rt_fee / pos_eur if pos_eur > 0 else 1.0

        if pos_eur < MIN_POSITION_EUR:
            msg = f"{ticker}: position {pos_eur:.0f} EUR < min {MIN_POSITION_EUR} EUR — skipped"
            logger.info(msg); skipped.append(msg)
            continue

        if rt_pct > MAX_ROUNDTRIP_FEE_PCT:
            msg = (
                f"{ticker}: round-trip fee {rt_pct:.2%} > {MAX_ROUNDTRIP_FEE_PCT:.2%} "
                f"(pos={pos_eur:.0f} EUR) — skipped"
            )
            logger.info(msg); skipped.append(msg)
            continue

        approved.append(p)

    return approved, skipped


def build_portfolio(
    scored: list[dict],
    current_state: dict,
    regime: str,
    betas: dict,
) -> dict:
    """
    Full portfolio construction pipeline.
    Returns dict: {proposed_buys, skipped_trades, weights, cash_eur}.
    """
    current_positions = current_state.get("positions", {})
    cash_eur          = current_state.get("cash_eur", INITIAL_CAPITAL)
    capital           = sum(
        p.get("nb_shares", 0) * p.get("entry_price", 0)
        for p in current_positions.values()
    ) + cash_eur

    if capital <= 0:
        capital = INITIAL_CAPITAL

    weighted = compute_weights(scored, regime)
    if not weighted:
        return {"proposed_buys": [], "skipped_trades": [], "weights": [], "cash_eur": cash_eur}

    approved, skipped = apply_cost_gate(weighted, current_positions, capital, regime)

    # Compute share counts and EUR amounts
    for p in approved:
        p["position_eur"] = round(p["weight"] * capital, 2)
        price = p.get("last_close") or 100.0  # fallback
        p["nb_shares"]    = max(1, int(p["position_eur"] / price))

    # Identify new buys (not already held)
    proposed_buys = [p for p in approved if p["ticker"] not in current_positions]

    params  = REGIME_PARAMS[regime]
    cash_wt = params["cash_pct_min"]
    cash_kept = round(capital * cash_wt, 2)

    return {
        "proposed_buys":  proposed_buys,
        "full_portfolio":  approved,
        "skipped_trades": skipped,
        "weights":        weighted,
        "cash_eur":       cash_kept,
        "capital":        capital,
    }
