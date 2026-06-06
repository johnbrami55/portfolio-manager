import os
import requests

key = os.environ.get("ALPHA_VANTAGE_KEY", "")

r = requests.get(
    "https://www.alphavantage.co/query",
    params={
        "function": "TIME_SERIES_DAILY",
        "symbol": "AIR.PAR",
        "apikey": key,
        "outputsize": "compact",
    },
    timeout=10,
)
print("Status:", r.status_code)
print("Response:", r.text[:500])
