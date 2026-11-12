from __future__ import annotations

import signal
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

from rich.console import Console

from app.alerts import Notifier, batch_messages, build_notifier
from app.config import AppConfig, default_config_path, load_config
from app.market import MarketData
from app.series import Asset
from app.settings import get_settings
from app.signals import AlertEngine, compute_snapshot, fmt_price
from app.timeframes import TIMEFRAMES


console = Console(force_terminal=True, soft_wrap=True)
LogFn = Callable[[str], None]
StatusFn = Callable[[list[str]], None]

# config.yaml mtime 검사 주기 (저장 후 대략 이 시간 안에 반영)
CONFIG_RELOAD_SECONDS = 5.0
UNIVERSE_RETRY_SECONDS = 300.0


class Monitor:
    """바이낸스 선물 상위 N + 야후 큰 자산 → RSI / 이평 근접 알람 → 텔레그램."""

    def __init__(
        self,
        config: AppConfig,
        notifier: Notifier | None = None,
        on_log: LogFn | None = None,
        on_status: StatusFn | None = None,
        register_signals: bool = True,
        config_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self._config_path = Path(config_path) if config_path else default_config_path()
        self._config_mtime = _file_mtime(self._config_path)
        self._last_config_check = 0.0
        self.settings = get_settings()
        self.notifier = notifier or build_notifier(self.settings)
        self.on_log = on_log
        self.on_status = on_status
        self.register_signals = register_signals
        self.market = MarketData(log=self._log)
        self.engine = AlertEngine()
        self._stop = Event()
        self._universe_due = 0.0
        self._next_binance = 0.0
        self._next_macro = 0.0
        self._next_status = 0.0
        self._alerts_total = 0
        self._binance_stat = "-"
        self._macro_stat = "-"

    def _log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)
        else:
            console.print(message, markup=False, highlight=False)

    def request_stop(self, *_args) -> None:
        self._stop.set()
        self._log("종료 요청...")

    def _stopping(self) -> bool:
        return self._stop.is_set()

    # ---------- 실행 ----------

    def start(self) -> None:
        if self.register_signals:
            signal.signal(signal.SIGINT, self.request_stop)
            try:
                signal.signal(signal.SIGTERM, self.request_stop)
            except Exception:
                pass

        self._log_settings("모니터 시작")
        self._log("Telegram 알림: ON" if self.notifier.enabled else "Telegram 알림: OFF (.env 확인)")

        self._refresh_universe()
        self._sync_macro_assets()
        now = time.monotonic()
        self._next_binance = now + self.config.poll_seconds
        self._next_macro = now + self.config.macro.poll_seconds
        self._next_status = now
        self._log("기준값 기록 완료 - 이후 새로 진입하는 신호부터 알람")

        while not self._stopping():
            try:
                self._maybe_reload_config()
                now = time.monotonic()
                if now >= self._universe_due:
                    self._refresh_universe()
                if self.market.binance_assets() and time.monotonic() >= self._next_binance:
                    self._next_binance = time.monotonic() + self.config.poll_seconds
                    self._binance_cycle()
                if self.market.macro_assets() and time.monotonic() >= self._next_macro:
                    self._next_macro = time.monotonic() + self.config.macro.poll_seconds
                    self._macro_cycle()
                self._emit_status()
            except Exception as exc:
                self._log(f"루프 오류: {exc}")
            self._stop.wait(1.0)

        self._shutdown()

    def _binance_cycle(self) -> None:
        started = time.monotonic()
        ok, total = self.market.refresh_binance(self._stopping)
        sent = self._evaluate(self.market.binance_assets())
        self._binance_stat = (
            f"{len(self.market.binance_assets())}종목 갱신 {ok}/{total} "
            f"{time.monotonic() - started:.1f}s 알람 {sent}"
        )
        if ok < total * 0.8:
            self._log(f"바이낸스 갱신 일부 실패: {ok}/{total}")

    def _macro_cycle(self) -> None:
        started = time.monotonic()
        ok, total = self.market.refresh_macro()
        sent = self._evaluate(self.market.macro_assets())
        self._macro_stat = (
            f"{len(self.market.macro_assets())}종목 갱신 {ok}/{total} "
            f"{time.monotonic() - started:.1f}s 알람 {sent}"
        )
        if ok < total * 0.5:
            self._log(f"야후 갱신 일부 실패: {ok}/{total}")

    def _evaluate(self, assets: list[Asset]) -> int:
        messages: list[str] = []
        for a in assets:
            for tf in TIMEFRAMES:
                bars = self.market.bars(a, tf)
                if not bars:
                    continue
                try:
                    messages.extend(self.engine.evaluate(a, tf, bars, self.config))
                except Exception as exc:
                    self._log(f"평가 오류 {a.symbol} {tf}: {exc}")
        for m in messages:
            self._log("알람 | " + m.replace("\n", " | "))
        for chunk in batch_messages(messages):
            try:
                self.notifier.send(chunk)
            except Exception as exc:
                self._log(f"텔레그램 전송 실패: {exc}")
        self._alerts_total += len(messages)
        return len(messages)

    # ---------- 유니버스 / 야후 목록 ----------

    def _refresh_universe(self) -> None:
        u = self.config.universe
        if not u.enabled:
            self._apply_universe([])
            self._universe_due = float("inf")
            return
        try:
            symbols = self.market.binance.top_symbols(u.top_n, u.include_tradfi, u.exclude_bases)
        except Exception as exc:
            self._log(f"바이낸스 상위 종목 조회 실패 ({UNIVERSE_RETRY_SECONDS:.0f}초 후 재시도): {exc}")
            self._universe_due = time.monotonic() + UNIVERSE_RETRY_SECONDS
            return
        self._apply_universe(symbols)
        self._universe_due = time.monotonic() + u.refresh_hours * 3600.0

    def _apply_universe(self, symbols) -> None:
        added, removed = self.market.set_binance_universe(symbols)
        for a in removed:
            self.engine.forget(a.key)
        if added or removed:
            self._log(
                f"바이낸스 대상 {len(self.market.binance_assets())}종목 "
                f"(+{len(added)} / -{len(removed)})"
            )
        if added:
            self.market.bootstrap_binance(added, self._stopping)
            self._evaluate(added)

    def _sync_macro_assets(self) -> None:
        m = self.config.macro
        added, removed = self.market.set_macro_assets(m.assets if m.enabled else [])
        for a in removed:
            self.engine.forget(a.key)
        if added or removed:
            self._log(
                f"야후 대상 {len(self.market.macro_assets())}종목 "
                f"(+{len(added)} / -{len(removed)})"
            )
        if added:
            self.market.bootstrap_macro(added)
            self._evaluate(added)

    # ---------- 설정 자동 반영 ----------

    def _maybe_reload_config(self) -> None:
        now = time.monotonic()
        if now - self._last_config_check < CONFIG_RELOAD_SECONDS:
            return
        self._last_config_check = now
        mtime = _file_mtime(self._config_path)
        if mtime is None or mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            new_cfg = load_config(self._config_path)
        except Exception as exc:
            self._log(f"config reload 실패(기존 유지): {exc}")
            return
        old = self.config
        self.config = new_cfg
        self._log_settings("config 반영")
        if old.universe.model_dump() != new_cfg.universe.model_dump():
            self._universe_due = 0.0
        if old.macro.model_dump() != new_cfg.macro.model_dump():
            self._sync_macro_assets()
        now = time.monotonic()
        self._next_binance = min(self._next_binance, now + new_cfg.poll_seconds)
        self._next_macro = min(self._next_macro, now + new_cfg.macro.poll_seconds)

    def _log_settings(self, prefix: str) -> None:
        c = self.config
        u = c.universe
        rsi_parts = []
        for tf in TIMEFRAMES:
            t = c.rsi_alert.tf(tf)
            if not t.enabled:
                continue
            ob = f"{t.overbought:g}" if t.overbought_enabled else "-"
            os_ = f"{t.oversold:g}" if t.oversold_enabled else "-"
            rsi_parts.append(f"{tf} {ob}/{os_}")
        ma_parts = []
        for key in ("ema50", "ema200", "vwma100"):
            line = c.ma_alert.line(key)
            if not line.enabled:
                continue
            tfs = [f"{tf}±{line.tf(tf).proximity_pct:g}%" for tf in TIMEFRAMES if line.tf(tf).enabled]
            if tfs:
                ma_parts.append(f"{key}[{' '.join(tfs)}]")
        self._log(
            f"{prefix}: 바이낸스 {'상위 ' + str(u.top_n) if u.enabled else 'OFF'}"
            f"{' (주식/원자재 포함)' if u.enabled and u.include_tradfi else ''} 갱신 {c.poll_seconds:g}s | "
            f"야후 {len(c.macro.assets) if c.macro.enabled else 'OFF'} 갱신 {c.macro.poll_seconds:g}s"
        )
        self._log(f"  RSI({c.rsi_alert.period}) {'ON' if c.rsi_alert.enabled else 'OFF'}: {', '.join(rsi_parts) or '-'}")
        self._log(f"  이평 근접 {'ON' if c.ma_alert.enabled else 'OFF'}: {' '.join(ma_parts) or '-'}")

    # ---------- 상태 ----------

    def _emit_status(self) -> None:
        interval = self.config.status_log_seconds
        now = time.monotonic()
        if now < self._next_status:
            return
        self._next_status = now + (interval if interval > 0 else 60.0)
        if interval > 0:
            self._log(
                f"상태 | 바이낸스 {self._binance_stat} | 야후 {self._macro_stat} | 누적 알람 {self._alerts_total}"
            )
        if self.on_status is not None:
            self.on_status(self.status_lines())

    def status_lines(self, limit: int = 40) -> list[str]:
        lines = []
        for a in (self.market.macro_assets() + self.market.binance_assets())[:limit]:
            parts = []
            price = None
            for tf in TIMEFRAMES:
                snap = compute_snapshot(self.market.bars(a, tf), self.config.rsi_alert.period)
                if snap is None:
                    parts.append(f"{tf} -")
                    continue
                price = snap.price
                parts.append(f"{tf} {snap.rsi:.0f}" if snap.rsi is not None else f"{tf} -")
            px = fmt_price(price) if price is not None else "-"
            lines.append(f"{a.symbol:<14} {px:>14}  RSI " + " ".join(parts))
        return lines

    def _shutdown(self) -> None:
        self.market.close()
        try:
            self.notifier.close()
        except Exception:
            pass
        self._log("모니터 종료")


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
