from __future__ import annotations

from app.models import Candle
from app.timeframes import floor_open_time


class CandleAggregator:
    """실시간 가격 tick을 timeframe 봉으로 집계."""

    def __init__(self, symbol_key: str, timeframe: str) -> None:
        self.symbol_key = symbol_key
        self.timeframe = timeframe
        self.current: Candle | None = None

    def load(self, candle: Candle | None) -> None:
        self.current = candle

    def update(self, price: float, ts_epoch: float, volume: float = 0.0) -> list[Candle]:
        """
        tick 반영.
        반환: 저장해야 할 캔들 목록 (닫힌 봉 + 현재 진행 봉).
        """
        open_time = floor_open_time(ts_epoch, self.timeframe)
        out: list[Candle] = []

        if self.current is None:
            self.current = Candle(
                symbol_key=self.symbol_key,
                timeframe=self.timeframe,
                open_time=open_time,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                closed=False,
            )
            out.append(self.current)
            return out

        if open_time > self.current.open_time:
            # 이전 봉 마감
            closed = self.current.model_copy(update={"closed": True})
            out.append(closed)
            self.current = Candle(
                symbol_key=self.symbol_key,
                timeframe=self.timeframe,
                open_time=open_time,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
                closed=False,
            )
            out.append(self.current)
            return out

        # 같은 봉 갱신
        c = self.current
        self.current = c.model_copy(
            update={
                "high": max(c.high, price),
                "low": min(c.low, price),
                "close": price,
                "volume": c.volume + volume,
                "closed": False,
            }
        )
        out.append(self.current)
        return out
