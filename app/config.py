from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


Timeframe = Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]


class CryptoSymbol(BaseModel):
    id: str
    symbol: str
    name: str
    binance_symbol: str | None = None

    def market_symbol(self) -> str:
        return self.binance_symbol or f"{self.symbol.upper()}USDT"


class StockSymbol(BaseModel):
    ticker: str
    name: str


class RsiConfig(BaseModel):
    period: int = 14
    min: float = 30.0
    max: float = 70.0
    # True면 완성된 봉만으로 RSI 계산 (확정값). False면 진행 중 봉 포함(차트 실시간과 유사)
    closed_only: bool = False

    @field_validator("period")
    @classmethod
    def _period_ok(cls, v: int) -> int:
        if v < 2:
            raise ValueError("rsi.period must be >= 2")
        return v

    @field_validator("max")
    @classmethod
    def _range_ok(cls, v: float, info) -> float:
        min_v = info.data.get("min")
        if min_v is not None and v <= min_v:
            raise ValueError("rsi.max must be > rsi.min")
        return v


class HistoryConfig(BaseModel):
    max_candles: int = 300
    db_path: str = "data/candles.db"


class AppConfig(BaseModel):
    crypto: list[CryptoSymbol] = Field(default_factory=list)
    kr_stocks: list[StockSymbol] = Field(default_factory=list)
    us_stocks: list[StockSymbol] = Field(default_factory=list)
    poll_interval_seconds: float = 1.0
    timeframe: Timeframe = "1m"
    rsi: RsiConfig = Field(default_factory=RsiConfig)
    alert_cooldown_seconds: int = 300
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    status_log_seconds: float = 10.0

    @field_validator("poll_interval_seconds")
    @classmethod
    def _poll_ok(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        return v


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with config_path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config.yaml"


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    """현재 설정을 YAML로 저장 (감시 심볼 포함)."""
    config_path = Path(path) if path else default_config_path()
    payload = config.model_dump(mode="python")
    text = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    header = (
        "# stock-monitor config (UI/CLI에서 저장됨)\n"
        "# timeframe: 1m | 3m | 5m | 15m | 30m | 1h | 4h | 1d\n"
    )
    config_path.write_text(header + text, encoding="utf-8")
    return config_path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_db_path(config: AppConfig) -> Path:
    path = Path(config.history.db_path)
    if not path.is_absolute():
        path = project_root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
