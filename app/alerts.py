from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import AppConfig, AtrConfig
from app.context import build_context
from app.models import Candle, WatchTarget
from app.rules import build_rules
from app.rules.base import AlertRule, AlertSignal, IndicatorContext
from app.settings import Settings, get_settings


class Notifier:
    def send(self, message: str) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def send(self, message: str) -> None:
        print(message, flush=True)


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 15.0) -> None:
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self._url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self._http = httpx.Client(timeout=timeout)

    def send(self, message: str) -> None:
        resp = self._http.post(
            self._url,
            json={
                "chat_id": self.chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
        )
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise RuntimeError(f"Telegram send failed ({resp.status_code}): {detail}")

    def close(self) -> None:
        self._http.close()


class MultiNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, message: str) -> None:
        errors: list[str] = []
        for n in self.notifiers:
            try:
                n.send(message)
            except Exception as exc:
                errors.append(f"{type(n).__name__}: {exc}")
        if errors:
            print("[notify error] " + " | ".join(errors), flush=True)

    def close(self) -> None:
        for n in self.notifiers:
            close = getattr(n, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def build_notifier(settings: Settings | None = None) -> Notifier:
    settings = settings or get_settings()
    notifiers: list[Notifier] = [ConsoleNotifier()]
    if settings.telegram_enabled:
        notifiers.append(
            TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        )
    return MultiNotifier(notifiers) if len(notifiers) > 1 else notifiers[0]


@dataclass
class AlertEvent:
    target: WatchTarget
    signal: AlertSignal
    timeframe: str
    price: float
    atr: float | None
    sl: float | None
    tp: float | None
    extras: dict[str, Any] = field(default_factory=dict)

    def message(self) -> str:
        lines = [
            self.signal.title,
            f"{self.target.name} ({self.target.symbol})",
        ]
        if self.signal.detail:
            lines.append(self.signal.detail)
        lines.append(f"price={_fmt_price(self.price)}  tf={self.timeframe}")
        if self.atr is not None and self.sl is not None and self.tp is not None:
            sl_diff = self.sl - self.price
            tp_diff = self.tp - self.price
            lines.append("----------")
            lines.append(f"ATR 기반 TP/SL 가이드")
            lines.append(
                f"- 손절가 (SL): {_fmt_price(self.sl)} ({_fmt_signed(sl_diff)})"
            )
            lines.append(
                f"- 익절가 (TP): {_fmt_price(self.tp)} ({_fmt_signed(tp_diff)})"
            )
        return "\n".join(lines)


def compute_tp_sl(
    price: float,
    atr: float | None,
    side: str,
    atr_cfg: AtrConfig,
) -> tuple[float | None, float | None]:
    if atr is None or atr <= 0:
        return None, None
    sl_dist = atr_cfg.sl_mult * atr
    tp_dist = atr_cfg.tp_mult * atr
    if side in ("long", "watch_long"):
        return price - sl_dist, price + tp_dist
    if side in ("short", "watch_short"):
        return price + sl_dist, price - tp_dist
    return None, None


@dataclass
class RuleEngine:
    """등록된 AlertRule들을 실행. 새 규칙은 rules/ 에 추가 후 registry만 수정."""

    config: AppConfig
    rules: list[AlertRule] = field(default_factory=build_rules)
    cooldown_seconds: int = 300
    _last_sent: dict[str, float] = field(default_factory=dict)
    _last_closed_ot: dict[str, int] = field(default_factory=dict)

    def evaluate_candles(
        self,
        target: WatchTarget,
        candles: list[Candle],
        *,
        allow_alert: bool = True,
    ) -> tuple[IndicatorContext | None, list[AlertEvent]]:
        closed = [c for c in candles if c.closed] if self.config.signal_on_closed_bar else candles
        if not closed:
            closed = candles[:-1] if len(candles) > 1 else []
        ctx = build_context(target, self.config.timeframe, closed, self.config)
        if ctx is None:
            return None, []

        closed_ot = closed[-1].open_time
        prev_ot = self._last_closed_ot.get(target.key)
        is_new_bar = prev_ot is None or closed_ot != prev_ot
        self._last_closed_ot[target.key] = closed_ot

        events: list[AlertEvent] = []
        # 시작 백필 중에는 상태만 갱신하고 알람은 생략(폭주 방지)
        if not allow_alert or not is_new_bar:
            return ctx, events
        # 첫 관측(백필 직후 첫 틱)도 알람 생략: prev_ot is None 이었던 경우
        # allow_alert=True 이고 prev was None -> skip once by treating as arming
        if prev_ot is None:
            return ctx, events

        price = ctx.last_close()
        atr = ctx.last_atr()
        for rule in self.rules:
            if not rule.enabled(self.config):
                continue
            try:
                signals = rule.evaluate(ctx, self.config)
            except Exception:
                continue
            for sig in signals:
                key = f"{target.key}:{sig.rule_id}:{sig.side}"
                now = time.monotonic()
                if now - self._last_sent.get(key, 0.0) < self.cooldown_seconds:
                    continue
                self._last_sent[key] = now
                sl, tp = compute_tp_sl(price, atr, sig.side, self.config.atr)
                events.append(
                    AlertEvent(
                        target=target,
                        signal=sig,
                        timeframe=self.config.timeframe,
                        price=price,
                        atr=atr,
                        sl=sl,
                        tp=tp,
                        extras=sig.extras,
                    )
                )
        return ctx, events


def _fmt_price(price: float) -> str:
    if abs(price) >= 1000:
        return f"{price:,.2f}"
    if abs(price) >= 1:
        return f"{price:,.4f}"
    return f"{price:.6f}"


def _fmt_signed(v: float) -> str:
    return f"{v:+,.2f}" if abs(v) >= 1 else f"{v:+.6f}"
