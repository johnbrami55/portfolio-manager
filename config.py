"""
config.py — Central configuration for the portfolio management system.
All parameters are defined here and imported by other modules.
Never hardcode values in other files.
"""

# ─── Capital & Position Sizing ───────────────────────────────────────────────
INITIAL_CAPITAL = 1890.0          # EUR — starting capital
MIN_POSITION_EUR = 200.0          # Minimum position size in EUR
MAX_POSITION_PCT = 0.20           # Max weight per stock (20%)
MAX_SECTOR_PCT = 0.35             # Max sector exposure (35%)
MIN_LINES = 5                     # Minimum number of positions
MAX_LINES = 7                     # Maximum number of positions

# ─── Rebalancing ─────────────────────────────────────────────────────────────
REBALANCE_FREQUENCY = "biweekly"  # Every 2 weeks

# ─── DEGIRO Fee Model ─────────────────────────────────────────────────────────
DEGIRO_FIXED_FEE = 0.50           # EUR per order (Euronext)
DEGIRO_VARIABLE_FEE = 0.00004     # 0.004% of order value
MAX_ROUNDTRIP_FEE_PCT = 0.008     # Skip trade if round-trip > 0.8% of position

# ─── Market Regime Parameters ─────────────────────────────────────────────────
REGIME_PARAMS = {
    "BULL": {
        "beta_target_min": 1.3,
        "beta_target_max": 1.6,
        "max_beta_per_stock": 2.0,
        "score_threshold": 60,
        "max_lines": 7,
        "cash_pct_min": 0.00,
        "cash_pct_max": 0.00,
        "stop_loss_pct": -0.10,
        "take_profit_pct": 0.22,
        "trailing_stop_pct": -0.08,
        "trailing_stop_trigger": 0.15,
    },
    "NEUTRAL": {
        "beta_target_min": 1.0,
        "beta_target_max": 1.3,
        "max_beta_per_stock": 1.5,
        "score_threshold": 65,
        "max_lines": 6,
        "cash_pct_min": 0.10,
        "cash_pct_max": 0.15,
        "stop_loss_pct": -0.08,
        "take_profit_pct": 0.18,
        "trailing_stop_pct": None,
        "trailing_stop_trigger": None,
    },
    "BEAR": {
        "beta_target_min": 0.7,
        "beta_target_max": 1.0,
        "max_beta_per_stock": 1.2,
        "score_threshold": 72,
        "max_lines": 5,
        "cash_pct_min": 0.20,
        "cash_pct_max": 0.30,
        "stop_loss_pct": -0.06,
        "take_profit_pct": 0.14,
        "trailing_stop_pct": None,
        "trailing_stop_trigger": None,
    },
}

# ─── Regime Bonus ─────────────────────────────────────────────────────────────
BULL_BETA_BONUS_THRESHOLD = 1.3
BEAR_BETA_BONUS_THRESHOLD = 1.0
REGIME_BONUS_PTS = 5

# ─── Liquidity Filters ────────────────────────────────────────────────────────
LIQUIDITY_MIN_VOLUME_EUR = 300_000
LIQUIDITY_MIN_MARKET_CAP_EUR = 300_000_000
LIQUIDITY_MAX_SPREAD_PCT = 0.03
LIQUIDITY_LOOKBACK_DAYS = 20
BEAR_MIN_VOLUME_EUR = 500_000
BEAR_MIN_MARKET_CAP_EUR = 500_000_000

# ─── Scoring Weights ──────────────────────────────────────────────────────────
SCORE_TECH_TREND_MAX = 15
SCORE_TECH_RSI_MAX = 10
SCORE_TECH_VOLUME_MAX = 10
SCORE_TECH_MACD_MAX = 8
SCORE_TECH_MOMENTUM_MAX = 7

SCORE_FUND_EPS_REVISIONS_MAX = 15
SCORE_FUND_VALUATION_MAX = 15
SCORE_FUND_BALANCE_SHEET_MAX = 10
SCORE_FUND_GROWTH_MAX = 10

RSI_THRESHOLDS = {
    "BULL":    {"min": 45, "max": 65, "oversold": 35, "overbought": 75},
    "NEUTRAL": {"min": 40, "max": 60, "oversold": 30, "overbought": 70},
    "BEAR":    {"min": 35, "max": 55, "oversold": 25, "overbought": 65},
}

VOLUME_HIGH_MULT = 2.0
VOLUME_MED_MULT  = 1.5
MOMENTUM_LOOKBACK_MONTHS = 3
EPS_REVISION_LOOKBACK_DAYS = 30
PEG_GOOD_BULL = 1.5
DEBT_EBITDA_MAX = 3.0

# ─── Sell Signal Parameters ───────────────────────────────────────────────────
SCORE_DEGRADATION_CONSECUTIVE = 2
BEAR_SELL_BETA_THRESHOLD = 1.2

# ─── Reference Indices ────────────────────────────────────────────────────────
CAC40_TICKER  = "^FCHI"
STOXX600_TICKER = "^STOXX"
MA_SHORT = 50
MA_LONG  = 200

# ─── State File ───────────────────────────────────────────────────────────────
STATE_FILE = "portfolio_state.json"

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# ─── Stock Universes ──────────────────────────────────────────────────────────
CAC40_TICKERS = [
    "AI.PA","AIR.PA","ALO.PA","MT.AS","CS.PA","BNP.PA","EN.PA","CAP.PA",
    "CA.PA","ACA.PA","BN.PA","DSY.PA","ENGI.PA","EL.PA","RMS.PA","KER.PA",
    "LR.PA","MC.PA","ML.PA","ORA.PA","RI.PA","PUB.PA","RNO.PA","SAF.PA",
    "SGO.PA","SAN.PA","SU.PA","GLE.PA","STLAM.MI","STM.PA","TEP.PA","HO.PA",
    "TTE.PA","URW.AS","VIE.PA","DG.PA","VIV.PA","WLN.PA",
]

AEX_TICKERS = [
    "ADYEN.AS","AGN.AS","AD.AS","AKZA.AS","MT.AS","ASM.AS","ASML.AS",
    "ASRNL.AS","BESI.AS","EXOR.AS","HEIA.AS","IMCD.AS","INGA.AS","DSFIR.AS",
    "KPN.AS","NN.AS","PHIA.AS","PRX.AS","RAND.AS","REN.AS","SHELL.AS",
    "UMG.AS","UNA.AS","VPK.AS","WKL.AS",
]

FULL_UNIVERSE = list(dict.fromkeys(CAC40_TICKERS + AEX_TICKERS))

# ─── Sector Mapping ──────────────────────────────────────────────────────────
SECTOR_MAP = {
    "AI.PA":"Materials","AIR.PA":"Industrials","ALO.PA":"Industrials",
    "MT.AS":"Materials","CS.PA":"Financials","BNP.PA":"Financials",
    "EN.PA":"Industrials","CAP.PA":"Technology","CA.PA":"Consumer Staples",
    "ACA.PA":"Financials","BN.PA":"Consumer Staples","DSY.PA":"Technology",
    "ENGI.PA":"Utilities","EL.PA":"Consumer Discretionary","RMS.PA":"Consumer Discretionary",
    "KER.PA":"Consumer Discretionary","LR.PA":"Industrials","MC.PA":"Consumer Discretionary",
    "ML.PA":"Consumer Discretionary","ORA.PA":"Communication Services",
    "RI.PA":"Consumer Staples","PUB.PA":"Communication Services",
    "RNO.PA":"Consumer Discretionary","SAF.PA":"Industrials","SGO.PA":"Industrials",
    "SAN.PA":"Health Care","SU.PA":"Industrials","GLE.PA":"Financials",
    "STLAM.MI":"Consumer Discretionary","STM.PA":"Technology","TEP.PA":"Technology",
    "HO.PA":"Industrials","TTE.PA":"Energy","URW.AS":"Real Estate",
    "VIE.PA":"Utilities","DG.PA":"Industrials","VIV.PA":"Communication Services",
    "WLN.PA":"Technology","ADYEN.AS":"Technology","AGN.AS":"Financials",
    "AD.AS":"Consumer Staples","AKZA.AS":"Materials","ASM.AS":"Technology",
    "ASML.AS":"Technology","ASRNL.AS":"Financials","BESI.AS":"Technology",
    "EXOR.AS":"Financials","HEIA.AS":"Consumer Staples","IMCD.AS":"Materials",
    "INGA.AS":"Financials","DSFIR.AS":"Materials","KPN.AS":"Communication Services",
    "NN.AS":"Financials","PHIA.AS":"Health Care","PRX.AS":"Technology",
    "RAND.AS":"Industrials","REN.AS":"Industrials","SHELL.AS":"Energy",
    "UMG.AS":"Communication Services","UNA.AS":"Consumer Staples",
    "VPK.AS":"Materials","WKL.AS":"Technology",
}
