from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.config import RsiConfig
from app.models import WatchTarget
from app.settings import Settings, get_settings


class Notifier:
    def send(self, message: str) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def send(self, message: str) -> None:
        print(message, flush=True)


class TelegramNotifier(Notifier):
    """Telegram Bot API sendMessage."""

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
    """여러 Notifier에 동시에 전달. 개별 실패는 로그만 남기고 계속."""

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
    """콘솔 항상 사용. 텔레그램 env가 있으면 함께 사용."""
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
    rsi: float
    price: float
    side: str  # "oversold" | "overbought"
    timeframe: str

    def message(self) -> str:
        label = "RSI 이하(과매도)" if self.side == "oversold" else "RSI 이상(과매수)"
        return (
            f"[ALERT] {self.target.name} ({self.target.symbol})\n"
            f"{label}\n"
            f"RSI={self.rsi:.2f} (period 확인: config rsi.period)\n"
            f"price={self.price}\n"
            f"tf={self.timeframe}"
        )


@dataclass
class RsiAlertEngine:
    rsi_cfg: RsiConfig
    cooldown_seconds: int = 300
    _last_sent: dict[str, float] = field(default_factory=dict)

    def evaluate(
        self,
        target: WatchTarget,
        rsi: float | None,
        price: float,
        timeframe: str,
    ) -> AlertEvent | None:
        if rsi is None:
            return None

        side: str | None = None
        if rsi <= self.rsi_cfg.min:
            side = "oversold"
        elif rsi >= self.rsi_cfg.max:
            side = "overbought"
        if side is None:
            return None

        key = f"{target.key}:{side}"
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        if now - last < self.cooldown_seconds:
            return None

        self._last_sent[key] = now
        return AlertEvent(
            target=target,
            rsi=rsi,
            price=price,
            side=side,
            timeframe=timeframe,
        )
