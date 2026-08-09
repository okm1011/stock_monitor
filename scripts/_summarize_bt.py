import json
from pathlib import Path

p = Path("data/backtests/volume_spike_tpsl_latest.json")
d = json.loads(p.read_text(encoding="utf-8"))
print("signals", d["signals"], "symbols", d["symbols_fetched"], "days", d["lookback_days"])
print("params", d["params"])
print("\n=== TOP 10 by mean ROI ===")
for r in d["top_by_mean_roi"][:10]:
    print(
        "lev=%sx TP=%sROI(px=%.2f) SL=%sROI(px=%.2f) mean=%.2f WR=%.1f PF=%.2f exits=%s"
        % (
            r["leverage"],
            r["tp_roi"],
            r["tp_price_pct"],
            r["sl_roi"],
            r["sl_price_pct"],
            r["mean_roi"],
            r["win_rate"],
            r["profit_factor"],
            r["exits"],
        )
    )
print("\n=== TOP 8 by score ===")
for r in d["top_by_score"][:8]:
    print(
        "lev=%sx TP=%s SL=%s mean=%.2f WR=%.1f PF=%.2f med=%.2f score=%.1f"
        % (
            r["leverage"],
            r["tp_roi"],
            r["sl_roi"],
            r["mean_roi"],
            r["win_rate"],
            r["profit_factor"],
            r["median_roi"],
            r["score"],
        )
    )
print("\n=== TOP 8 by PF ===")
for r in d["top_by_pf"][:8]:
    print(
        "lev=%sx TP=%s SL=%s mean=%.2f WR=%.1f PF=%.2f"
        % (r["leverage"], r["tp_roi"], r["sl_roi"], r["mean_roi"], r["win_rate"], r["profit_factor"])
    )

# conservative: lev<=10, mean>0, WR>=45, PF>=1.1
safe = [
    r
    for r in d["all_results"]
    if r["leverage"] <= 10 and r["mean_roi"] > 0 and r["win_rate"] >= 45 and r["profit_factor"] >= 1.1
]
safe.sort(key=lambda x: x["mean_roi"], reverse=True)
print("\n=== SAFE (lev<=10, WR>=45, PF>=1.1) TOP 5 ===")
for r in safe[:5]:
    print(
        "lev=%sx TP=%sROI(px=%.2f) SL=%sROI(px=%.2f) mean=%.2f WR=%.1f PF=%.2f"
        % (
            r["leverage"],
            r["tp_roi"],
            r["tp_price_pct"],
            r["sl_roi"],
            r["sl_price_pct"],
            r["mean_roi"],
            r["win_rate"],
            r["profit_factor"],
        )
    )
print("unique signal symbols", len(d.get("signal_symbols", [])))
