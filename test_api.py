import os
import requests

key = os.environ.get("RAPIDAPI_KEY", "")
host = "enclout-yahoo-finance.p.rapidapi.com"

headers = {
    "x-rapidapi-key": key,
    "x-rapidapi-host": host,
}

# Test endpoint de base
r = requests.get(
    f"https://{host}/",
    headers=headers,
    timeout=10,
)
print("Status:", r.status_code)
print("Response:", r.text[:1000])
