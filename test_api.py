import os
import requests

key = os.environ.get("RAPIDAPI_KEY", "")
host = "yahoo-finance187.p.rapidapi.com"

headers = {
    "x-rapidapi-key": key,
    "x-rapidapi-host": host,
}

r = requests.get(
    f"https://{host}/api/v1/markets/stock/history",
    headers=headers,
    params={"symbol": "AIR.PA", "interval": "1d", "diffandsplits": "false"},
    timeout=10,
)
print("Status:", r.status_code)
print("Response:", r.text[:1000])
