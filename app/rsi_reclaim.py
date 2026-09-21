from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread, local

import httpx

from app.config import RsiMacdCrossRuleConfig
from app.indicators import calc_rsi
from app.timeframes import BINANCE_INTERVAL, floor_open_time


LogFn = Callable[[str], None]
NotifyFn = Callable[[str], None]
_thread_http = local()


@dataclass
class _SymState:
    closes: deque[float]
    forming_ot: int | None = None
    forming_close: float | None = None
    last_rsi: float | None = None
    armed: bool = False  # oversold 이하로 내려간 적 있음
    last_alert_mono: float = 0.0


class FuturesRsiReclaimWatcher:
    """
    설정봉 RSI가 oversold 이하 → reclaim 이상으로 올라오는 순간 즉시 알람.
    봉 마감을 기다리지 않고, 형성 중 봉 종가(현재가)로 주기적으로 재계산.
    """

    BASE = "https://fapi.binance.com"

    def __init__(
        self,
        cfg: RsiMacdCrossRuleConfig,
        rsi_period: int,
        notify: NotifyFn,
        log: LogFn | None = None,
        global_poll: float = 60.0,
    ) -> None:
        self.cfg = cfg
        self.rsi_period = max(2, int(rsi_period))
        self.global_poll = float(global_poll)
        self.notify = notify
        self._log = log or (lambda _m: None)
        self._http = httpx.Client(timeout=30.0)
        self._stop = Event()
        self._thread: Thread | None = None
        self._symbols: list[str] = []
        self._states: dict[str, _SymState] = {}
        self._symbols_loaded_at = 0.0

    def apply_config(
        self,
        cfg: RsiMacdCrossRuleConfig,
        rsi_period: int | None = None,
        global_poll: float | None = None,
    ) -> bool:
        """timeframe이 바뀌면 True (워처 재시작 필요)."""
        prev_tf = self.cfg.timeframe
        self.cfg = cfg
        if rsi_period is not None:
            self.rsi_period = max(2, int(rsi_period))
        if global_poll is not None:
            self.global_poll = float(global_poll)
        return cfg.timeframe != prev_tf and bool(self._thread and self._thread.is_alive())

    def _poll_seconds(self) -> float:
        if self.cfg.follow_global_poll:
            return max(15.0, float(self.global_poll))
        return max(15.0, float(self.cfg.poll_seconds))

    def _new_state(self) -> _SymState:
        return _SymState(closes=deque(maxlen=max(self.cfg.history_bars, self.rsi_period + 5)))

    def start(self) -> None:
        if not self.cfg.enabled or not self.cfg.live:
            self._log("RSI 재돌파 알람: OFF")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="rsi-reclaim", daemon=True)
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
            self._log(f"RSI 재돌파: 미지원 timeframe={self.cfg.timeframe}")
            return
        poll = self._poll_seconds()
        self._log(
            f"RSI 재돌파 감시 시작 futures {self.cfg.timeframe} (형성중봉) | "
            f"RSI≤{self.cfg.oversold:g} → ≥{self.cfg.reclaim:g} | "
            f"poll={poll:g}s"
            f"{' (공통)' if self.cfg.follow_global_poll else ''} "
            f"cooldown={self.cfg.cooldown_seconds}s"
        )
        try:
            self._ensure_symbols(force=True)
            self._bootstrap(interval)
        except Exception as exc:
            self._log(f"RSI 재돌파 초기화 실패: {exc}")

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.cfg.enabled:
                    self._ensure_symbols(force=False)
                    self._scan()
            except Exception as exc:
                self._log(f"RSI 재돌파 스캔 오류: {exc}")
            wait = self._poll_seconds() - (time.monotonic() - started)
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
            self._states.setdefault(sym, self._new_state())
        alive = set(symbols)
        for key in list(self._states):
            if key not in alive:
                del self._states[key]
        self._log(f"RSI 재돌파 대상: {len(symbols)}개 (USDT-M 무기한 전체)")

    def _bootstrap(self, interval: str) -> None:
        need = self.cfg.history_bars
        self._log(f"RSI 재돌파 백필 시작 ({need}봉 × {len(self._symbols)}심볼)...")
        done = 0
        hits = 0

        def one(sym: str) -> tuple[str, list[tuple[int, float]] | None]:
            return sym, self._fetch_klines(sym, interval, need)

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s) for s in self._symbols]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                try:
                    sym, rows = fut.result()
                except Exception:
                    continue
                done += 1
                if not rows or len(rows) < self.rsi_period + 2:
                    continue
                st = self._states.setdefault(sym, self._new_state())
                st.closes.clear()
                closed, forming = rows[:-1], rows[-1]
                for ot, close in closed[-self.cfg.history_bars :]:
                    st.closes.append(close)
                st.forming_ot, st.forming_close = forming
                rsi = calc_rsi(list(st.closes) + [forming[1]], self.rsi_period)
                st.last_rsi = rsi
                st.armed = bool(rsi is not None and rsi <= self.cfg.oversold)
                hits += 1
                if done % 100 == 0:
                    self._log(f"  RSI 백필 {done}/{len(self._symbols)}")
        self._log(f"RSI 재돌파 백필 완료: {hits}/{len(self._symbols)} (이후 1분 갱신·즉시 알람)")

    def _scan(self) -> None:
        wanted = set(self._symbols)
        try:
            data = self._http.get(f"{self.BASE}/fapi/v1/ticker/price").json()
        except Exception as exc:
            self._log(f"RSI 재돌파 시세 조회 실패: {exc}")
            return
        if not isinstance(data, list):
            return
        now = time.time()
        ot = floor_open_time(now, self.cfg.timeframe)
        now_m = time.monotonic()
        alerts = 0
        checked = 0
        for row in data:
            sym = row.get("symbol")
            if sym not in wanted:
                continue
            try:
                px = float(row["price"])
            except (KeyError, TypeError, ValueError):
                continue
            st = self._states.setdefault(sym, self._new_state())
            if st.forming_ot is not None and ot > st.forming_ot and st.forming_close is not None:
                st.closes.append(st.forming_close)
            st.forming_ot = ot
            st.forming_close = px
            if len(st.closes) < self.rsi_period:
                continue
            rsi = calc_rsi(list(st.closes) + [px], self.rsi_period)
            if rsi is None:
                continue
            checked += 1
            prev = st.last_rsi
            st.last_rsi = rsi
            if rsi <= self.cfg.oversold:
                st.armed = True
            if prev is None:
                continue
            if not st.armed:
                continue
            # oversold 이하로 내려간 뒤, reclaim 레벨을 상향 돌파할 때
            if not (prev < self.cfg.reclaim <= rsi):
                continue
            if now_m - st.last_alert_mono < self.cfg.cooldown_seconds:
                continue
            self._emit(sym, px, rsi, prev)
            st.last_alert_mono = now_m
            st.armed = False
            alerts += 1
        self._log(f"RSI 재돌파 스캔: checked≈{checked} alerts={alerts}")

    def _emit(self, symbol: str, price: float, rsi: float, prev: float) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"[RSI 재돌파] Binance Futures {self.cfg.timeframe} (형성중)\n"
            f"{symbol}\n"
            f"RSI {prev:.2f} → {rsi:.2f}  "
            f"(≤{self.cfg.oversold:g} 후 ≥{self.cfg.reclaim:g})\n"
            f"price={_fmt_price(price)}  {ts}"
        )
        try:
            self.notify(msg)
        except Exception as exc:
            self._log(f"RSI 재돌파 전송 실패 {symbol}: {exc}")
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
