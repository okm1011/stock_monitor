"""
5번 펌프 초입 알람 → 즉시 LONG 백테스트.
config.yaml volume_spike 조건과 동일(형성봉=해당 봉 종가 기준).
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402

BASE = "https://fapi.binance.com"
FEE_ROUND = 0.0008  # 왕복 taker 근사 0.08% on notional (레버리지 반영 전 가격%)


@dataclass
class Bar:
    ot: int
    open: float
    high: float
    low: float
    close: float
    qvol: float


@dataclass
class Signal:
    symbol: str
    bar_idx: int
    entry: float
    ot: int


def fetch_klines_history(symbol: str, interval: str, days: int) -> list[Bar]:
    """과거 days일치 3m 봉 (페이지네이션)."""
    limit = 1500
    ms_per_bar = 3 * 60 * 1000
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    out: list[Bar] = []
    client = httpx.Client(timeout=30.0)
    try:
        cur_end = end
        while cur_end > start:
            resp = client.get(
                f"{BASE}/fapi/v1/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit,
                    "endTime": cur_end,
                },
            )
            if resp.status_code == 429:
                time.sleep(2)
                continue
            if resp.status_code >= 400:
                break
            rows = resp.json()
            if not rows:
                break
            batch: list[Bar] = []
            for r in rows:
                ot_ms = int(r[0])
                if ot_ms < start:
                    continue
                batch.append(
                    Bar(
                        ot=ot_ms // 1000,
                        open=float(r[1]),
                        high=float(r[2]),
                        low=float(r[3]),
                        close=float(r[4]),
                        qvol=float(r[7]),
                    )
                )
            if not batch:
                break
            out = batch + out
            cur_end = int(rows[0][0]) - 1
            if len(rows) < limit:
                break
            time.sleep(0.05)
    finally:
        client.close()
    # dedupe by ot
    seen: set[int] = set()
    deduped: list[Bar] = []
    for b in out:
        if b.ot in seen:
            continue
        seen.add(b.ot)
        deduped.append(b)
    deduped.sort(key=lambda x: x.ot)
    return deduped


def get_symbols(exclude: set[str]) -> list[str]:
    info = httpx.get(f"{BASE}/fapi/v1/exchangeInfo", timeout=60).json()
    syms: list[str] = []
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        base = (s.get("baseAsset") or s["symbol"].replace("USDT", "")).upper()
        if base in exclude:
            continue
        syms.append(s["symbol"])
    return sorted(syms)


def passes_filters(bars: list[Bar], i: int, cfg) -> bool:
    """봉 i 종가 시점 = 형성봉 종가로 알람 (백테스트 근사)."""
    need = cfg.quiet_bars + cfg.window_bars
    if i < max(need, cfg.volume_lookback):
        return False
    slice_bars = bars[: i + 1]
    window = slice_bars[-cfg.window_bars :]
    quiet = slice_bars[-(need) : -cfg.window_bars]
    if len(quiet) < cfg.quiet_bars:
        return False

    q_hi = max(b.high for b in quiet)
    q_lo = min(b.low for b in quiet)
    if q_lo <= 0:
        return False
    quiet_pct = (q_hi - q_lo) / q_lo * 100.0
    if quiet_pct > cfg.quiet_range_pct:
        return False

    base_px = slice_bars[-(cfg.window_bars + 1)].close
    last = window[-1]
    if base_px <= 0:
        return False
    price_pct = (last.close - base_px) / base_px * 100.0
    if price_pct < cfg.min_price_pct:
        return False

    prior = slice_bars[-(cfg.volume_lookback + 1) : -1]
    if len(prior) < cfg.volume_lookback:
        return False
    avg_vol = sum(b.qvol for b in prior) / len(prior)
    if avg_vol <= 0 or last.qvol < avg_vol * cfg.volume_mult:
        return False
    return True


def find_signals(symbol: str, bars: list[Bar], cfg) -> list[Signal]:
    sigs: list[Signal] = []
    cooldown_ot = 0
    cd_sec = cfg.cooldown_seconds
    for i in range(len(bars)):
        b = bars[i]
        if b.ot < cooldown_ot:
            continue
        if passes_filters(bars, i, cfg):
            sigs.append(Signal(symbol=symbol, bar_idx=i, entry=b.close, ot=b.ot))
            cooldown_ot = b.ot + cd_sec
    return sigs


def simulate_long(
    bars: list[Bar],
    sig: Signal,
    tp_pct: float,
    sl_pct: float,
    max_hold: int,
    leverage: float,
) -> float:
    """거래 1건 ROI% (레버리지·수수료 반영)."""
    entry = sig.entry
    tp_px = entry * (1 + tp_pct / 100)
    sl_px = entry * (1 - sl_pct / 100)
    liq_move = 100.0 / leverage  # 단순 isolated 근사

    start = sig.bar_idx + 1
    end = min(len(bars), start + max_hold)
    for j in range(start, end):
        b = bars[j]
        # 보수적: 동봉 TP·SL 동시 → SL 우선
        move_low = (b.low - entry) / entry * 100
        if move_low <= -liq_move:
            return -100.0
        if b.low <= sl_px:
            roi = -sl_pct * leverage - FEE_ROUND * leverage * 100
            return roi
        if b.high >= tp_px:
            roi = tp_pct * leverage - FEE_ROUND * leverage * 100
            return roi

    exit_px = bars[end - 1].close if end > start else entry
    move = (exit_px - entry) / entry * 100
    roi = move * leverage - FEE_ROUND * leverage * 100
    return roi


def main() -> None:
    cfg = load_config().rules.volume_spike
    exclude = {b.upper() for b in cfg.exclude_bases}
    days = 30
    max_hold = 80  # ~4h

    print(f"=== 펌프 LONG 백테스트 ({days}일, {cfg.timeframe}) ===")
    print(
        f"조건: +{cfg.min_price_pct}%/{cfg.window_bars}봉, "
        f"vol×{cfg.volume_mult}, quiet<{cfg.quiet_range_pct}%/{cfg.quiet_bars}봉"
    )

    symbols = get_symbols(exclude)
    print(f"심볼 {len(symbols)}개 데이터 수집 중...")

    all_bars: dict[str, list[Bar]] = {}
    done = 0

    def fetch_one(sym: str) -> tuple[str, list[Bar]]:
        return sym, fetch_klines_history(sym, "3m", days)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch_one, s): s for s in symbols}
        for fut in as_completed(futs):
            sym, bars = fut.result()
            done += 1
            if len(bars) >= cfg.history_bars + 10:
                all_bars[sym] = bars
            if done % 80 == 0:
                print(f"  fetch {done}/{len(symbols)}")

    print(f"유효 심볼 {len(all_bars)}개")

    all_signals: list[tuple[str, list[Bar], Signal]] = []
    for sym, bars in all_bars.items():
        for sig in find_signals(sym, bars, cfg):
            all_signals.append((sym, bars, sig))

    print(f"총 신호 {len(all_signals)}건 (cooldown {cfg.cooldown_seconds}s 적용)")

    if len(all_signals) < 20:
        print("신호가 너무 적어 통계 신뢰도 낮음")
        return

    leverages = [3, 5, 10, 15, 20]
    tps = [5, 8, 10, 15, 20, 30, 40, 50]
    sls = [3, 5, 8, 10, 12, 15, 20]

    results: list[dict] = []
    for lev, tp, sl in product(leverages, tps, sls):
        if sl >= 100 / lev * 0.95:
            continue  # 청산과 SL 겹침
        rois: list[float] = []
        for sym, bars, sig in all_signals:
            rois.append(simulate_long(bars, sig, tp, sl, max_hold, lev))
        n = len(rois)
        wins = sum(1 for r in rois if r > 0)
        losses = sum(1 for r in rois if r <= 0)
        liqs = sum(1 for r in rois if r <= -99)
        avg = sum(rois) / n
        win_rate = wins / n * 100
        results.append(
            {
                "lev": lev,
                "tp": tp,
                "sl": sl,
                "n": n,
                "win_rate": win_rate,
                "avg_roi": avg,
                "total_roi": sum(rois),
                "median_roi": sorted(rois)[n // 2],
                "liqs": liqs,
                "worst": min(rois),
                "best": max(rois),
            }
        )

    results.sort(key=lambda x: x["avg_roi"], reverse=True)
    top = results[:15]

    print("\n=== TOP 15 (평균 ROI% 기준, 레버리지·TP·SL = 가격 % × 레버리지) ===")
    print(f"{'Lev':>4} {'TP%':>5} {'SL%':>5} {'N':>5} {'Win%':>6} {'AvgROI':>8} {'MedROI':>8} {'Liq':>4} {'Worst':>8}")
    for r in top:
        print(
            f"{r['lev']:>4} {r['tp']:>5.0f} {r['sl']:>5.0f} {r['n']:>5} "
            f"{r['win_rate']:>5.1f}% {r['avg_roi']:>7.2f}% {r['median_roi']:>7.2f}% "
            f"{r['liqs']:>4} {r['worst']:>7.1f}%"
        )

    best = top[0]
    print("\n=== 추천 (기대값 최대 조합) ===")
    print(
        f"레버리지 {best['lev']}x | TP +{best['tp']}% | SL -{best['sl']}% "
        f"(가격 기준, ROI는 ×{best['lev']})"
    )
    print(f"  거래 수: {best['n']}")
    print(f"  승률: {best['win_rate']:.1f}%")
    print(f"  평균 ROI/건: {best['avg_roi']:+.2f}%")
    print(f"  중앙 ROI/건: {best['median_roi']:+.2f}%")
    print(f"  청산(-100%): {best['liqs']}건")
    print(f"  최악/최고: {best['worst']:+.1f}% / {best['best']:+.1f}%")

    # 보수적: 승률 40%+ & liq<=2%
    safe = [
        r
        for r in results
        if r["win_rate"] >= 40 and r["liqs"] <= max(2, r["n"] * 0.02) and r["avg_roi"] > 0
    ]
    safe.sort(key=lambda x: x["avg_roi"], reverse=True)
    if safe:
        s = safe[0]
        print("\n=== 보수적 추천 (승률≥40%, 청산≤2%) ===")
        print(f"레버리지 {s['lev']}x | TP +{s['tp']}% | SL -{s['sl']}%")
        print(f"  승률 {s['win_rate']:.1f}% | 평균 ROI {s['avg_roi']:+.2f}%/건")

    out_path = ROOT / "data" / "backtest_pump_long.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "days": days,
                "signals": len(all_signals),
                "symbols": len(all_bars),
                "config": cfg.model_dump(),
                "top15": top,
                "best": best,
                "safe_best": safe[0] if safe else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n상세 저장: {out_path}")


if __name__ == "__main__":
    main()
