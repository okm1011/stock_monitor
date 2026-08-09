import httpx
from scripts.backtest_volume_spike import list_symbols, EXCLUDE_BASES, MIN_PRICE_PCT

print("min_price_pct", MIN_PRICE_PCT)
print("exclude", len(EXCLUDE_BASES))
c = httpx.Client(timeout=60)
r = c.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
print("status", r.status_code)
data = r.json()
print("keys", list(data.keys())[:8] if isinstance(data, dict) else type(data))
if isinstance(data, dict) and "symbols" in data:
    print("raw symbols", len(data["symbols"]))
print("filtered", len(list_symbols(c)))
