from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread

import httpx

from app.config import VolumeSpikeRuleConfig
from app.timeframes import BINANCE_INTERVAL


LogFn = Callable[[str], None]
NotifyFn = Callable[[str], None]


@dataclass
class _Bar:
    ot: int
    open: float
    high: float
    low: float
    close: float
    qvol: float


@dataclass
class _SymbolState:
    bars: deque[_Bar]
    last_closed_ot: int | None = None
    last_alert_mono: float = 0.0


class FuturesVolumeSpikeWatcher:
    """
    잡코인 펌프 초입 (필터 1∧2∧3):
    1) 최근 window 가격 급등 + 최신봉 거래량 배수
    2) 직전 quiet 구간 횡보(고저 폭 제한)
    3) 메이저 제외한 선물 USDT 퍼페추얼
    """

    BASE = "https://fapi.binance.com"

    def __init__(
        self,
        cfg: VolumeSpikeRuleConfig,
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
        self._states: dict[str, _SymbolState] = {}
        self._symbols_loaded_at = 0.0
        self._exclude = {b.upper() for b in cfg.exclude_bases}

    def _new_state(self) -> _SymbolState:
        return _SymbolState(bars=deque(maxlen=self.cfg.history_bars))

    def start(self) -> None:
        if not self.cfg.enabled:
            self._log("펌프 초입 알람: OFF")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="pump-alert", daemon=True)
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
        interval = BINANCE_INTERVAL.get(self.cfg.timeframe)
        if not interval:
            self._log(f"펌프 초입: 미지원 timeframe={self.cfg.timeframe}")
            return

        self._log(
            f"펌프 초입 감시 시작 futures {self.cfg.timeframe} | "
            f"price≥{self.cfg.min_price_pct:g}%/{self.cfg.window_bars}봉 + "
            f"vol×{self.cfg.volume_mult:g} | "
            f"quiet<{self.cfg.quiet_range_pct:g}%/{self.cfg.quiet_bars}봉 | "
            f"cooldown={self.cfg.cooldown_seconds}s"
        )

        try:
            self._ensure_symbols(force=True)
            self._bootstrap(interval)
        except Exception as exc:
            self._log(f"펌프 초입 초기화 실패: {exc}")

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.cfg.enabled:
                    self._ensure_symbols(force=False)
                    self._scan(interval)
            except Exception as exc:
                self._log(f"펌프 초입 스캔 오류: {exc}")

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
            self._states.setdefault(sym, self._new_state())
        alive = set(symbols)
        for key in list(self._states):
            if key not in alive:
                del self._states[key]
        self._log(
            f"펌프 초입 대상: {len(symbols)}개 "
            f"(메이저 제외 {skipped}개, exclude={len(self._exclude)})"
        )

    def _bootstrap(self, interval: str) -> None:
        need = self.cfg.history_bars
        self._log(f"펌프 초입 백필 시작 ({need}봉 × {len(self._symbols)}심볼)...")
        done = 0
        hits = 0

        def one(sym: str) -> tuple[str, list[_Bar] | None]:
            return sym, self._fetch_klines(sym, interval, need)

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s) for s in self._symbols]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                sym, rows = fut.result()
                done += 1
                if not rows or len(rows) < 2:
                    continue
                closed = rows[:-1]
                st = self._states[sym]
                st.bars.clear()
                for bar in closed[-self.cfg.history_bars :]:
                    st.bars.append(bar)
                if closed:
                    st.last_closed_ot = closed[-1].ot
                    hits += 1
                if done % 100 == 0:
                    self._log(f"  펌프 백필 {done}/{len(self._symbols)}")

        self._log(f"펌프 초입 백필 완료: {hits}/{len(self._symbols)} (알람은 이후 새 봉부터)")

    def _scan(self, interval: str) -> None:
        spiked = 0
        checked = 0
        now_m = time.monotonic()

        def one(sym: str) -> tuple[str, list[_Bar] | None]:
            return sym, self._fetch_klines(sym, interval, 3)

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s) for s in self._symbols]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                sym, rows = fut.result()
                if not rows or len(rows) < 2:
                    continue
                checked += 1
                closed = rows[:-1]
                st = self._states.setdefault(sym, self._new_state())
                for bar in closed:
                    if st.last_closed_ot is not None and bar.ot <= st.last_closed_ot:
                        continue
                    st.bars.append(bar)
                    st.last_closed_ot = bar.ot
                    ok, meta = self._passes_filters(st)
                    if not ok or not meta:
                        continue
                    if now_m - st.last_alert_mono < self.cfg.cooldown_seconds:
                        continue
                    self._emit(sym, meta)
                    st.last_alert_mono = now_m
                    spiked += 1

        self._log(f"펌프 스캔: checked≈{checked} alerts={spiked}")

    def _passes_filters(self, st: _SymbolState) -> tuple[bool, dict | None]:
        cfg = self.cfg
        bars = list(st.bars)
        need = cfg.quiet_bars + cfg.window_bars
        if len(bars) < max(need, cfg.volume_lookback + 1):
            return False, None

        window = bars[-cfg.window_bars :]
        quiet = bars[-(need) : -cfg.window_bars]
        if len(quiet) < cfg.quiet_bars:
            return False, None

        # 2) 횡보: 직전 quiet 구간 고저 폭
        q_hi = max(b.high for b in quiet)
        q_lo = min(b.low for b in quiet)
        if q_lo <= 0:
            return False, None
        quiet_pct = (q_hi - q_lo) / q_lo * 100.0
        if quiet_pct > cfg.quiet_range_pct:
            return False, None

        # 1) 가격: window 직전 종가 대비 최신 종가
        base_px = bars[-(cfg.window_bars + 1)].close
        last = window[-1]
        if base_px <= 0:
            return False, None
        price_pct = (last.close - base_px) / base_px * 100.0
        if price_pct < cfg.min_price_pct:
            return False, None

        # 1) 거래량: 최신 봉 vs 직전 volume_lookback 평균
        prior = bars[-(cfg.volume_lookback + 1) : -1]
        if len(prior) < cfg.volume_lookback:
            return False, None
        avg_vol = sum(b.qvol for b in prior) / len(prior)
        if avg_vol <= 0 or last.qvol < avg_vol * cfg.volume_mult:
            return False, None

        return True, {
            "price_pct": price_pct,
            "quiet_pct": quiet_pct,
            "vol": last.qvol,
            "avg_vol": avg_vol,
            "vol_x": last.qvol / avg_vol,
            "close": last.close,
            "ot": last.ot,
        }

    def _emit(self, symbol: str, meta: dict) -> None:
        ts = datetime.fromtimestamp(meta["ot"], tz=timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"[펌프 초입] Binance Futures {self.cfg.timeframe}\n"
            f"{symbol}\n"
            f"price={meta['price_pct']:+.1f}% / {self.cfg.window_bars}봉  "
            f"(≥{self.cfg.min_price_pct:g}%)\n"
            f"vol=x{meta['vol_x']:.1f} ({_fmt_vol(meta['vol'])} / avg {_fmt_vol(meta['avg_vol'])})\n"
            f"quiet={meta['quiet_pct']:.1f}% 고저 "
            f"(<{self.cfg.quiet_range_pct:g}% / {self.cfg.quiet_bars}봉)\n"
            f"close={_fmt_price(meta['close'])}  bar={ts}"
        )
        try:
            self.notify(msg)
        except Exception as exc:
            self._log(f"펌프 알람 전송 실패 {symbol}: {exc}")
        self._log(msg.replace("\n", " | "))

    def _fetch_klines(self, symbol: str, interval: str, limit: int) -> list[_Bar] | None:
        for attempt in range(3):
            try:
                resp = self._http.get(
                    f"{self.BASE}/fapi/v1/klines",
                    params={"symbol": symbol, "interval": interval, "limit": limit},
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
                            open=float(r[1]),
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
