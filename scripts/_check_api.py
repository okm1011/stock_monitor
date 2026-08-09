import time
import httpx

for i in range(5):
    r = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=60)
    print(i, r.status_code, r.text[:150])
    if r.status_code == 200 and "symbols" in r.json():
        n = sum(
            1
            for s in r.json()["symbols"]
            if s.get("status") == "TRADING"
            and s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
        )
        print("perp", n)
        break
    time.sleep(5)
else:
    raise SystemExit("api unavailable")
