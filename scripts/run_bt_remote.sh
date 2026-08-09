#!/bin/bash
set -e
cd ~/stock_monitor
source .venv/bin/activate

# sync min_price_pct to 10 for this run
python - <<'PY'
from pathlib import Path
p = Path("config.yaml")
t = p.read_text(encoding="utf-8")
import re
t2 = re.sub(r"(min_price_pct:\s*)\d+(\.?\d*)", r"\g<1>10", t, count=1)
p.write_text(t2, encoding="utf-8")
from app.config import load_config
c = load_config().rules.volume_spike
print(f"config min_price_pct={c.min_price_pct} vol={c.volume_mult} quiet={c.quiet_range_pct}")
PY

python - <<'PY'
import httpx
r = httpx.get("https://fapi.binance.com/fapi/v1/klines", params={"symbol":"BTCUSDT","interval":"3m","limit":2}, timeout=20)
print("api", r.status_code)
if r.status_code != 200:
    print(r.text[:200])
    raise SystemExit(1)
PY

python scripts/backtest_volume_spike.py
