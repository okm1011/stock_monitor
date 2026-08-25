from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread, local

import httpx

from app.config import AbsorptionBarRuleConfig
from app.timeframes import BINANCE_INTERVAL


LogFn = Callable[[str], None]
NotifyFn = Callable[[str], None]
_thread_http = local()


@dataclass
class _Bar:
    ot: int
    open: float
    high: float
    low: float
    close: float
    qvol: float


@dataclass
class _SymState:
    last_alert_ot: int | None = None
    last_alert_mono: float = 0.0


class FuturesAbsorptionBarWatcher:
    """
    알트코인 신호 2 — 매집봉:
    윗꼬리 김 + 거래량 급증 + 종가는 전봉 대비 거의 안 움직임.
    """

    BASE = "https://fapi.binance.com"

    def __init__(
        self,
        cfg: AbsorptionBarRuleConfig,
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

    def apply_config(self, cfg: AbsorptionBarRuleConfig) -> None:
        self.cfg = cfg
        self._exclude = {b.upper() for b in cfg.exclude_bases}

    def start(self) -> None:
        if not self.cfg.enabled:
            self._log("알트 신호2 매집봉: OFF")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._skip_first_alerts = True
        self._thread = Thread(target=self._run, name="absorb-alert", daemon=True)
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
            self._log(f"알트 신호2 매집봉: 미지원 timeframe={self.cfg.timeframe}")
            return
        self._log(
            f"알트 신호2 매집봉 감시 futures {self.cfg.timeframe} (형성중봉) | "
            f"윗꼬리≥{self.cfg.wick_ratio:g} 몸통≤{self.cfg.max_body_ratio:g} "
            f"종가변화≤{self.cfg.max_close_pct:g}% vol×{self.cfg.volume_mult:g} | "
            f"poll={self.cfg.poll_seconds:g}s cooldown={self.cfg.cooldown_seconds}s"
        )
        try:
            self._ensure_symbols(force=True)
        except Exception as exc:
            self._log(f"알트 신호2 초기화 실패: {exc}")

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.cfg.enabled:
                    self._ensure_symbols(force=False)
                    self._scan(interval)
            except Exception as exc:
                self._log(f"알트 신호2 스캔 오류: {exc}")
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
            f"알트 신호2 대상: {len(symbols)}개 "
            f"(메이저/스테이블 제외 {skipped}개)"
        )

    def _scan(self, interval: str) -> None:
        now_m = time.monotonic()
        checked = 0
        hits = 0
        alerted = 0
        skip_notify = self._skip_first_alerts
        need = self.cfg.volume_lookback + 3

        def one(sym: str) -> tuple[str, list[_Bar] | None]:
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
                if not rows or len(rows) < self.cfg.volume_lookback + 2:
                    continue
                checked += 1
                forming = rows[-1]
                closed = rows[:-1]
                ok, meta = self._passes(closed, forming)
                if not ok or not meta:
                    continue
                hits += 1
                st = self._states.setdefault(sym, _SymState())
                if skip_notify:
                    continue
                if st.last_alert_ot == forming.ot:
                    continue
                if now_m - st.last_alert_mono < self.cfg.cooldown_seconds:
                    continue
                self._emit(sym, meta)
                st.last_alert_ot = forming.ot
                st.last_alert_mono = now_m
                alerted += 1

        if skip_notify:
            self._skip_first_alerts = False
            self._log(
                f"알트 신호2 기동 스캔: checked≈{checked} 후보={hits} "
                f"(첫 스캔 알람 생략)"
            )
            return
        self._log(f"알트 신호2 스캔: checked≈{checked} 후보={hits} alerts={alerted}")

    def _passes(
        self, closed: list[_Bar], bar: _Bar
    ) -> tuple[bool, dict | None]:
        cfg = self.cfg
        if len(closed) < cfg.volume_lookback:
            return False, None
        prev = closed[-1]
        if prev.close <= 0:
            return False, None
        rng = bar.high - bar.low
        if rng <= 0:
            return False, None
        range_pct = rng / prev.close * 100.0
        if range_pct < cfg.min_range_pct:
            return False, None

        upper = bar.high - max(bar.open, bar.close)
        body = abs(bar.close - bar.open)
        wick_r = upper / rng
        body_r = body / rng
        if wick_r < cfg.wick_ratio:
            return False, None
        if body_r > cfg.max_body_ratio:
            return False, None

        close_pct = abs(bar.close - prev.close) / prev.close * 100.0
        if close_pct > cfg.max_close_pct:
            return False, None

        prior = closed[-cfg.volume_lookback :]
        avg_vol = sum(b.qvol for b in prior) / len(prior)
        if avg_vol <= 0 or bar.qvol < avg_vol * cfg.volume_mult:
            return False, None

        return True, {
            "wick_r": wick_r,
            "body_r": body_r,
            "close_pct": close_pct,
            "range_pct": range_pct,
            "vol_x": bar.qvol / avg_vol,
            "vol": bar.qvol,
            "avg_vol": avg_vol,
            "close": bar.close,
            "high": bar.high,
            "ot": bar.ot,
        }

    def _emit(self, symbol: str, meta: dict) -> None:
        ts = datetime.fromtimestamp(meta["ot"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        msg = (
            f"[알트코인 신호 2] 매집봉  {self.cfg.timeframe}\n"
            f"{symbol}\n"
            f"관망/관심 (즉시 진입 금지)\n"
            f"윗꼬리={meta['wick_r']*100:.0f}% of range  (≥{self.cfg.wick_ratio*100:.0f}%)\n"
            f"몸통={meta['body_r']*100:.0f}%  "
            f"종가변화={meta['close_pct']:+.2f}%  (≤{self.cfg.max_close_pct:g}%)\n"
            f"vol=x{meta['vol_x']:.1f}  ({_fmt_vol(meta['vol'])} / avg {_fmt_vol(meta['avg_vol'])})\n"
            f"high={_fmt_price(meta['high'])}  close={_fmt_price(meta['close'])}  bar={ts}"
        )
        try:
            self.notify(msg)
        except Exception as exc:
            self._log(f"알트 신호2 전송 실패 {symbol}: {exc}")
        self._log(msg.replace("\n", " | "))

    def _worker_http(self) -> httpx.Client:
        client = getattr(_thread_http, "client", None)
        if client is None:
            client = httpx.Client(timeout=30.0)
            _thread_http.client = client
        return client

    def _fetch_klines(self, symbol: str, interval: str, limit: int) -> list[_Bar] | None:
        http = self._worker_http()
        for attempt in range(3):
            try:
                resp = http.get(
                    f"{self.BASE}/fapi/v1/klines",
                    params={"symbol": symbol, "interval": interval, "limit": min(limit, 100)},
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
