from __future__ import annotations

from datetime import datetime, timezone

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# yfinance interval 매핑
YFINANCE_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "3m": "5m",  # yfinance에 3m 없음 -> 5m으로 근사 백필
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "60m",
    "1d": "1d",
}

# Binance interval 매핑
BINANCE_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def timeframe_seconds(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return TIMEFRAME_SECONDS[timeframe]


def floor_open_time(ts: datetime | float | int, timeframe: str) -> int:
    """시각을 봉 시작 epoch(초)로 floor."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        epoch = int(ts.timestamp())
    else:
        epoch = int(ts)
    step = timeframe_seconds(timeframe)
    return epoch - (epoch % step)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
