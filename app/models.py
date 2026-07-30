from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    KR_STOCK = "kr_stock"
    US_STOCK = "us_stock"


class DataFeed(str, Enum):
    BINANCE_SPOT = "binance_spot"
    BINANCE_FUTURES = "binance_futures"
    YAHOO = "yahoo"


class Quote(BaseModel):
    """공통 시세 스냅샷. 자산 종류와 무관하게 동일 스키마로 취급."""

    asset_class: AssetClass
    symbol: str
    name: str
    price: float
    currency: str
    change_pct: Optional[float] = None
    volume: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    market_cap: Optional[float] = None
    source: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: Optional[dict] = None

    def summary_line(self) -> str:
        change = f"{self.change_pct:+.2f}%" if self.change_pct is not None else "n/a"
        return (
            f"[{self.asset_class.value}] {self.name} ({self.symbol}) "
            f"{self.price:,.4g} {self.currency}  d {change}  @{self.source}"
        )


class Candle(BaseModel):
    """OHLCV 캔들. open_time은 봉 시작 시각(UTC, epoch seconds)."""

    symbol_key: str
    timeframe: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    closed: bool = False


class WatchTarget(BaseModel):
    """모니터가 추적하는 단일 심볼."""

    key: str
    asset_class: AssetClass
    symbol: str
    name: str
    currency: str
    # API용 원본 티커 (예: 005930.KS, BTCUSDT, SKHYUSDT)
    market_id: str
    feed: DataFeed = DataFeed.BINANCE_SPOT

    def uses_binance_klines(self) -> bool:
        return self.feed in (DataFeed.BINANCE_SPOT, DataFeed.BINANCE_FUTURES)
