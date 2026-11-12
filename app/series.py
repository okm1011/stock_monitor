from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class Asset:
    key: str  # bn:BTCUSDT / yf:NQ=F
    symbol: str  # BTCUSDT / NQ=F
    name: str
    source: str  # "binance" | "yahoo"
    tag: str = ""  # 바이낸스 주식/원자재 토큰 구분

    @property
    def source_label(self) -> str:
        return "Binance 선물" if self.source == "binance" else "Yahoo"

    @property
    def display(self) -> str:
        text = f"{self.name} ({self.symbol})" if self.name and self.name != self.symbol else self.symbol
        return f"{text} [{self.tag}]" if self.tag else text


@dataclass(frozen=True)
class Bar:
    open_time: int  # 봉 시작 epoch seconds (UTC)
    close: float
    volume: float


class BarSeries:
    """open_time 기준으로 병합되는 봉 목록. 마지막 봉은 형성 중일 수 있음."""

    def __init__(self, max_bars: int = 1000) -> None:
        self.max_bars = max_bars
        self._bars: dict[int, Bar] = {}
        self._sorted: list[Bar] | None = []
        self._lock = Lock()

    def __len__(self) -> int:
        return len(self._bars)

    def replace(self, bars: list[Bar]) -> None:
        with self._lock:
            self._bars = {b.open_time: b for b in bars}
            self._sorted = None
            self._trim()

    def merge(self, bars: list[Bar]) -> None:
        if not bars:
            return
        with self._lock:
            for b in bars:
                self._bars[b.open_time] = b
            self._sorted = None
            self._trim()

    def bars(self) -> list[Bar]:
        with self._lock:
            if self._sorted is None:
                self._sorted = [self._bars[k] for k in sorted(self._bars)]
            return list(self._sorted)

    def _trim(self) -> None:
        if len(self._bars) <= self.max_bars:
            return
        keys = sorted(self._bars)
        for k in keys[: len(keys) - self.max_bars]:
            del self._bars[k]
