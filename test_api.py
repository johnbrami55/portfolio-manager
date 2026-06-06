import os
import requests

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "yh-finance.p.rapidapi.com"

headers = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": RAPIDAPI_HOST,
}

r = requests.get(
    f"https://{RAPIDAPI_HOST}/api/v1/markets/stock/history",
    headers=headers,
    params={"symbol": "AIR.PA", "interval": "1d", "diffandsplits": "false"},
    timeout=10,
)
print("Status:", r.status_code)
print("Response:", r.text[:1000])
