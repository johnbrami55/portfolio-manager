"""
backtest.py — Advanced backtest with:
- Dynamic position sizing
- ATR dynamic stops
- Bear/sector filters
- Annual performance breakdown
"""
import json
import logging
from datetime import datetime
from itertools import product
import pandas as pd
import numpy as np
import requests
import openpyxl
from openpyxl.styles import PatternFill, Font
from openpyxl.chart import LineChart, Reference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

START_DATE    = "2020-01-01"
END_DATE      = "2025-12-31"
MAX_POSITIONS = 12
FEE_US        = 2.00
INITIAL_CASH  = 10000.0

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com",
}

US_TICKERS = [
    "KO", "BAC", "ABT", "NEE", "PFE", "F", "T", "VZ", "KHC", "PYPL",
    "NKE",
