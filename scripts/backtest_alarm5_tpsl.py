#!/usr/bin/env python3
"""
Alarm 5 (volume_spike) long → leverage × TP/SL(ROI) grid backtest.

Data: Binance Vision futures UM monthly 3m klines.
Processes one symbol at a time (low memory). Same filters as live config.
"""
from __future__ import annotations

import io
import json
import math
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

VISION = "https://data.binance.vision"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "backtest_cache"
OUT = ROOT / "data" / "backtest_alarm5_results.json"

WINDOW_BARS = 5
MIN_PRICE_PCT = 15.0
VOLUME_LOOKBACK = 20
VOLUME_MULT = 4.0
QUIET_BARS = 40
QUIET_RANGE_PCT = 10.0
COOLDOWN_SEC = 600

EXCLUDE = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT",
    "TRX", "LTC", "BCH", "NEAR", "SUI", "PEPE", "WLD", "UNI", "AAVE", "FIL",
    "TON", "SHIB", "APT", "ARB", "OP", "ATOM", "ICP", "HYPE",
}

MONTHS = [
    "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
]
MAX_HOLD_BARS = 160
FEE_RATE = 0.0004
FETCH_WORKERS = 10
MIN_BARS = QUIET_BARS + WINDOW_BARS + MAX_HOLD_BARS + 50
FWD_HORIZONS = (1, 2, 5, 10, 20, 40, 80, 160)

LEVERAGES = [3, 5, 7, 10, 15, 20]
TP_ROIS = [20, 30, 50, 75, 100, 150, 200]
SL_ROIS = [10, 15, 20, 30, 40, 50]


@dataclass
class Bar:
    ot: int
    open: float
    high: float
    low: float
    close: float
    qvol: float


@dataclass
class ComboStat:
    leverage: int
    tp_roi: int
    sl_roi: int
    n: int = 0
    sum_roi: float = 0.0
    sum_sq: float = 0.0
    wins: int = 0
    outcomes: dict = field(default_factory=lambda: {
        "tp": 0, "sl": 0, "sl_same_bar": 0, "timeout": 0, "bad_entry": 0
    })
    # reservoir for percentiles (cap memory)
    sample: list[float] = field(default_factory=list)

    def add(self, roi: float, reason: str) -> None:
        self.n += 1
        self.sum_roi += roi
        self.sum_sq += roi * roi
        if roi > 0:
            self.wins += 1
        self.outcomes[reason] = self.outcomes.get(reason, 0) + 1
        if len(self.sample) < 5000:
            self.sample.append(roi)
        elif self.n % 7 == 0:
            self.sample[self.n % len(self.sample)] = roi

    def summary(self) -> dict:
        n = self.n
        mean = self.sum_roi / n if n else 0.0
        var = self.sum_sq / n - mean * mean if n else 0.0
        std = math.sqrt(max(0.0, var))
        sr = sorted(self.sample) if self.sample else [0.0]
        m = len(sr)
        med = sr[m // 2]
        p05 = sr[max(0, int(m * 0.05) - 1)]
        p95 = sr[min(m - 1, int(m * 0.95))]
        sharpe = mean / std if std > 1e-9 else 0.0
        return {
            "leverage": self.leverage,
            "tp_roi": self.tp_roi,
            "sl_roi": self.sl_roi,
            "n": n,
            "mean_roi": round(mean, 3),
            "median_roi": round(med, 3),
            "win_rate": round(self.wins / n * 100, 2) if n else 0.0,
            "std_roi": round(std, 3),
            "sharpe_like": round(sharpe, 4),
            "p05": round(p05, 3),
            "p95": round(p95, 3),
            "sum_roi": round(self.sum_roi, 2),
            "outcomes": dict(self.outcomes),
            "score_ev": round(mean, 3),
            "score_risk_adj": round(mean - 0.15 * abs(p05), 3),
        }


def log(msg: str) -> None:
    print(msg, flush=True)


def base_asset(symbol: str) -> str:
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def list_um_symbols(client: httpx.Client) -> list[str]:
    url = f"{S3}?prefix=data/futures/um/monthly/klines/&delimiter=/"
    r = client.get(url, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    symbols: list[str] = []
    for cp in root.findall("s3:CommonPrefixes", ns):
        pref = cp.findtext("s3:Prefix", default="", namespaces=ns)
        m = re.search(r"klines/([A-Z0-9]+)USDT/", pref)
        if not m:
            continue
        sym = m.group(1) + "USDT"
        if base_asset(sym).upper() in EXCLUDE:
            continue
        symbols.append(sym)
    return sorted(set(symbols))


def parse_klines_csv(text: str) -> list[Bar]:
    bars: list[Bar] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 8:
            continue
        try:
            ot_ms = int(float(parts[0]))
            bars.append(
                Bar(
                    ot=ot_ms // 1000,
                    open=float(parts[1]),
                    high=float(parts[2]),
                    low=float(parts[3]),
                    close=float(parts[4]),
                    qvol=float(parts[7]),
                )
            )
        except ValueError:
            continue
    return bars


def download_month(client: httpx.Client, symbol: str, month: str) -> list[Bar]:
    path = f"data/futures/um/monthly/klines/{symbol}/3m/{symbol}-3m-{month}.zip"
    url = f"{VISION}/{path}"
    try:
        r = client.get(url, timeout=45, follow_redirects=True)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            name = zf.namelist()[0]
            text = zf.read(name).decode("utf-8", errors="ignore")
            return parse_klines_csv(text)
    except Exception:
        return []


def cache_path(symbol: str) -> Path:
    return CACHE / f"{symbol}_3m_vision_{MONTHS[0]}_{MONTHS[-1]}.json"


def load_symbol_bars(symbol: str) -> list[Bar]:
    cp = cache_path(symbol)
    if cp.exists():
        try:
            raw = json.loads(cp.read_text(encoding="utf-8"))
            return [Bar(*row) for row in raw]
        except Exception:
            pass

    bars: list[Bar] = []
    with httpx.Client(timeout=45.0, headers={"User-Agent": "stock-monitor-bt/1.0"}) as client:
        for month in MONTHS:
            bars.extend(download_month(client, symbol, month))

    by_ot: dict[int, Bar] = {b.ot: b for b in bars}
    ordered = [by_ot[k] for k in sorted(by_ot)]
    if len(ordered) >= MIN_BARS:
        # compact cache as tuples
        cp.write_text(
            json.dumps([[b.ot, b.open, b.high, b.low, b.close, b.qvol] for b in ordered]),
            encoding="utf-8",
        )
    return ordered


def passes_at(bars: list[Bar], i: int) -> bool:
    need = QUIET_BARS + WINDOW_BARS
    if i + 1 < max(need + 1, VOLUME_LOOKBACK + 1):
        return False
    window = bars[i - WINDOW_BARS + 1 : i + 1]
    quiet = bars[i - need + 1 : i - WINDOW_BARS + 1]
    if len(quiet) < QUIET_BARS or len(window) < WINDOW_BARS:
        return False
    q_lo = min(b.low for b in quiet)
    if q_lo <= 0:
        return False
    if (max(b.high for b in quiet) - q_lo) / q_lo * 100.0 > QUIET_RANGE_PCT:
        return False
    base_px = bars[i - WINDOW_BARS].close
    last = window[-1]
    if base_px <= 0:
        return False
    if (last.close - base_px) / base_px * 100.0 < MIN_PRICE_PCT:
        return False
    prior = bars[i - VOLUME_LOOKBACK : i]
    if len(prior) < VOLUME_LOOKBACK:
        return False
    avg_vol = sum(b.qvol for b in prior) / len(prior)
    return avg_vol > 0 and last.qvol >= avg_vol * VOLUME_MULT


def find_signals(bars: list[Bar]) -> list[int]:
    idxs: list[int] = []
    last_alert = -10**18
    for i in range(QUIET_BARS + WINDOW_BARS, len(bars)):
        if bars[i].ot - last_alert < COOLDOWN_SEC:
            continue
        if passes_at(bars, i):
            idxs.append(i)
            last_alert = bars[i].ot
    return idxs


def simulate_trade(
    bars: list[Bar], entry_i: int, leverage: float, tp_roi: float, sl_roi: float
) -> tuple[float, str]:
    entry = bars[entry_i].close
    if entry <= 0:
        return 0.0, "bad_entry"
    eff_sl = min(sl_roi, 90.0)
    tp = entry * (1.0 + (tp_roi / leverage) / 100.0)
    sl = entry * (1.0 - (eff_sl / leverage) / 100.0)
    fee = 2.0 * FEE_RATE * leverage * 100.0
    end = min(len(bars) - 1, entry_i + MAX_HOLD_BARS)
    for j in range(entry_i + 1, end + 1):
        b = bars[j]
        hit_tp = b.high >= tp
        hit_sl = b.low <= sl
        if hit_tp and hit_sl:
            return -eff_sl - fee, "sl_same_bar"
        if hit_sl:
            return -eff_sl - fee, "sl"
        if hit_tp:
            return tp_roi - fee, "tp"
    exit_px = bars[end].close
    return (exit_px / entry - 1.0) * 100.0 * leverage - fee, "timeout"


def init_grid() -> dict[tuple[int, int, int], ComboStat]:
    return {
        (lev, tp, sl): ComboStat(lev, tp, sl)
        for lev in LEVERAGES
        for tp in TP_ROIS
        for sl in SL_ROIS
    }


def process_symbol(
    symbol: str, grid: dict[tuple[int, int, int], ComboStat], fwd_sums: dict, fwd_lists: dict
) -> tuple[int, int | None, int | None]:
    """Returns (n_signals, first_ot, last_ot)."""
    bars = load_symbol_bars(symbol)
    if len(bars) < MIN_BARS:
        return 0, None, None
    idxs = [i for i in find_signals(bars) if i + 2 < len(bars)]
    if not idxs:
        return 0, None, None

    for i in idxs:
        for key, st in grid.items():
            lev, tp, sl = key
            roi, reason = simulate_trade(bars, i, lev, tp, sl)
            st.add(roi, reason)
        entry = bars[i].close
        for h in FWD_HORIZONS:
            j = min(len(bars) - 1, i + h)
            pct = (bars[j].close / entry - 1.0) * 100.0
            fwd_sums[h] = fwd_sums.get(h, 0.0) + pct
            lst = fwd_lists.setdefault(h, [])
            if len(lst) < 8000:
                lst.append(pct)

    return len(idxs), bars[idxs[0]].ot, bars[idxs[-1]].ot


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log("=== Alarm5 TP/SL leverage backtest (Vision, streaming) ===")
    log(
        f"filters: +{MIN_PRICE_PCT:g}%/{WINDOW_BARS}bars volx{VOLUME_MULT:g} "
        f"quiet<{QUIET_RANGE_PCT:g}%/{QUIET_BARS} cooldown={COOLDOWN_SEC}s"
    )
    log(f"months={MONTHS[0]}..{MONTHS[-1]} hold<={MAX_HOLD_BARS * 3}m")

    with httpx.Client(timeout=60.0, headers={"User-Agent": "stock-monitor-bt/1.0"}) as client:
        symbols = list_um_symbols(client)
    log(f"symbols eligible: {len(symbols)}")

    grid = init_grid()
    fwd_sums: dict[int, float] = {}
    fwd_lists: dict[int, list[float]] = {}
    per_sym: list[tuple[str, int]] = []
    n_signals = 0
    n_ok = 0
    ot_min: int | None = None
    ot_max: int | None = None

    # Prefetch missing caches in parallel batches, then process sequentially for RAM
    missing = [s for s in symbols if not cache_path(s).exists()]
    log(f"cache hit={len(symbols) - len(missing)} miss={len(missing)}")

    if missing:
        done = 0
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            futs = {pool.submit(load_symbol_bars, s): s for s in missing}
            for fut in as_completed(futs):
                done += 1
                try:
                    fut.result()
                except Exception:
                    pass
                if done % 40 == 0 or done == len(missing):
                    log(f"  prefetch {done}/{len(missing)}")

    log("scanning signals + scoring grid (one symbol at a time)...")
    for i, sym in enumerate(symbols, 1):
        try:
            cnt, a, b = process_symbol(sym, grid, fwd_sums, fwd_lists)
        except Exception as exc:
            log(f"  skip {sym}: {exc}")
            cnt, a, b = 0, None, None
        if cnt:
            n_ok += 1
            n_signals += cnt
            per_sym.append((sym, cnt))
            if a is not None:
                ot_min = a if ot_min is None else min(ot_min, a)
            if b is not None:
                ot_max = b if ot_max is None else max(ot_max, b)
        if i % 50 == 0 or i == len(symbols):
            log(f"  scan {i}/{len(symbols)} signals={n_signals} syms={n_ok}")

    if n_signals == 0:
        log("No signals — abort")
        return

    summaries = [st.summary() for st in grid.values()]
    by_ev = sorted(summaries, key=lambda r: (-r["mean_roi"], -r["sharpe_like"]))
    by_risk = sorted(summaries, key=lambda r: (-r["score_risk_adj"], -r["mean_roi"]))
    by_sharpe = sorted(summaries, key=lambda r: (-r["sharpe_like"], -r["mean_roi"]))
    best_per_lev = []
    for lev in LEVERAGES:
        subset = [r for r in summaries if r["leverage"] == lev]
        if subset:
            best_per_lev.append(max(subset, key=lambda r: r["mean_roi"]))

    fwd_stats = []
    for h in FWD_HORIZONS:
        lst = sorted(fwd_lists.get(h, []))
        m = len(lst) or 1
        fwd_stats.append(
            {
                "bars": h,
                "minutes": h * 3,
                "mean_pct": round(fwd_sums.get(h, 0.0) / n_signals, 3),
                "median_pct": round(lst[len(lst) // 2], 3) if lst else 0.0,
                "p25": round(lst[len(lst) // 4], 3) if lst else 0.0,
                "p75": round(lst[len(lst) * 3 // 4], 3) if lst else 0.0,
                "n_sample": len(lst),
            }
        )

    per_sym.sort(key=lambda x: -x[1])
    meta = {
        "from": datetime.fromtimestamp(ot_min, tz=timezone.utc).isoformat() if ot_min else None,
        "to": datetime.fromtimestamp(ot_max, tz=timezone.utc).isoformat() if ot_max else None,
        "months": MONTHS,
        "symbols_scanned": len(symbols),
        "symbols_with_data_or_cache": n_ok,
        "symbols_with_signals": n_ok,
        "n_signals": n_signals,
        "max_hold_minutes": MAX_HOLD_BARS * 3,
        "fee_taker_each": FEE_RATE,
        "entry": "signal_bar_close",
        "same_bar_tp_sl": "assume_sl_first",
        "data_source": "binance_vision_um_monthly_3m",
        "elapsed_sec": round(time.time() - t0, 1),
    }
    payload = {
        "meta": meta,
        "forward_price_path": fwd_stats,
        "top_by_ev": by_ev[:25],
        "top_by_risk_adj": by_risk[:15],
        "top_by_sharpe": by_sharpe[:15],
        "best_per_leverage": best_per_lev,
        "all_grid": by_ev,
        "top_signal_symbols": [{"symbol": s, "count": c} for s, c in per_sym[:40]],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"wrote {OUT}")
    log("--- TOP 10 by mean ROI (EV) ---")
    for r in by_ev[:10]:
        log(
            f"  lev={r['leverage']:>2} TP={r['tp_roi']:>3}% SL={r['sl_roi']:>2}% | "
            f"EV={r['mean_roi']:+.2f}% win={r['win_rate']:.1f}% "
            f"med={r['median_roi']:+.1f} sharpe={r['sharpe_like']:.3f} n={r['n']}"
        )
    log(f"done in {meta['elapsed_sec']}s signals={n_signals}")


if __name__ == "__main__":
    main()
