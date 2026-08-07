"""
알람5(volume_spike) 신호 후 즉시 롱 진입 가정.
ROI 기준 TP/SL × 레버리지 그리드 백테스트.

Usage:
  python scripts/backtest_volume_spike.py
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://fapi.binance.com"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "backtests"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "backtest_cache"
INTERVAL = "3m"
BAR_SEC = 180

# config.yaml 과 동기화 (없으면 기본값)
def _cfg():
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.config import load_config

    return load_config().rules.volume_spike


VS = _cfg()
WINDOW_BARS = VS.window_bars
MIN_PRICE_PCT = VS.min_price_pct
VOLUME_LOOKBACK = VS.volume_lookback
VOLUME_MULT = VS.volume_mult
QUIET_BARS = VS.quiet_bars
QUIET_RANGE_PCT = VS.quiet_range_pct
COOLDOWN_SEC = VS.cooldown_seconds
EXCLUDE_BASES = {b.upper() for b in VS.exclude_bases}

# 데이터
LOOKBACK_DAYS = 90
MAX_WORKERS_FETCH = 4  # API rate limit 완화
FETCH_SLEEP = 0.15
KLINE_LIMIT = 1500
TAKER_FEE = 0.0004  # 편도
MAX_HOLD_BARS = 160  # 8시간

# ROI(%) 기준 TP/SL — 마진 대비. 가격% = ROI / leverage
LEVERAGES = [3, 5, 7, 10, 15, 20]
TP_ROIS = [20, 30, 50, 80, 100, 150, 200, 300]
SL_ROIS = [15, 20, 30, 40, 50, 75, 100]


@dataclass
class Bar:
    ot: int
    o: float
    h: float
    l: float
    c: float
    qvol: float


@dataclass
class Signal:
    symbol: str
    ot: int
    entry: float
    price_pct: float
    vol_x: float


def _base(sym: str) -> str:
    return sym[:-4] if sym.endswith("USDT") else sym


def list_symbols(client: httpx.Client) -> list[str]:
    info = client.get(f"{BASE}/fapi/v1/exchangeInfo").json()
    out: list[str] = []
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        base = (s.get("baseAsset") or _base(s["symbol"])).upper()
        if base in EXCLUDE_BASES:
            continue
        out.append(s["symbol"])
    return sorted(out)


def _cache_path(symbol: str, start_ms: int, end_ms: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{symbol}_{start_ms}_{end_ms}.json"


def _bars_from_cache(path: Path) -> list[Bar] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Bar(**b) for b in raw]
    except Exception:
        return None


def _save_cache(path: Path, bars: list[Bar]) -> None:
    path.write_text(
        json.dumps([b.__dict__ for b in bars], ensure_ascii=False),
        encoding="utf-8",
    )


def fetch_klines(
    client: httpx.Client, symbol: str, start_ms: int, end_ms: int
) -> list[Bar]:
    cache = _cache_path(symbol, start_ms, end_ms)
    cached = _bars_from_cache(cache)
    if cached is not None:
        return cached

    rows: list[Bar] = []
    cursor = start_ms
    while cursor < end_ms:
        for attempt in range(5):
            try:
                resp = client.get(
                    f"{BASE}/fapi/v1/klines",
                    params={
                        "symbol": symbol,
                        "interval": INTERVAL,
                        "startTime": cursor,
                        "endTime": end_ms,
                        "limit": KLINE_LIMIT,
                    },
                )
                if resp.status_code == 418 or resp.status_code == 429:
                    time.sleep(3.0 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    return rows
                batch = resp.json()
                if isinstance(batch, dict) and batch.get("code"):
                    time.sleep(3.0 * (attempt + 1))
                    continue
                break
            except Exception:
                time.sleep(0.5 * (attempt + 1))
                batch = []
                break
        else:
            break
        if not batch:
            break
        for r in batch:
            rows.append(
                Bar(
                    ot=int(r[0]) // 1000,
                    o=float(r[1]),
                    h=float(r[2]),
                    l=float(r[3]),
                    c=float(r[4]),
                    qvol=float(r[7]),
                )
            )
        last_ot_ms = int(batch[-1][0])
        nxt = last_ot_ms + BAR_SEC * 1000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < KLINE_LIMIT:
            break
        time.sleep(FETCH_SLEEP)
    # dedupe by ot
    by_ot: dict[int, Bar] = {}
    for b in rows:
        by_ot[b.ot] = b
    out = [by_ot[k] for k in sorted(by_ot)]
    if out:
        _save_cache(cache, out)
    return out


def detect_signals(symbol: str, bars: list[Bar]) -> list[Signal]:
    need = QUIET_BARS + WINDOW_BARS
    min_len = max(need, VOLUME_LOOKBACK + 1)
    out: list[Signal] = []
    last_alert_ot = -10**18

    for i in range(min_len, len(bars)):
        # bars[0..i-1] closed history, bars[i] forming
        window = bars[i - WINDOW_BARS + 1 : i + 1]
        quiet = bars[i - need + 1 : i - WINDOW_BARS + 1]
        if len(quiet) < QUIET_BARS or len(window) < WINDOW_BARS:
            continue

        q_hi = max(b.h for b in quiet)
        q_lo = min(b.l for b in quiet)
        if q_lo <= 0:
            continue
        quiet_pct = (q_hi - q_lo) / q_lo * 100.0
        if quiet_pct > QUIET_RANGE_PCT:
            continue

        base_px = bars[i - WINDOW_BARS].c
        last = bars[i]
        if base_px <= 0:
            continue
        price_pct = (last.c - base_px) / base_px * 100.0
        if price_pct < MIN_PRICE_PCT:
            continue

        prior = bars[i - VOLUME_LOOKBACK : i]
        if len(prior) < VOLUME_LOOKBACK:
            continue
        avg_vol = sum(b.qvol for b in prior) / len(prior)
        if avg_vol <= 0 or last.qvol < avg_vol * VOLUME_MULT:
            continue

        if last.ot - last_alert_ot < COOLDOWN_SEC:
            continue

        out.append(
            Signal(
                symbol=symbol,
                ot=last.ot,
                entry=last.c,
                price_pct=price_pct,
                vol_x=last.qvol / avg_vol,
            )
        )
        last_alert_ot = last.ot
    return out


def simulate_trade(
    bars: list[Bar],
    sig_idx: int,
    entry: float,
    leverage: float,
    tp_roi: float,
    sl_roi: float,
) -> dict:
    """
    tp_roi/sl_roi: 마진 ROI % 목표.
    가격 변동 % = ROI / leverage
    """
    # 청산 대략: 유지증거금 무시, 가격 -((1/L)-buffer)
    liq_price_pct = max(0.5, (100.0 / leverage) * 0.9)  # 마진의 ~90% 손실 지점
    tp_price_pct = tp_roi / leverage
    sl_price_pct = min(sl_roi / leverage, liq_price_pct)

    tp_px = entry * (1.0 + tp_price_pct / 100.0)
    sl_px = entry * (1.0 - sl_price_pct / 100.0)
    fee_roi = leverage * TAKER_FEE * 2 * 100.0  # 마진 대비 %

    end = min(sig_idx + 1 + MAX_HOLD_BARS, len(bars))
    for j in range(sig_idx + 1, end):
        b = bars[j]
        hit_sl = b.l <= sl_px
        hit_tp = b.h >= tp_px
        if hit_sl and hit_tp:
            # 같은 봉: 보수적으로 SL 우선
            price_pnl = -sl_price_pct
            return {
                "exit": "sl_ambiguous",
                "roi": price_pnl * leverage - fee_roi,
                "bars_held": j - sig_idx,
            }
        if hit_sl:
            return {
                "exit": "sl",
                "roi": -sl_price_pct * leverage - fee_roi,
                "bars_held": j - sig_idx,
            }
        if hit_tp:
            return {
                "exit": "tp",
                "roi": tp_price_pct * leverage - fee_roi,
                "bars_held": j - sig_idx,
            }

    # 시간 만료: 마지막 종가
    last = bars[end - 1] if end > sig_idx + 1 else bars[sig_idx]
    price_pnl = (last.c - entry) / entry * 100.0
    # 청산 체크
    if price_pnl <= -liq_price_pct:
        return {
            "exit": "liq",
            "roi": -liq_price_pct * leverage - fee_roi,
            "bars_held": end - 1 - sig_idx,
        }
    return {
        "exit": "timeout",
        "roi": price_pnl * leverage - fee_roi,
        "bars_held": end - 1 - sig_idx,
    }


def summarize(rois: list[float], exits: list[str]) -> dict:
    n = len(rois)
    if n == 0:
        return {"n": 0}
    wins = [r for r in rois if r > 0]
    losses = [r for r in rois if r <= 0]
    mean = sum(rois) / n
    # 기대값(트레이드당), 승률, PF
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    # 샤프 근사 (트레이드 단위)
    if n > 1:
        var = sum((r - mean) ** 2 for r in rois) / (n - 1)
        std = math.sqrt(var)
        sharpe = mean / std if std > 0 else 0.0
    else:
        sharpe = 0.0
    exit_counts: dict[str, int] = {}
    for e in exits:
        exit_counts[e] = exit_counts.get(e, 0) + 1
    return {
        "n": n,
        "mean_roi": mean,
        "median_roi": sorted(rois)[n // 2],
        "win_rate": len(wins) / n * 100.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "profit_factor": pf,
        "sharpe": sharpe,
        "total_roi_sum": sum(rois),  # 동일 마진 재투자 없이 합산
        "p05": sorted(rois)[max(0, int(n * 0.05))],
        "p95": sorted(rois)[min(n - 1, int(n * 0.95))],
        "exits": exit_counts,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = end_ms - LOOKBACK_DAYS * 24 * 3600 * 1000

    print(f"기간: 최근 {LOOKBACK_DAYS}일 | interval={INTERVAL}")
    with httpx.Client(timeout=60.0) as client:
        symbols = list_symbols(client)
        print(f"심볼: {len(symbols)}개 (메이저 제외)")

        # fetch
        all_bars: dict[str, list[Bar]] = {}
        done = 0

        def one(sym: str) -> tuple[str, list[Bar]]:
            with httpx.Client(timeout=60.0) as c:
                return sym, fetch_klines(c, sym, start_ms, end_ms)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS_FETCH) as pool:
            futs = [pool.submit(one, s) for s in symbols]
            for fut in as_completed(futs):
                sym, bars = fut.result()
                done += 1
                if len(bars) >= QUIET_BARS + WINDOW_BARS + VOLUME_LOOKBACK + 5:
                    all_bars[sym] = bars
                if done % 40 == 0 or done == len(symbols):
                    print(f"  다운로드 {done}/{len(symbols)}  ok={len(all_bars)}")

    # signals
    signals: list[tuple[Signal, str, int]] = []  # sig, symbol, bar_idx
    bar_index: dict[str, dict[int, int]] = {}
    for sym, bars in all_bars.items():
        bar_index[sym] = {b.ot: i for i, b in enumerate(bars)}
        for sig in detect_signals(sym, bars):
            idx = bar_index[sym].get(sig.ot)
            if idx is None:
                continue
            signals.append((sig, sym, idx))

    signals.sort(key=lambda x: x[0].ot)
    print(f"신호 수: {len(signals)}")
    min_trades = 30
    if len(signals) < min_trades:
        print(
            f"⚠️ 신호 {len(signals)}건 — 통계 신뢰도 낮음 (권장 ≥{min_trades}건). "
            "API rate limit 시 data/backtest_cache 재사용 또는 몇 시간 후 재실행."
        )
    if not signals:
        print("신호 없음 — 종료")
        return

    # grid
    results: list[dict] = []
    total_combos = len(LEVERAGES) * len(TP_ROIS) * len(SL_ROIS)
    combo_i = 0
    for lev in LEVERAGES:
        for tp in TP_ROIS:
            for sl in SL_ROIS:
                combo_i += 1
                # SL ROI가 TP보다 크면 비대칭이 너무 나빠서 스킵하진 않음 — EV로 판단
                rois: list[float] = []
                exits: list[str] = []
                for sig, sym, idx in signals:
                    bars = all_bars[sym]
                    # 청산거리보다 TP가 말이 안 되는 경우도 시뮬
                    tr = simulate_trade(bars, idx, sig.entry, lev, float(tp), float(sl))
                    rois.append(tr["roi"])
                    exits.append(tr["exit"])
                stats = summarize(rois, exits)
                stats.update(
                    {
                        "leverage": lev,
                        "tp_roi": tp,
                        "sl_roi": sl,
                        "tp_price_pct": tp / lev,
                        "sl_price_pct": sl / lev,
                        "score": stats.get("mean_roi", -999) * math.sqrt(stats.get("n", 0)),
                    }
                )
                results.append(stats)
                if combo_i % 50 == 0:
                    print(f"  그리드 {combo_i}/{total_combos}")

    results.sort(key=lambda r: r.get("mean_roi", -999), reverse=True)

    # also rank by expectancy * sqrt(n) and by profit factor with min trades
    by_score = sorted(results, key=lambda r: r.get("score", -999), reverse=True)
    by_pf = sorted(
        [r for r in results if r.get("n", 0) >= 30],
        key=lambda r: (r.get("profit_factor", 0), r.get("mean_roi", -999)),
        reverse=True,
    )

    sig_meta = [
        {
            "symbol": s.symbol,
            "ot": s.ot,
            "entry": s.entry,
            "price_pct": s.price_pct,
            "vol_x": s.vol_x,
            "ts": datetime.fromtimestamp(s.ot, tz=timezone.utc).isoformat(),
        }
        for s, _, _ in signals
    ]

    payload = {
        "generated_at": now.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "interval": INTERVAL,
        "symbols_fetched": len(all_bars),
        "signals": len(signals),
        "params": {
            "window_bars": WINDOW_BARS,
            "min_price_pct": MIN_PRICE_PCT,
            "volume_mult": VOLUME_MULT,
            "quiet_bars": QUIET_BARS,
            "quiet_range_pct": QUIET_RANGE_PCT,
            "cooldown_sec": COOLDOWN_SEC,
            "max_hold_bars": MAX_HOLD_BARS,
            "taker_fee": TAKER_FEE,
            "entry": "signal bar close (forming-bar proxy)",
            "same_bar_rule": "SL first (conservative)",
        },
        "top_by_mean_roi": results[:25],
        "top_by_score": by_score[:25],
        "top_by_pf": by_pf[:25],
        "all_results": results,
        "signal_sample": sig_meta[:50],
        "signal_symbols": sorted({s.symbol for s, _, _ in signals}),
    }

    out_path = OUT_DIR / f"volume_spike_tpsl_{now.strftime('%Y%m%d_%H%M%S')}.json"
    latest = OUT_DIR / "volume_spike_tpsl_latest.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    best = results[0]
    print("\n=== BEST mean ROI/trade ===")
    print(
        f"lev={best['leverage']}x  TP={best['tp_roi']}%ROI(~{best['tp_price_pct']:.2f}%px)  "
        f"SL={best['sl_roi']}%ROI(~{best['sl_price_pct']:.2f}%px)  "
        f"mean={best['mean_roi']:.2f}%  WR={best['win_rate']:.1f}%  "
        f"PF={best['profit_factor']:.2f}  n={best['n']}"
    )
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
