"""
config.py -- Central configuration for the portfolio management system.
All parameters are defined here and imported by other modules.
Never hardcode values in other files.
"""

# Capital and Position Sizing
INITIAL_CAPITAL = 1890.0
MIN_POSITION_EUR = 200.0
MAX_POSITION_PCT = 0.20
MAX_SECTOR_PCT = 0.35
MIN_LINES = 5
MAX_LINES = 7

# Rebalancing
REBALANCE_FREQUENCY = "biweekly"

# DEGIRO Fee Model (kept for backward compat)
DEGIRO_FIXED_FEE = 0.50
DEGIRO_VARIABLE_FEE = 0.00004
MAX_ROUNDTRIP_FEE_PCT = 0.008

# Adaptive fees by market
DEGIRO_FEES = {
    "EU": {"fixed": 0.50,  "variable": 0.00004},
    "US": {"fixed": 2.00,  "variable": 0.00004},
    "HK": {"fixed": 1.50,  "variable": 0.00050},
}

# Market Regime Parameters
REGIME_PARAMS = {
    "BULL": {
        "beta_target_min": 1.3, "beta_target_max": 1.6,
        "max_beta_per_stock": 2.0, "score_threshold": 60, "max_lines": 7,
        "cash_pct_min": 0.00, "cash_pct_max": 0.00,
        "stop_loss_pct": -0.10, "take_profit_pct": 0.22,
        "trailing_stop_pct": -0.08, "trailing_stop_trigger": 0.15,
    },
    "NEUTRAL": {
        "beta_target_min": 1.0, "beta_target_max": 1.3,
        "max_beta_per_stock": 1.5, "score_threshold": 58, "max_lines": 6,
        "cash_pct_min": 0.10, "cash_pct_max": 0.15,
        "stop_loss_pct": -0.08, "take_profit_pct": 0.18,
        "trailing_stop_pct": None, "trailing_stop_trigger": None,
    },
    "BEAR": {
        "beta_target_min": 0.7, "beta_target_max": 1.0,
        "max_beta_per_stock": 1.2, "score_threshold": 52, "max_lines": 5,
        "cash_pct_min": 0.20, "cash_pct_max": 0.30,
        "stop_loss_pct": -0.06, "take_profit_pct": 0.14,
        "trailing_stop_pct": None, "trailing_stop_trigger": None,
    },
}

BULL_BETA_BONUS_THRESHOLD = 1.3
BEAR_BETA_BONUS_THRESHOLD = 1.0
REGIME_BONUS_PTS = 5

LIQUIDITY_MIN_VOLUME_EUR = 300_000
LIQUIDITY_MIN_MARKET_CAP_EUR = 300_000_000
LIQUIDITY_MAX_SPREAD_PCT = 0.03
LIQUIDITY_LOOKBACK_DAYS = 20
BEAR_MIN_VOLUME_EUR = 500_000
BEAR_MIN_MARKET_CAP_EUR = 500_000_000
MAX_PRICE_EUR = 200.0   # max entry price for EU (.PA/.AS/.MI/.DE/.F) and US (USD) tickers
MAX_PRICE_HKD = 800.0   # max entry price for .HK tickers (approx 100 USD equivalent)

SCORE_TECH_TREND_MAX = 10
SCORE_TECH_RSI_MAX = 6
SCORE_TECH_VOLUME_MAX = 8
SCORE_TECH_MACD_MAX = 6
SCORE_TECH_MOMENTUM_MAX = 6
SCORE_TECH_BOLLINGER_MAX = 8
SCORE_TECH_STOCHRSI_MAX = 6
SCORE_FUND_EPS_REVISIONS_MAX = 15
SCORE_FUND_VALUATION_MAX = 15
SCORE_FUND_BALANCE_SHEET_MAX = 10
SCORE_FUND_GROWTH_MAX = 10

RSI_THRESHOLDS = {
    "BULL":    {"min": 45, "max": 65, "oversold": 35, "overbought": 75},
    "NEUTRAL": {"min": 40, "max": 60, "oversold": 30, "overbought": 70},
    "BEAR":    {"min": 35, "max": 55, "oversold": 25, "overbought": 65},
}

VOLUME_HIGH_MULT = 1.5
VOLUME_MED_MULT  = 1.2
MOMENTUM_LOOKBACK_MONTHS = 3
EPS_REVISION_LOOKBACK_DAYS = 30
PEG_GOOD_BULL = 1.5
DEBT_EBITDA_MAX = 3.0

SCORE_DEGRADATION_CONSECUTIVE = 2
BEAR_SELL_BETA_THRESHOLD = 1.2

CAC40_TICKER  = "^FCHI"
STOXX600_TICKER = "^STOXX"
MA_SHORT = 50
MA_LONG  = 200

# Index used for beta calculation by market (via yfinance)
INDEX_BY_MARKET = {
    "EU": "^FCHI",
    "US": "^GSPC",
    "HK": "^HSI",
}

STATE_FILE = "portfolio_state.json"
LOG_LEVEL  = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Score momentum detection
SCORE_SAFETY_BUY_THRESHOLD          = 55    # kept for reference; threshold=52 already covers it
MOMENTUM_SIGNAL_MIN_GAIN_PER_RUN    = 3.0   # minimum pts gain per run to count as momentum
MOMENTUM_SIGNAL_HISTORY_RUNS        = 3     # data points needed (2 prior + current run)
MOMENTUM_SIGNAL_MAX_PTS_FROM_THRESHOLD = 8  # score must be within this many pts of threshold
MOMENTUM_SIGNAL_POSITION_FACTOR     = 0.70  # 30% size reduction for momentum signals
SCORE_HISTORY_FILE                  = "score_history.json"
SCORE_HISTORY_MAX_RUNS              = 5     # keep last 5 runs per ticker

CAC40_TICKERS = [
    "AIR.PA","ALO.PA","MT.AS","CS.PA","BNP.PA","EN.PA","CAP.PA",
    "CA.PA","ACA.PA","BN.PA","DSY.PA","ENGI.PA",
    "LR.PA","ML.PA","ORA.PA","RI.PA","PUB.PA","RNO.PA","SAF.PA",
    "SGO.PA","SAN.PA","SU.PA","GLE.PA","STLAM.MI","STLAP.PA","STM.PA","TEP.PA",
    "HO.PA","TTE.PA","URW.AS","VIE.PA","DG.PA","VIV.PA","WLN.PA","TFI.PA","STLAP.PA","TFI.PA","GLE.PA",
]

AEX_TICKERS = [
    "ADYEN.AS","AGN.AS","AD.AS","AKZA.AS","MT.AS","ASM.AS","ASML.AS",
    "ASRNL.AS","BESI.AS","EXOR.AS","HEIA.AS","IMCD.AS","INGA.AS","DSFIR.AS",
    "KPN.AS","NN.AS","PHIA.AS","PRX.AS","RAND.AS","REN.AS","SHELL.AS",
    "UMG.AS","UNA.AS","VPK.AS","WKL.AS",
]

# Tickers européens hors CAC40/AEX (Xetra, Frankfurt, etc.)
EU_EXTRA_TICKERS = [
    "VVSM.DE",   # VanEck Vectors Semiconductor
    "SEC0.DE",   # Secunet Security Networks
    "QDVE.DE",   # iShares S&P 500 IT Sector
    "SXRV.DE",   # iShares STOXX Europe 600 Tech
    "AIXA.DE",   # Aixtron
    "EVT.DE",    # Evotec
    "MDXH.AS",   # MDxHealth
    "BIV.F",     # BIT Mining (Frankfurt, EUR)
]

US_TICKERS = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","BRK-B","JPM","UNH",
    "V","XOM","JNJ","WMT","MA","PG","LLY","HD","MRK","ABBV",
    "AVGO","PEP","KO","COST","TMO","MCD","ACN","BAC","CRM","CSCO",
    "ABT","NEE","TXN","DHR","QCOM","LIN","PM","RTX","HON","UPS",
    "PFE",   # Pfizer ~25$
    "F",     # Ford ~12$
    "T",     # AT&T ~20$
    "INTC",  # Intel ~30$
    "VZ",    # Verizon ~40$
    "WBA",   # Walgreens ~10$
    "KHC",   # Kraft Heinz ~30$
    "PARA",  # Paramount ~12$
    "DAL",   # Delta ~45$
    "UAL",   # United Airlines ~50$
    "CCL",   # Carnival ~18$
    "NCLH",  # Norwegian Cruise ~20$
    "SNAP",  # Snap ~12$
    "PLTR",  # Palantir ~25$
    "SOFI",  # SoFi ~10$
    "NIO",   # NIO ~6$
    "LCID",  # Lucid ~3$
    "PYPL",  # PayPal ~70$
    "DIS",   # Disney ~95$
    "NKE",   # Nike ~75$
    "SBUX",  # Starbucks ~85$
    "GM",    # General Motors ~45$
    "MO",    # Altria ~50$
    "CVS",   # CVS Health ~60$
    "GE",    # General Electric ~85$
    "AAL",   # American Airlines ~12$
    "BBY",   # Best Buy ~75$
    "GAP",   # Gap ~20$
    "X",     # US Steel ~40$
    "BB",    # BlackBerry ~3$
    # High volatility / momentum basket
    "AMD", "RBLX", "RIVN", "COIN", "MSTR", "HOOD",
    "IONQ", "SMCI", "MELI", "SQ", "DKNG",
    "HUT", "CLSK", "MARA", "RIOT",
    # Small/mid cap momentum
    "CELH", "RDDT", "CAVA", "JOBY", "ACHR", "LUNR",
    "PONY", "RGTI", "QUBT", "KULR",
    "WULF", "CORZ", "CIFR", "EXPI",
]

HK_TICKERS = [
    "0700.HK","9988.HK","0941.HK","1299.HK","0005.HK",
    "0388.HK","2318.HK","1398.HK","0939.HK","3690.HK",
    "0883.HK","2628.HK","0011.HK","1810.HK","9999.HK",
    "0002.HK","0003.HK","0016.HK","0017.HK","0027.HK",
    "1928.HK",  # Sands China
    "0175.HK",  # Geely Auto
    "0001.HK",  # CK Hutchison
    "0006.HK",  # Power Assets
    "0012.HK",  # Henderson Land
    "0066.HK",  # MTR Corp
    "0101.HK",  # Hang Lung
    "0151.HK",  # Want Want China
    "0285.HK",  # BYD Electronic
    "0386.HK",  # Sinopec
    "0762.HK",  # China Unicom
    "0857.HK",  # PetroChina
    "1088.HK",  # China Shenhua
    "2007.HK",  # Country Garden
    "6098.HK",  # CG Services
    "1801.HK",  # Innovent Biologics
    "9866.HK",  # NIO (HK)
    "2015.HK",  # Li Auto
    "9618.HK",  # JD.com
    "1024.HK",  # Kuaishou
    "9888.HK",  # Baidu
    "6160.HK",  # BeiGene
    "9868.HK",  # Xpeng
    "0020.HK",  # SenseTime
    "9961.HK",  # Trip.com
    "2382.HK",  # Sunny Optical
    "2238.HK",  # GAC Group
]

FULL_UNIVERSE = list(dict.fromkeys(
    CAC40_TICKERS + AEX_TICKERS + EU_EXTRA_TICKERS + US_TICKERS + HK_TICKERS
))

SECTOR_MAP = {
    # EU (CAC40 / AEX)
    "AIR.PA": "Industrials", "ALO.PA": "Industrials",
    "MT.AS": "Materials", "CS.PA": "Financials", "BNP.PA": "Financials",
    "EN.PA": "Industrials", "CAP.PA": "Technology", "CA.PA": "Consumer Staples",
    "ACA.PA": "Financials", "BN.PA": "Consumer Staples", "DSY.PA": "Technology",
    "ENGI.PA": "Utilities", "LR.PA": "Industrials",
    "ML.PA": "Consumer Discretionary", "ORA.PA": "Communication Services",
    "RI.PA": "Consumer Staples", "PUB.PA": "Communication Services",
    "RNO.PA": "Consumer Discretionary", "SAF.PA": "Industrials", "SGO.PA": "Industrials",
    "SAN.PA": "Health Care", "SU.PA": "Industrials", "GLE.PA": "Financials",
    "STLAM.MI": "Consumer Discretionary", "STLAP.PA": "Consumer Discretionary",
    "STM.PA": "Technology", "TEP.PA": "Technology",
    "HO.PA": "Industrials", "TTE.PA": "Energy", "URW.AS": "Real Estate",
    "VIE.PA": "Utilities", "DG.PA": "Industrials", "VIV.PA": "Communication Services",
    "WLN.PA": "Technology", "TFI.PA": "Communication Services",
    "ADYEN.AS": "Technology", "AGN.AS": "Financials",
    "AD.AS": "Consumer Staples", "AKZA.AS": "Materials", "ASM.AS": "Technology",
    "ASML.AS": "Technology", "ASRNL.AS": "Financials", "BESI.AS": "Technology",
    "EXOR.AS": "Financials", "HEIA.AS": "Consumer Staples", "IMCD.AS": "Materials",
    "INGA.AS": "Financials", "DSFIR.AS": "Materials", "KPN.AS": "Communication Services",
    "NN.AS": "Financials", "PHIA.AS": "Health Care", "PRX.AS": "Technology",
    "RAND.AS": "Industrials", "REN.AS": "Industrials", "SHELL.AS": "Energy",
    "UMG.AS": "Communication Services", "UNA.AS": "Consumer Staples",
    "VPK.AS": "Materials", "WKL.AS": "Technology",
    # EU Extra (Xetra / Frankfurt)
    "VVSM.DE": "Technology",
    "SEC0.DE": "Technology",
    "QDVE.DE": "Technology",
    "SXRV.DE": "Technology",
    "AIXA.DE": "Technology",
    "EVT.DE": "Health Care",
    "MDXH.AS": "Health Care",
    "BIV.F": "Crypto Mining",
    # US (NYSE / Nasdaq)
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "GOOGL": "Communication Services", "AMZN": "Consumer Discretionary",
    "META": "Communication Services", "TSLA": "Consumer Discretionary",
    "BRK-B": "Financials", "JPM": "Financials", "UNH": "Health Care",
    "V": "Financials", "XOM": "Energy", "JNJ": "Health Care",
    "WMT": "Consumer Staples", "MA": "Financials", "PG": "Consumer Staples",
    "LLY": "Health Care", "HD": "Consumer Discretionary", "MRK": "Health Care",
    "ABBV": "Health Care", "AVGO": "Technology", "PEP": "Consumer Staples",
    "KO": "Consumer Staples", "COST": "Consumer Staples", "TMO": "Health Care",
    "MCD": "Consumer Discretionary", "ACN": "Technology", "BAC": "Financials",
    "CRM": "Technology", "CSCO": "Technology", "ABT": "Health Care",
    "NEE": "Utilities", "TXN": "Technology", "DHR": "Health Care",
    "QCOM": "Technology", "LIN": "Materials", "PM": "Consumer Staples",
    "RTX": "Industrials", "HON": "Industrials", "UPS": "Industrials",
    "PFE": "Health Care", "F": "Consumer Discretionary", "T": "Communication Services",
    "INTC": "Technology", "VZ": "Communication Services", "WBA": "Health Care",
    "KHC": "Consumer Staples", "PARA": "Communication Services", "DAL": "Industrials",
    "UAL": "Industrials", "CCL": "Consumer Discretionary", "NCLH": "Consumer Discretionary",
    "SNAP": "Communication Services", "PLTR": "Technology", "SOFI": "Fintech",
    "NIO": "Consumer Discretionary", "LCID": "Consumer Discretionary", "PYPL": "Fintech",
    "DIS": "Communication Services", "NKE": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    "GM": "Consumer Discretionary", "MO": "Consumer Staples", "CVS": "Health Care",
    "GE": "Industrials", "AAL": "Industrials", "BBY": "Consumer Discretionary",
    "GAP": "Consumer Discretionary", "X": "Materials", "BB": "Technology",
    "AMD": "Technology", "RBLX": "Communication Services",
    "RIVN": "Consumer Discretionary", "COIN": "Fintech", "MSTR": "Crypto Mining",
    "HOOD": "Fintech", "IONQ": "Technology", "SMCI": "Technology",
    "MELI": "Consumer Discretionary", "SQ": "Fintech", "AFRM": "Fintech", "DKNG": "Consumer Discretionary",
    "HUT": "Crypto Mining", "CLSK": "Crypto Mining", "MARA": "Crypto Mining",
    "RIOT": "Crypto Mining", "WULF": "Crypto Mining", "CORZ": "Crypto Mining",
    "CIFR": "Crypto Mining",
    "CELH": "Consumer Staples", "RDDT": "Communication Services",
    "CAVA": "Consumer Discretionary", "JOBY": "Industrials", "ACHR": "Industrials",
    "LUNR": "Industrials", "PONY": "Technology", "RGTI": "Technology",
    "QUBT": "Technology", "KULR": "Technology", "EXPI": "Financials",
    # HK (HKEX)
    "0700.HK": "Communication Services",
    "9988.HK": "Consumer Discretionary",
    "0941.HK": "Communication Services",
    "1299.HK": "Financials",
    "0005.HK": "Financials",
    "0388.HK": "Financials",
    "2318.HK": "Financials",
    "1398.HK": "Financials",
    "0939.HK": "Financials",
    "3690.HK": "Consumer Discretionary",
    "0883.HK": "Energy",
    "2628.HK": "Financials",
    "0011.HK": "Financials",
    "1810.HK": "Technology",
    "9999.HK": "Communication Services",
    "0002.HK": "Utilities",
    "0003.HK": "Utilities",
    "0016.HK": "Real Estate",
    "0017.HK": "Real Estate",
    "0027.HK": "Consumer Discretionary",
    "1928.HK": "Consumer Discretionary",
    "0175.HK": "Consumer Discretionary",
    "0001.HK": "Industrials",
    "0006.HK": "Utilities",
    "0012.HK": "Real Estate",
    "0066.HK": "Industrials",
    "0101.HK": "Real Estate",
    "0151.HK": "Consumer Staples",
    "0285.HK": "Technology",
    "0386.HK": "Energy",
    "0762.HK": "Communication Services",
    "0857.HK": "Energy",
    "1088.HK": "Energy",
    "2007.HK": "Real Estate",
    "6098.HK": "Industrials",
    "1801.HK": "Health Care",
    "9866.HK": "Consumer Discretionary",
    "2015.HK": "Consumer Discretionary",
    "9618.HK": "Consumer Discretionary",
    "1024.HK": "Communication Services",
    "9888.HK": "Communication Services",
    "6160.HK": "Health Care",
    "9868.HK": "Consumer Discretionary",
    "0020.HK": "Technology",
    "9961.HK": "Consumer Discretionary",
    "2382.HK": "Technology",
    "2238.HK": "Consumer Discretionary",
}

SECTOR_ETF = {
    "Crypto Mining":          "WGMI",
    "Technology":             "XLK",
    "Health Care":            "XLV",
    "Financials":             "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Industrials":            "XLI",
    "Energy":                 "XLE",
    "Communication Services": "XLC",
    "Fintech":                "ARKF",
}
