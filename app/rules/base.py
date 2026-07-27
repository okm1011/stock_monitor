from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import Candle, WatchTarget


@dataclass
class IndicatorContext:
    """한 심볼의 완성 봉 + 지표 시계열. 인덱스는 candles와 동일."""

    target: WatchTarget
    timeframe: str
    candles: list[Candle]
    closes: list[float]
    highs: list[float]
    lows: list[float]
    rsi: list[float | None]
    macd: list[float | None]
    macd_signal: list[float | None]
    macd_hist: list[float | None]
    bb_mid: list[float | None]
    bb_upper: list[float | None]
    bb_lower: list[float | None]
    atr: list[float | None]

    @property
    def i(self) -> int:
        """마지막 완성 봉 인덱스."""
        return len(self.candles) - 1

    def last_close(self) -> float:
        return self.closes[self.i]

    def last_atr(self) -> float | None:
        return self.atr[self.i] if self.i >= 0 else None


@dataclass
class AlertSignal:
    """규칙이 발생시킨 신호. 엔진이 cooldown/ATR/메시지를 붙인다."""

    rule_id: str
    side: str  # long | short | watch_long | watch_short
    title: str
    detail: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


class AlertRule:
    """새 알람 조건은 이 클래스를 상속해 registry에 등록하면 됨."""

    id: str = "base"
    name: str = "base"

    def enabled(self, config) -> bool:
        return True

    def evaluate(self, ctx: IndicatorContext, config) -> list[AlertSignal]:
        raise NotImplementedError
