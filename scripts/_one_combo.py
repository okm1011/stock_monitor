"""Quick one-combo: lev=10, TP ROI 50, SL ROI 10 on same 90d pump signals."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_volume_spike import (  # noqa: E402
    LOOKBACK_DAYS,
    QUIET_BARS,
    WINDOW_BARS,
    VOLUME_LOOKBACK,
    detect_signals,
    fetch_klines,
    list_symbols,
    simulate_trade,
    summarize,
)

LEV, TP, SL = 10, 50, 10


def main() -> None:
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = end_ms - LOOKBACK_DAYS * 24 * 3600 * 1000
    need = QUIET_BARS + WINDOW_BARS + VOLUME_LOOKBACK + 5

    with httpx.Client(timeout=60.0) as client:
        symbols = list_symbols(client)
        print(f"symbols={len(symbols)}")
        all_bars = {}
        for i, sym in enumerate(symbols, 1):
            bars = fetch_klines(client, sym, start_ms, end_ms)
            if len(bars) >= need:
                all_bars[sym] = bars
            if i % 100 == 0:
                print(f"  load {i}/{len(symbols)} ok={len(all_bars)}")

    signals = []
    for sym, bars in all_bars.items():
        idx_map = {b.ot: i for i, b in enumerate(bars)}
        for sig in detect_signals(sym, bars):
            idx = idx_map.get(sig.ot)
            if idx is not None:
                signals.append((sig, sym, idx))

    print(f"signals={len(signals)}")
    rois, exits = [], []
    for sig, sym, idx in signals:
        tr = simulate_trade(all_bars[sym], idx, sig.entry, LEV, float(TP), float(SL))
        rois.append(tr["roi"])
        exits.append(tr["exit"])

    stats = summarize(rois, exits)
    print(
        f"lev={LEV}x TP={TP}%ROI(~{TP/LEV:.2f}%px) SL={SL}%ROI(~{SL/LEV:.2f}%px)"
    )
    print(
        f"n={stats['n']} WR={stats['win_rate']:.1f}% mean={stats['mean_roi']:.2f}% "
        f"med={stats['median_roi']:.2f}% PF={stats['profit_factor']:.2f} exits={stats['exits']}"
    )


if __name__ == "__main__":
    main()
