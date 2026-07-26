from __future__ import annotations

import signal
import time
from collections.abc import Callable
from datetime import datetime, timezone

from rich.console import Console

from app.aggregator import CandleAggregator
from app.alerts import Notifier, RsiAlertEngine, build_notifier
from app.config import AppConfig, resolve_db_path
from app.fetchers.market import LivePriceFetcher, OhlcvBackfiller, build_watch_targets
from app.indicators import calc_rsi
from app.models import AssetClass, WatchTarget
from app.settings import get_settings
from app.storage import CandleStore


console = Console(force_terminal=True, soft_wrap=True)
LogFn = Callable[[str], None]
StatusFn = Callable[[dict[str, float], dict[str, float | None]], None]


class Monitor:
    """상시 실행 모니터: 가격 폴링 -> 봉 집계/저장 -> RSI -> 알림."""

    def __init__(
        self,
        config: AppConfig,
        notifier: Notifier | None = None,
        on_log: LogFn | None = None,
        on_status: StatusFn | None = None,
        register_signals: bool = True,
    ) -> None:
        self.config = config
        self.settings = get_settings()
        self.notifier = notifier or build_notifier(self.settings)
        self.on_log = on_log
        self.on_status = on_status
        self.register_signals = register_signals
        self.targets = build_watch_targets(config)
        self.store = CandleStore(resolve_db_path(config), max_candles=config.history.max_candles)
        self.alert_engine = RsiAlertEngine(
            rsi_cfg=config.rsi,
            cooldown_seconds=config.alert_cooldown_seconds,
        )
        self.price_fetcher = LivePriceFetcher(stock_min_interval=max(5.0, config.poll_interval_seconds))
        self.backfiller = OhlcvBackfiller()
        self.aggregators: dict[str, CandleAggregator] = {
            t.key: CandleAggregator(t.key, config.timeframe) for t in self.targets
        }
        self._stop = False
        self._last_status = 0.0
        self._last_crypto_sync = 0.0
        self._crypto_rr = 0
        self._latest_rsi: dict[str, float | None] = {}
        self._latest_price: dict[str, float] = {}
        self._need = max(config.history.max_candles, config.rsi.period + 50)
        self._crypto_targets = [t for t in self.targets if t.asset_class == AssetClass.CRYPTO]

    def _log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)
        else:
            console.print(message)

    def request_stop(self, *_args) -> None:
        self._stop = True
        self._log("종료 요청...")

    def start(self) -> None:
        if self.register_signals:
            signal.signal(signal.SIGINT, self.request_stop)
            try:
                signal.signal(signal.SIGTERM, self.request_stop)
            except Exception:
                pass

        mode = "closed_only" if self.config.rsi.closed_only else "live(진행봉포함)"
        self._log(
            f"모니터 시작 poll={self.config.poll_interval_seconds}s "
            f"tf={self.config.timeframe} RSI({self.config.rsi.period}) "
            f"min={self.config.rsi.min} max={self.config.rsi.max} mode={mode}"
        )
        self._log(f"심볼 {len(self.targets)}개 | DB={resolve_db_path(self.config)}")
        if self.settings.telegram_enabled:
            self._log("Telegram 알림: ON")
        else:
            self._log("Telegram 알림: OFF (.env 확인)")

        self._backfill_all()

        while not self._stop:
            loop_started = time.monotonic()
            try:
                self._tick()
            except Exception as exc:
                self._log(f"tick error: {exc}")

            elapsed = time.monotonic() - loop_started
            sleep_for = self.config.poll_interval_seconds - elapsed
            if sleep_for > 0:
                end = time.monotonic() + sleep_for
                while not self._stop and time.monotonic() < end:
                    time.sleep(min(0.2, end - time.monotonic()))

        self._shutdown()

    def _closes_for_rsi(self, symbol_key: str) -> list[float]:
        candles = self.store.get_candles(symbol_key, self.config.timeframe)
        if self.config.rsi.closed_only:
            return [c.close for c in candles if c.closed]
        return [c.close for c in candles]

    def _apply_rsi_and_alert(self, t: WatchTarget, price: float) -> None:
        closes = self._closes_for_rsi(t.key)
        rsi = calc_rsi(closes, self.config.rsi.period)
        self._latest_rsi[t.key] = rsi
        self._latest_price[t.key] = price
        event = self.alert_engine.evaluate(t, rsi, price, self.config.timeframe)
        if event is not None:
            self.notifier.send(event.message())
            self._log(event.message().replace("\n", " | "))

    def _backfill_all(self) -> None:
        for t in self.targets:
            if self._stop:
                return
            try:
                candles = self.backfiller.backfill(t, self.config.timeframe, self._need)
                if candles:
                    self.store.upsert_many(candles)
                    self.aggregators[t.key].load(candles[-1])
                    self._apply_rsi_and_alert(t, candles[-1].close)
                    self._log(
                        f"backfill OK {t.name}: {len(candles)} candles, "
                        f"RSI={_fmt_rsi(self._latest_rsi[t.key])}"
                    )
                else:
                    self._log(f"backfill empty {t.name}")
            except Exception as exc:
                self._log(f"backfill fail {t.name}: {exc}")
        self._emit_status(force=True)

    def _resync_crypto(self) -> None:
        """
        코인 봉을 Binance에서 부분 갱신.
        전체를 매번 받지 않고, 라운드로빈으로 일부만 최신 봉(소수) 갱신해 부하를 줄임.
        """
        if not self._crypto_targets:
            return

        # 5초 폴링 기준: 한 번에 최대 8개, 최근 봉만
        batch = 8
        sync_limit = max(self.config.rsi.period + 10, 40)
        n = len(self._crypto_targets)
        for i in range(batch):
            t = self._crypto_targets[(self._crypto_rr + i) % n]
            try:
                candles = self.backfiller.backfill(t, self.config.timeframe, sync_limit)
                if not candles:
                    continue
                self.store.upsert_many(candles)
                self.aggregators[t.key].load(candles[-1])
                self._apply_rsi_and_alert(t, candles[-1].close)
            except Exception as exc:
                self._log(f"crypto sync fail {t.name}: {exc}")
        self._crypto_rr = (self._crypto_rr + batch) % n

    def _tick(self) -> None:
        # 코인: 거래소 봉 재동기화 (RSI가 바이낸스와 어긋나지 않게)
        now_m = time.monotonic()
        sync_every = max(5.0, float(self.config.poll_interval_seconds))
        if now_m - self._last_crypto_sync >= sync_every:
            self._resync_crypto()
            self._last_crypto_sync = now_m

        prices = self.price_fetcher.fetch_prices(self.targets)
        now = datetime.now(timezone.utc).timestamp()

        for t in self.targets:
            # 코인은 위에서 kline 기준으로 이미 RSI 갱신
            if t.asset_class == AssetClass.CRYPTO:
                if t.key in prices:
                    self._latest_price[t.key] = prices[t.key]
                continue

            price = prices.get(t.key)
            if price is None:
                continue
            updated = self.aggregators[t.key].update(price, now)
            self.store.upsert_many(updated)
            self._apply_rsi_and_alert(t, price)

        self._emit_status()

    def _emit_status(self, force: bool = False) -> None:
        interval = self.config.status_log_seconds
        now = time.monotonic()
        if not force and interval > 0 and (now - self._last_status) < interval:
            return
        self._last_status = now

        if self.on_status is not None:
            self.on_status(dict(self._latest_price), dict(self._latest_rsi))

        ts = datetime.now().strftime("%H:%M:%S")
        parts = []
        for t in self.targets:
            p = self._latest_price.get(t.key)
            r = self._latest_rsi.get(t.key)
            if p is None:
                continue
            parts.append(f"{t.symbol}={_fmt_price(p)} RSI={_fmt_rsi(r)}")
        if parts:
            self._log(f"{ts} " + " | ".join(parts))

    def _shutdown(self) -> None:
        self.price_fetcher.close()
        self.backfiller.close()
        self.store.close()
        close = getattr(self.notifier, "close", None)
        if callable(close):
            close()
        self._log("모니터 종료")


def _fmt_rsi(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "n/a"


def _fmt_price(price: float) -> str:
    if abs(price) >= 1000:
        return f"{price:,.2f}"
    if abs(price) >= 1:
        return f"{price:,.4f}"
    return f"{price:.6f}"
