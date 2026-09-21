from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Thread, local

import httpx

from app.config import Ma20ApproachRuleConfig, Ma20TfConfig
from app.indicators import calc_sma
from app.timeframes import BINANCE_INTERVAL, floor_open_time


LogFn = Callable[[str], None]
NotifyFn = Callable[[str], None]
_thread_http = local()


@dataclass
class _TfState:
    closes: deque[float]
    forming_ot: int | None = None
    forming_close: float | None = None
    last_dist: float | None = None
    last_alert_mono: float = 0.0


@dataclass
class _SymBundle:
    by_tf: dict[str, _TfState] = field(default_factory=dict)


class FuturesMa20ApproachWatcher:
    """
    가격이 SMA20 위에서 아래로 근접(이격 ≤ proximity%)할 때 알람.
    1h / 4h / 1d 각각 독립 on/off·근접%.
    """

    BASE = "https://fapi.binance.com"

    def __init__(
        self,
        cfg: Ma20ApproachRuleConfig,
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
        self._states: dict[str, _SymBundle] = {}
        self._symbols_loaded_at = 0.0
        self._skip_first_alerts = True

    def apply_config(self, cfg: Ma20ApproachRuleConfig) -> None:
        self.cfg = cfg

    def start(self) -> None:
        if not self.cfg.enabled or not self.cfg.enabled_timeframes():
            self._log("MA20 근접 알람: OFF")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._skip_first_alerts = True
        self._thread = Thread(target=self._run, name="ma20-approach", daemon=True)
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
        tfs = self.cfg.enabled_timeframes()
        if not tfs:
            self._log("MA20 근접: 활성화된 타임프레임 없음")
            return
        parts = [f"{tf}≤{c.proximity_pct:g}%" for tf, c in tfs]
        self._log(
            f"MA20 근접 감시 futures SMA{self.cfg.period} (형성중) | "
            f"{', '.join(parts)} | "
            f"poll={self.cfg.poll_seconds:g}s cooldown={self.cfg.cooldown_seconds}s"
        )
        try:
            self._ensure_symbols(force=True)
            self._bootstrap()
        except Exception as exc:
            self._log(f"MA20 근접 초기화 실패: {exc}")

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.cfg.enabled:
                    self._ensure_symbols(force=False)
                    self._scan()
            except Exception as exc:
                self._log(f"MA20 근접 스캔 오류: {exc}")
            wait = self.cfg.poll_seconds - (time.monotonic() - started)
            end = time.monotonic() + max(1.0, wait)
            while not self._stop.is_set() and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))

    def _ensure_symbols(self, force: bool) -> None:
        age_h = (time.monotonic() - self._symbols_loaded_at) / 3600.0
        if not force and self._symbols and age_h < self.cfg.symbol_refresh_hours:
            return
        info = self._http.get(f"{self.BASE}/fapi/v1/exchangeInfo").json()
        symbols: list[str] = []
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            symbols.append(s["symbol"])
        symbols.sort()
        self._symbols = symbols
        self._symbols_loaded_at = time.monotonic()
        for sym in symbols:
            self._states.setdefault(sym, _SymBundle())
        alive = set(symbols)
        for key in list(self._states):
            if key not in alive:
                del self._states[key]
        self._log(f"MA20 근접 대상: {len(symbols)}개 (USDT-M 무기한 전체)")
        if not force and symbols:
            # 목록 갱신 시 신규 심볼 백필
            self._bootstrap(only_missing=True)

    def _tf_state(self, sym: str, tf: str) -> _TfState:
        bundle = self._states.setdefault(sym, _SymBundle())
        st = bundle.by_tf.get(tf)
        if st is None:
            st = _TfState(closes=deque(maxlen=max(self.cfg.period + 10, 40)))
            bundle.by_tf[tf] = st
        return st

    def _bootstrap(self, only_missing: bool = False) -> None:
        tfs = self.cfg.enabled_timeframes()
        if not tfs:
            return
        need = self.cfg.period + 5
        self._log(f"MA20 근접 백필 시작 ({need}봉 × {len(self._symbols)} × {len(tfs)}TF)...")
        done = 0
        hits = 0

        def one(sym: str, tf: str, interval: str) -> tuple[str, str, list[tuple[int, float]] | None]:
            return sym, tf, self._fetch_klines(sym, interval, need)

        jobs: list[tuple[str, str, str]] = []
        for sym in self._symbols:
            for tf, _cfg in tfs:
                interval = BINANCE_INTERVAL.get(tf)
                if not interval:
                    continue
                if only_missing:
                    st = self._states.get(sym, _SymBundle()).by_tf.get(tf)
                    if st is not None and len(st.closes) >= self.cfg.period:
                        continue
                jobs.append((sym, tf, interval))

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s, tf, iv) for s, tf, iv in jobs]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                try:
                    sym, tf, rows = fut.result()
                except Exception:
                    continue
                done += 1
                if not rows or len(rows) < self.cfg.period + 1:
                    continue
                st = self._tf_state(sym, tf)
                st.closes.clear()
                closed, forming = rows[:-1], rows[-1]
                for _ot, close in closed[-(self.cfg.period + 5) :]:
                    st.closes.append(close)
                st.forming_ot, st.forming_close = forming
                ma = calc_sma(list(st.closes) + [forming[1]], self.cfg.period)
                if ma and ma > 0:
                    st.last_dist = (forming[1] - ma) / ma * 100.0
                hits += 1
                if done % 200 == 0:
                    self._log(f"  MA20 백필 {done}/{len(jobs)}")
        self._log(f"MA20 근접 백필 완료: {hits}/{len(jobs)}")

    def _scan(self) -> None:
        tfs = self.cfg.enabled_timeframes()
        if not tfs:
            return
        wanted = set(self._symbols)
        try:
            data = self._http.get(f"{self.BASE}/fapi/v1/ticker/price").json()
        except Exception as exc:
            self._log(f"MA20 근접 시세 조회 실패: {exc}")
            return
        if not isinstance(data, list):
            return

        now = time.time()
        now_m = time.monotonic()
        ot_by_tf = {tf: floor_open_time(now, tf) for tf, _ in tfs}
        alerts = 0
        checked = 0
        skip_notify = self._skip_first_alerts

        for row in data:
            sym = row.get("symbol")
            if sym not in wanted:
                continue
            try:
                px = float(row["price"])
            except (KeyError, TypeError, ValueError):
                continue

            for tf, tfcfg in tfs:
                st = self._tf_state(sym, tf)
                ot = ot_by_tf[tf]
                if st.forming_ot is not None and ot > st.forming_ot and st.forming_close is not None:
                    st.closes.append(st.forming_close)
                st.forming_ot = ot
                st.forming_close = px
                if len(st.closes) < self.cfg.period - 1:
                    continue
                ma = calc_sma(list(st.closes) + [px], self.cfg.period)
                if ma is None or ma <= 0:
                    continue
                dist = (px - ma) / ma * 100.0
                checked += 1
                prev = st.last_dist
                st.last_dist = dist
                if prev is None:
                    continue
                # 위→아래 근접: 이전에 근접밴드보다 멀었다가, 지금 MA 위쪽 근접밴드 진입
                if not (prev > tfcfg.proximity_pct and 0.0 < dist <= tfcfg.proximity_pct):
                    continue
                if skip_notify:
                    continue
                if now_m - st.last_alert_mono < self.cfg.cooldown_seconds:
                    continue
                self._emit(sym, tf, tfcfg, px, ma, dist, prev)
                st.last_alert_mono = now_m
                alerts += 1

        if skip_notify:
            self._skip_first_alerts = False
            self._log(
                f"MA20 근접 기동 스캔: checked≈{checked} "
                f"(첫 스캔 알람 생략)"
            )
            return
        self._log(f"MA20 근접 스캔: checked≈{checked} alerts={alerts}")

    def _emit(
        self,
        symbol: str,
        tf: str,
        tfcfg: Ma20TfConfig,
        price: float,
        ma: float,
        dist: float,
        prev: float,
    ) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"[MA20 근접] Binance Futures {tf}\n"
            f"{symbol}\n"
            f"위에서 SMA{self.cfg.period} 근접  "
            f"이격 {prev:.2f}% → {dist:.2f}%  (≤{tfcfg.proximity_pct:g}%)\n"
            f"price={_fmt_price(price)}  MA={_fmt_price(ma)}  {ts}"
        )
        try:
            self.notify(msg)
        except Exception as exc:
            self._log(f"MA20 근접 전송 실패 {symbol} {tf}: {exc}")
        self._log(msg.replace("\n", " | "))

    def _worker_http(self) -> httpx.Client:
        client = getattr(_thread_http, "client", None)
        if client is None:
            client = httpx.Client(timeout=30.0)
            _thread_http.client = client
        return client

    def _fetch_klines(self, symbol: str, interval: str, limit: int) -> list[tuple[int, float]] | None:
        http = self._worker_http()
        for attempt in range(3):
            try:
                resp = http.get(
                    f"{self.BASE}/fapi/v1/klines",
                    params={"symbol": symbol, "interval": interval, "limit": limit},
                )
                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    return None
                out: list[tuple[int, float]] = []
                for r in resp.json():
                    out.append((int(r[0]) // 1000, float(r[4])))
                return out
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        return None


def _fmt_price(price: float) -> str:
    if abs(price) >= 1000:
        return f"{price:,.2f}"
    if abs(price) >= 1:
        return f"{price:,.4f}"
    return f"{price:.6f}"
