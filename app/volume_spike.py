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
class _SymbolState:
    volumes: deque[float]
    last_closed_ot: int | None = None  # 마지막 처리한 닫힌 봉 open_time (sec)
    last_alert_mono: float = 0.0


class FuturesVolumeSpikeWatcher:
    """
    바이낸스 USDⓈ-M 선물 전체 USDT 퍼페추얼 대상.
    3분봉 기준: 최신 닫힌 봉 quote volume >= 직전 lookback봉 평균 * multiplier 이면 알람.
    심볼별 최근 lookback 볼륨을 메모리에 유지하고, 주기적으로 최신 봉만 갱신.
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

    def _new_state(self) -> _SymbolState:
        return _SymbolState(volumes=deque(maxlen=self.cfg.lookback))

    def start(self) -> None:
        if not self.cfg.enabled:
            self._log("거래량 급증 알람: OFF")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="volume-spike", daemon=True)
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
            self._log(f"거래량 급증: 미지원 timeframe={self.cfg.timeframe}")
            return

        self._log(
            f"거래량 급증 감시 시작 futures {self.cfg.timeframe} "
            f"lookback={self.cfg.lookback} x{self.cfg.multiplier} "
            f"poll={self.cfg.poll_seconds}s cooldown={self.cfg.cooldown_seconds}s"
        )

        try:
            self._ensure_symbols(force=True)
            self._bootstrap(interval)
        except Exception as exc:
            self._log(f"거래량 급증 초기화 실패: {exc}")

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.cfg.enabled:
                    self._ensure_symbols(force=False)
                    self._scan(interval, alert=True)
            except Exception as exc:
                self._log(f"거래량 급증 스캔 오류: {exc}")

            # poll_seconds 간격 유지
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
            self._states.setdefault(sym, self._new_state())
        # 상장폐지 심볼 정리
        alive = set(symbols)
        for key in list(self._states):
            if key not in alive:
                del self._states[key]
        self._log(f"거래량 급증 대상: 선물 USDT 퍼페추얼 {len(symbols)}개")

    def _bootstrap(self, interval: str) -> None:
        need = self.cfg.lookback + 2  # 여유 + 진행중 봉
        self._log(f"거래량 급증 백필 시작 ({need}봉 × {len(self._symbols)}심볼)...")
        done = 0
        hits = 0

        def one(sym: str) -> tuple[str, list[tuple[int, float]] | None]:
            rows = self._fetch_klines(sym, interval, need)
            return sym, rows

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s) for s in self._symbols]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                sym, rows = fut.result()
                done += 1
                if not rows:
                    continue
                closed = rows[:-1] if len(rows) >= 2 else rows
                st = self._states[sym]
                st.volumes.clear()
                for ot, vol in closed[-self.cfg.lookback :]:
                    st.volumes.append(vol)
                if closed:
                    st.last_closed_ot = closed[-1][0]
                    hits += 1
                if done % 100 == 0:
                    self._log(f"  거래량 백필 {done}/{len(self._symbols)}")

        self._log(f"거래량 급증 백필 완료: {hits}/{len(self._symbols)} (알람은 이후 새 봉부터)")

    def _scan(self, interval: str, alert: bool) -> None:
        # 최신 몇 봉만 (닫힌 봉 감지)
        limit = 3
        spiked = 0
        checked = 0
        now_m = time.monotonic()

        def one(sym: str) -> tuple[str, list[tuple[int, float]] | None]:
            return sym, self._fetch_klines(sym, interval, limit)

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as pool:
            futs = [pool.submit(one, s) for s in self._symbols]
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                sym, rows = fut.result()
                if not rows or len(rows) < 2:
                    continue
                checked += 1
                # 마지막은 진행중 봉 → 제외
                closed = rows[:-1]
                st = self._states.setdefault(sym, self._new_state())
                for ot, vol in closed:
                    if st.last_closed_ot is not None and ot <= st.last_closed_ot:
                        continue
                    # 새 닫힌 봉
                    if alert and len(st.volumes) >= self.cfg.lookback:
                        avg = sum(st.volumes) / len(st.volumes)
                        if avg > 0 and vol >= avg * self.cfg.multiplier:
                            if now_m - st.last_alert_mono >= self.cfg.cooldown_seconds:
                                ratio = vol / avg
                                self._emit(sym, vol, avg, ratio, ot)
                                st.last_alert_mono = now_m
                                spiked += 1
                    st.volumes.append(vol)
                    st.last_closed_ot = ot

        self._log(f"거래량 스캔: checked≈{checked} spike_alerts={spiked}")

    def _emit(self, symbol: str, vol: float, avg: float, ratio: float, open_time: int) -> None:
        ts = datetime.fromtimestamp(open_time, tz=timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"[거래량 급증] Binance Futures {self.cfg.timeframe}\n"
            f"{symbol}\n"
            f"vol={_fmt_vol(vol)}  avg{self.cfg.lookback}={_fmt_vol(avg)}  "
            f"x{ratio:.2f} (≥{self.cfg.multiplier:g})\n"
            f"bar={ts}"
        )
        try:
            self.notify(msg)
        except Exception as exc:
            self._log(f"거래량 알람 전송 실패 {symbol}: {exc}")
        self._log(msg.replace("\n", " | "))

    def _fetch_klines(self, symbol: str, interval: str, limit: int) -> list[tuple[int, float]] | None:
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
                rows = resp.json()
                out: list[tuple[int, float]] = []
                for r in rows:
                    # [0]=open time ms, [7]=quote asset volume
                    ot = int(r[0]) // 1000
                    qvol = float(r[7])
                    out.append((ot, qvol))
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
