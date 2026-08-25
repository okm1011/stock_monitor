from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread, local

import httpx

from app.config import AccumulationRuleConfig


LogFn = Callable[[str], None]
NotifyFn = Callable[[str], None]
_thread_http = local()


@dataclass
class _Bar:
    ot: int
    high: float
    low: float
    close: float
    qvol: float


@dataclass
class _OiPt:
    ts: int
    value: float


@dataclass
class _SymState:
    last_alert_mono: float = 0.0


class FuturesAccumulationWatcher:
    """
    프리랠리 관심: 아직 안 터진 알트 중에서
    OI↑ 필수 + (타이트 박스 / 거래량 회복 / BTC 대비 상대강도) 중 1개 이상.
    """

    BASE = "https://fapi.binance.com"

    def __init__(
        self,
        cfg: AccumulationRuleConfig,
        notify: NotifyFn,
        log: LogFn | None = None,
    ) -> None:
        self.cfg = cfg
        self.notify = notify
        self._log = log or (lambda _m: None)
        self._http = httpx.Client(timeout=30.0)
        self._stop = Event()
        self._thread: Thread | None = None
        self._symbols: list[str] = []
        self._states: dict[str, _SymState] = {}
        self._symbols_loaded_at = 0.0
        self._exclude = {b.upper() for b in cfg.exclude_bases}
        self._skip_first_alerts = True

    def apply_config(self, cfg: AccumulationRuleConfig) -> None:
        self.cfg = cfg
        self._exclude = {b.upper() for b in cfg.exclude_bases}

    def start(self) -> None:
        if not self.cfg.enabled:
            self._log("알트 신호1 OI: OFF")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._skip_first_alerts = True
        self._thread = Thread(target=self._run, name="accum-alert", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15.0)
        self._thread = None
        try:
            self._http.close()
        except Exception:
            pass

    def _run(self) -> None:
        self._log(
            f"알트 신호1 OI 감시 시작 futures 1d | "
            f"gate range<{self.cfg.range_pct:g}%/{self.cfg.range_days}d "
            f"tight<{self.cfg.tight_range_pct:g}% "
            f"OI≥+{self.cfg.oi_change_pct:g}%/{self.cfg.oi_days}d (필수) "
            f"vol×{self.cfg.vol_mult:g} RS≥{self.cfg.btc_rs_pct:g}% vs BTC | "
            f"minOI={_fmt_vol(self.cfg.min_oi_usdt)} "
            f"7d {self.cfg.trend_min_pct:g}~{self.cfg.trend_max_pct:g}% | "
            f"poll={self.cfg.poll_seconds:g}s cooldown={self.cfg.cooldown_seconds}s"
        )
        try:
            self._ensure_symbols(force=True)
        except Exception as exc:
            self._log(f"알트 신호1 초기화 실패: {exc}")

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.cfg.enabled:
                    self._ensure_symbols(force=False)
                    self._scan()
            except Exception as exc:
                self._log(f"알트 신호1 스캔 오류: {exc}")

            wait = self.cfg.poll_seconds - (time.monotonic() - started)
            end = time.monotonic() + max(1.0, wait)
            while not self._stop.is_set() and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))

    def _base_asset(self, symbol: str) -> str:
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol

    def _ensure_symbols(self, force: bool) -> None:
        age_h = (time.monotonic() - self._symbols_loaded_at) / 3600.0
        if not force and self._symbols and age_h < self.cfg.symbol_refresh_hours:
            return
        info = self._http.get(f"{self.BASE}/fapi/v1/exchangeInfo").json()
        symbols: list[str] = []
        skipped = 0
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            sym = s["symbol"]
            base = (s.get("baseAsset") or self._base_asset(sym)).upper()
            if base in self._exclude:
                skipped += 1
                continue
            symbols.append(sym)
        symbols.sort()
        self._symbols = symbols
        self._symbols_loaded_at = time.monotonic()
        for sym in symbols:
            self._states.setdefault(sym, _SymState())
        alive = set(symbols)
        for key in list(self._states):
            if key not in alive:
                del self._states[key]
        self._log(
            f"알트 신호1 대상: {len(symbols)}개 "
            f"(메이저/스테이블 제외 {skipped}개, exclude={len(self._exclude)})"
        )

    def _scan(self) -> None:
        now_m = time.monotonic()
        checked = 0
        hits = 0
        alerted = 0
        skip_notify = self._skip_first_alerts
        need_klines = max(
            self.cfg.range_days,
            self.cfg.trend_days + 1,
            self.cfg.vol_recent_bars + self.cfg.vol_lookback,
        ) + 2

        btc_trend = self._btc_trend()

        def one(sym: str) -> tuple[str, list[_Bar] | None, list[_OiPt] | None]:
            bars = self._fetch_klines(sym, need_klines)
            oi = self._fetch_oi_hist(sym, max(self.cfg.oi_days, 7))
            return sym, bars, oi

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s) for s in self._symbols]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                try:
                    sym, bars, oi = fut.result()
                except Exception:
                    continue
                if not bars or not oi:
                    continue
                checked += 1
                ok, meta = self._passes(bars, oi, btc_trend)
                if not ok or not meta:
                    continue
                hits += 1
                st = self._states.setdefault(sym, _SymState())
                if skip_notify:
                    continue
                if now_m - st.last_alert_mono < self.cfg.cooldown_seconds:
                    continue
                self._emit(sym, meta)
                st.last_alert_mono = now_m
                alerted += 1

        if skip_notify:
            self._skip_first_alerts = False
            self._log(
                f"알트 신호1 기동 스캔: checked≈{checked} 후보={hits} "
                f"(첫 스캔 알람 생략, 다음부터 전송)"
            )
            return
        self._log(f"알트 신호1 스캔: checked≈{checked} 후보={hits} alerts={alerted}")

    def _btc_trend(self) -> float | None:
        bars = self._fetch_klines("BTCUSDT", self.cfg.trend_days + 2)
        if not bars or len(bars) < self.cfg.trend_days + 1:
            return None
        base = bars[-(self.cfg.trend_days + 1)].close
        last = bars[-1].close
        if base <= 0:
            return None
        return (last - base) / base * 100.0

    def _passes(
        self,
        bars: list[_Bar],
        oi: list[_OiPt],
        btc_trend: float | None,
    ) -> tuple[bool, dict | None]:
        cfg = self.cfg
        need_bars = max(
            cfg.range_days,
            cfg.trend_days + 1,
            cfg.vol_recent_bars + cfg.vol_lookback,
        )
        if len(bars) < need_bars or len(oi) < cfg.oi_days:
            return False, None

        window = bars[-cfg.range_days :]
        lo = min(b.low for b in window)
        hi = max(b.high for b in window)
        if lo <= 0:
            return False, None
        range_pct = (hi - lo) / lo * 100.0
        if range_pct > cfg.range_pct:
            return False, None

        base_px = bars[-(cfg.trend_days + 1)].close
        last = bars[-1]
        if base_px <= 0:
            return False, None
        trend_pct = (last.close - base_px) / base_px * 100.0
        if trend_pct < cfg.trend_min_pct or trend_pct > cfg.trend_max_pct:
            return False, None

        oi_win = oi[-cfg.oi_days :]
        oi0 = oi_win[0].value
        oi1 = oi_win[-1].value
        if oi0 <= 0 or oi1 < cfg.min_oi_usdt:
            return False, None
        oi_pct = (oi1 - oi0) / oi0 * 100.0

        prior = bars[-(cfg.vol_recent_bars + cfg.vol_lookback) : -cfg.vol_recent_bars]
        recent = bars[-cfg.vol_recent_bars :]
        avg_vol = sum(b.qvol for b in prior) / len(prior) if prior else 0.0
        rec_vol = sum(b.qvol for b in recent) / len(recent) if recent else 0.0
        vol_x = rec_vol / avg_vol if avg_vol > 0 else 0.0

        rs_pct = None if btc_trend is None else trend_pct - btc_trend

        tight_ok = range_pct <= cfg.tight_range_pct
        oi_ok = oi_pct >= cfg.oi_change_pct
        vol_ok = vol_x >= cfg.vol_mult
        rs_ok = rs_pct is not None and cfg.btc_rs_pct > 0 and rs_pct >= cfg.btc_rs_pct

        if not oi_ok:
            return False, None
        if not (tight_ok or vol_ok or rs_ok):
            return False, None

        flags = [n for n, ok in (("tight", tight_ok), ("oi", oi_ok), ("vol", vol_ok), ("rs", rs_ok)) if ok]
        return True, {
            "range_pct": range_pct,
            "trend_pct": trend_pct,
            "oi_pct": oi_pct,
            "oi_now": oi1,
            "vol_x": vol_x,
            "rs_pct": rs_pct,
            "flags": flags,
            "close": last.close,
            "ot": last.ot,
        }

    def _emit(self, symbol: str, meta: dict) -> None:
        ts = datetime.fromtimestamp(meta["ot"], tz=timezone.utc).strftime("%Y-%m-%d UTC")
        flags = "+".join(meta.get("flags") or [])
        rs = meta.get("rs_pct")
        rs_s = f"{rs:+.1f}%" if isinstance(rs, (int, float)) else "n/a"
        msg = (
            f"[알트코인 신호 1] OI 변동\n"
            f"{symbol}\n"
            f"관망/관심 (즉시 진입 금지)  hit={flags}\n"
            f"range={meta['range_pct']:.1f}% / {self.cfg.range_days}일  "
            f"(gate<{self.cfg.range_pct:g}% tight<{self.cfg.tight_range_pct:g}%)\n"
            f"OI={meta['oi_pct']:+.1f}% / {self.cfg.oi_days}일  "
            f"now={_fmt_vol(meta['oi_now'])} "
            f"(≥{_fmt_vol(self.cfg.min_oi_usdt)}, ≥+{self.cfg.oi_change_pct:g}%)\n"
            f"vol=x{meta['vol_x']:.2f} (최근{self.cfg.vol_recent_bars}d / "
            f"직전{self.cfg.vol_lookback}d, ≥{self.cfg.vol_mult:g})\n"
            f"{self.cfg.trend_days}d price={meta['trend_pct']:+.1f}%  "
            f"vsBTC={rs_s} (≥{self.cfg.btc_rs_pct:g}%)\n"
            f"close={_fmt_price(meta['close'])}  bar={ts}"
        )
        try:
            self.notify(msg)
        except Exception as exc:
            self._log(f"알트 신호1 전송 실패 {symbol}: {exc}")
        self._log(msg.replace("\n", " | "))

    def _worker_http(self) -> httpx.Client:
        client = getattr(_thread_http, "client", None)
        if client is None:
            client = httpx.Client(timeout=30.0)
            _thread_http.client = client
        return client

    def _fetch_klines(self, symbol: str, limit: int) -> list[_Bar] | None:
        http = self._worker_http()
        for attempt in range(3):
            try:
                resp = http.get(
                    f"{self.BASE}/fapi/v1/klines",
                    params={"symbol": symbol, "interval": "1d", "limit": min(limit, 30)},
                )
                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    return None
                out: list[_Bar] = []
                for r in resp.json():
                    out.append(
                        _Bar(
                            ot=int(r[0]) // 1000,
                            high=float(r[2]),
                            low=float(r[3]),
                            close=float(r[4]),
                            qvol=float(r[7]),
                        )
                    )
                return out
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        return None

    def _fetch_oi_hist(self, symbol: str, days: int) -> list[_OiPt] | None:
        http = self._worker_http()
        for attempt in range(3):
            try:
                resp = http.get(
                    f"{self.BASE}/futures/data/openInterestHist",
                    params={"symbol": symbol, "period": "1d", "limit": min(days, 30)},
                )
                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    return None
                rows = resp.json()
                if not isinstance(rows, list):
                    return None
                pts: list[_OiPt] = []
                for r in rows:
                    try:
                        pts.append(
                            _OiPt(
                                ts=int(r["timestamp"]) // 1000,
                                value=float(r["sumOpenInterestValue"]),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                pts.sort(key=lambda p: p.ts)
                return pts
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        return None


def _fmt_vol(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{v:.2f}"


def _fmt_price(price: float) -> str:
    if abs(price) >= 1000:
        return f"{price:,.2f}"
    if abs(price) >= 1:
        return f"{price:,.4f}"
    return f"{price:.6f}"
