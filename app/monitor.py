from __future__ import annotations

import signal
import time
from collections.abc import Callable
from datetime import datetime, timezone

from rich.console import Console

from app.aggregator import CandleAggregator
from app.alerts import Notifier, RuleEngine, build_notifier
from app.config import AppConfig, resolve_db_path
from app.fetchers.market import LivePriceFetcher, OhlcvBackfiller
from app.models import AssetClass, WatchTarget
from app.settings import get_settings
from app.storage import CandleStore
from app.universe import BinanceUniverseService, resolve_watch_targets
from app.volume_spike import FuturesVolumeSpikeWatcher


console = Console(force_terminal=True, soft_wrap=True)
LogFn = Callable[[str], None]
StatusFn = Callable[[dict[str, float], dict[str, float | None]], None]


class Monitor:
    """상시 모니터: 유니버스 갱신 -> 봉 수집 -> 규칙 엔진 -> 알림."""

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
        self.store = CandleStore(resolve_db_path(config), max_candles=config.history.max_candles)
        self.engine = RuleEngine(
            config=config,
            cooldown_seconds=config.alert_cooldown_seconds,
        )
        self.price_fetcher = LivePriceFetcher(stock_min_interval=max(5.0, config.poll_interval_seconds))
        self.backfiller = OhlcvBackfiller()
        self.targets: list[WatchTarget] = []
        self.aggregators: dict[str, CandleAggregator] = {}
        self._stop = False
        self._last_status = 0.0
        self._last_crypto_sync = 0.0
        self._crypto_rr = 0
        self._latest_rsi: dict[str, float | None] = {}
        self._latest_price: dict[str, float] = {}
        self._armed = False
        self._need = max(
            config.history.max_candles,
            config.macd.slow + config.macd.signal + 50,
            config.bollinger.period + 50,
            config.atr.period + 50,
        )
        self._crypto_targets: list[WatchTarget] = []
        self._binance_klines_targets: list[WatchTarget] = []
        self._universe_svc = (
            BinanceUniverseService(config.universe) if config.universe.enabled else None
        )
        self._volume_spike: FuturesVolumeSpikeWatcher | None = None

    def _log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)
        else:
            console.print(message)

    def request_stop(self, *_args) -> None:
        self._stop = True
        self._log("종료 요청...")

    def _apply_targets(self, targets: list[WatchTarget], backfill_new_only: bool = False) -> None:
        old_keys = {t.key for t in self.targets}
        self.targets = targets
        self._crypto_targets = [t for t in targets if t.asset_class == AssetClass.CRYPTO]
        self._binance_klines_targets = [t for t in targets if t.uses_binance_klines()]
        for t in targets:
            if t.key not in self.aggregators:
                self.aggregators[t.key] = CandleAggregator(t.key, self.config.timeframe)
        # 제거된 심볼 aggregator는 남겨둬도 무방 (메모리 소량)
        new_targets = [t for t in targets if t.key not in old_keys] if backfill_new_only else targets
        if new_targets:
            self._backfill_targets(new_targets)

    def start(self) -> None:
        if self.register_signals:
            signal.signal(signal.SIGINT, self.request_stop)
            try:
                signal.signal(signal.SIGTERM, self.request_stop)
            except Exception:
                pass

        enabled = [r.id for r in self.engine.rules if r.enabled(self.config)]
        if self.config.rules.volume_spike.enabled:
            enabled.append("volume_spike")
        self._log(
            f"모니터 시작 poll={self.config.poll_interval_seconds}s "
            f"tf={self.config.timeframe} closed={self.config.signal_on_closed_bar}"
        )
        self._log(f"규칙: {', '.join(enabled) if enabled else '(없음)'}")
        self._log(
            f"RSI({self.config.rsi.period}) MACD({self.config.macd.fast},{self.config.macd.slow},{self.config.macd.signal}) "
            f"BB({self.config.bollinger.period},{self.config.bollinger.stddev}) "
            f"ATR SL={self.config.atr.sl_mult} TP={self.config.atr.tp_mult}"
        )
        if self.config.universe.enabled:
            u = self.config.universe
            self._log(
                f"유니버스: Binance {u.quote_asset} / {u.volume_lookback_days}일 거래량 "
                f"상위 {u.top_percentile}% (max {u.max_symbols})"
            )

        targets = resolve_watch_targets(self.config, log=self._log, force_universe=False)
        self._apply_targets(targets, backfill_new_only=False)
        self._log(f"심볼 {len(self.targets)}개 | DB={resolve_db_path(self.config)}")
        self._log("Telegram 알림: ON" if self.settings.telegram_enabled else "Telegram 알림: OFF")

        self._armed = True
        self._log("백필 완료 - 이후 새 봉 마감부터 알람 활성")

        vs = self.config.rules.volume_spike
        if vs.enabled:
            self._volume_spike = FuturesVolumeSpikeWatcher(
                cfg=vs,
                notify=self.notifier.send,
                log=self._log,
            )
            self._volume_spike.start()
        else:
            self._log("펌프 초입 알람: OFF")

        while not self._stop:
            loop_started = time.monotonic()
            try:
                self._maybe_refresh_universe()
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

    def _maybe_refresh_universe(self) -> None:
        if not self._universe_svc or not self._universe_svc.needs_refresh():
            return
        self._log("유니버스 일일 갱신 시작...")
        try:
            new_targets = resolve_watch_targets(self.config, log=self._log, force_universe=True)
            self._apply_targets(new_targets, backfill_new_only=True)
            self._log(f"유니버스 갱신 완료: {len(self.targets)}개")
        except Exception as exc:
            self._log(f"유니버스 갱신 실패(기존 목록 유지): {exc}")

    def _emit_events(self, events) -> None:
        for ev in events:
            self.notifier.send(ev.message())
            self._log(ev.message().replace("\n", " | "))

    def _evaluate_target(self, t: WatchTarget, allow_alert: bool) -> None:
        candles = self.store.get_candles(t.key, self.config.timeframe)
        ctx, events = self.engine.evaluate_candles(t, candles, allow_alert=allow_alert and self._armed)
        if ctx is not None:
            self._latest_price[t.key] = ctx.last_close()
            self._latest_rsi[t.key] = ctx.rsi[ctx.i]
        if events:
            self._emit_events(events)

    def _backfill_targets(self, targets: list[WatchTarget]) -> None:
        for t in targets:
            if self._stop:
                return
            try:
                candles = self.backfiller.backfill(t, self.config.timeframe, self._need)
                if candles:
                    self.store.upsert_many(candles)
                    self.aggregators[t.key].load(candles[-1])
                    self._evaluate_target(t, allow_alert=False)
                    self._log(
                        f"backfill OK {t.name}: {len(candles)} candles, "
                        f"RSI={_fmt_rsi(self._latest_rsi.get(t.key))}"
                    )
                else:
                    self._log(f"backfill empty {t.name}")
            except Exception as exc:
                self._log(f"backfill fail {t.name}: {exc}")
        self._emit_status(force=True)

    def _resync_binance_klines(self) -> None:
        if not self._binance_klines_targets:
            return
        batch = 8
        sync_limit = min(self._need, 120)
        n = len(self._binance_klines_targets)
        for i in range(min(batch, n)):
            t = self._binance_klines_targets[(self._crypto_rr + i) % n]
            try:
                candles = self.backfiller.backfill(t, self.config.timeframe, sync_limit)
                if not candles:
                    continue
                self.store.upsert_many(candles)
                self.aggregators[t.key].load(candles[-1])
                self._evaluate_target(t, allow_alert=True)
            except Exception as exc:
                self._log(f"binance sync fail {t.name}: {exc}")
        self._crypto_rr = (self._crypto_rr + batch) % max(n, 1)

    def _tick(self) -> None:
        now_m = time.monotonic()
        sync_every = max(5.0, float(self.config.poll_interval_seconds))
        if now_m - self._last_crypto_sync >= sync_every:
            self._resync_binance_klines()
            self._last_crypto_sync = now_m

        prices = self.price_fetcher.fetch_prices(self.targets)
        now = datetime.now(timezone.utc).timestamp()

        for t in self.targets:
            price = prices.get(t.key)
            if price is None:
                continue
            # 바이낸스/주식 모두 실시간 가격으로 형성 중 봉 갱신 → 라이브 RSI 돌파 가능
            updated = self.aggregators[t.key].update(price, now)
            self.store.upsert_many(updated)
            self._evaluate_target(t, allow_alert=True)

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
            shown = parts[:12]
            more = f" ...(+{len(parts)-12})" if len(parts) > 12 else ""
            self._log(f"{ts} " + " | ".join(shown) + more)

    def _shutdown(self) -> None:
        if self._volume_spike:
            self._volume_spike.stop()
            self._volume_spike = None
        self.price_fetcher.close()
        self.backfiller.close()
        if self._universe_svc:
            self._universe_svc.close()
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
