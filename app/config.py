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
    period: int = 7

    @field_validator("period")
    @classmethod
    def _period_ok(cls, v: int) -> int:
        if v < 2:
            raise ValueError("rsi.period must be >= 2")
        return v


class MacdConfig(BaseModel):
    fast: int = 12
    slow: int = 26
    signal: int = 9


class BollingerConfig(BaseModel):
    period: int = 20
    stddev: float = 2.0


class AtrConfig(BaseModel):
    period: int = 14
    sl_mult: float = 1.5
    tp_mult: float = 3.0


class ExtremeRsiRuleConfig(BaseModel):
    enabled: bool = True
    high: float = 80.0
    low: float = 23.0


class RsiMacdCrossRuleConfig(BaseModel):
    enabled: bool = True
    oversold: float = 30.0
    overbought: float = 70.0


class DivergenceRuleConfig(BaseModel):
    enabled: bool = True
    oversold: float = 30.0
    overbought: float = 70.0
    lookback: int = 60
    pivot_left: int = 3
    pivot_right: int = 3
    use_rsi: bool = True
    use_macd: bool = True


class BbSqueezeRuleConfig(BaseModel):
    enabled: bool = True
    squeeze_ratio: float = 0.05


class RulesConfig(BaseModel):
    extreme_rsi: ExtremeRsiRuleConfig = Field(default_factory=ExtremeRsiRuleConfig)
    rsi_macd_cross: RsiMacdCrossRuleConfig = Field(default_factory=RsiMacdCrossRuleConfig)
    divergence: DivergenceRuleConfig = Field(default_factory=DivergenceRuleConfig)
    bb_squeeze: BbSqueezeRuleConfig = Field(default_factory=BbSqueezeRuleConfig)


class HistoryConfig(BaseModel):
    max_candles: int = 300
    db_path: str = "data/candles.db"


class AppConfig(BaseModel):
    crypto: list[CryptoSymbol] = Field(default_factory=list)
    kr_stocks: list[StockSymbol] = Field(default_factory=list)
    us_stocks: list[StockSymbol] = Field(default_factory=list)
    poll_interval_seconds: float = 5.0
    timeframe: Timeframe = "1h"
    # 알람은 완성 봉 마감 기준
    signal_on_closed_bar: bool = True
    rsi: RsiConfig = Field(default_factory=RsiConfig)
    macd: MacdConfig = Field(default_factory=MacdConfig)
    bollinger: BollingerConfig = Field(default_factory=BollingerConfig)
    atr: AtrConfig = Field(default_factory=AtrConfig)
    rules: RulesConfig = Field(default_factory=RulesConfig)
    alert_cooldown_seconds: int = 300
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    status_log_seconds: float = 15.0

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
    # 구버전 rsi.min/max 호환: 무시하고 새 스키마로 로드
    if isinstance(data.get("rsi"), dict):
        legacy = data["rsi"]
        data["rsi"] = {"period": legacy.get("period", 7)}
    return AppConfig.model_validate(data)


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config.yaml"


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    config_path = Path(path) if path else default_config_path()
    payload = config.model_dump(mode="python")
    text = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    header = (
        "# stock-monitor config\n"
        "# timeframe: 1m | 3m | 5m | 15m | 30m | 1h | 4h | 1d\n"
        "# 알람은 signal_on_closed_bar=true 일 때 봉 마감 기준\n"
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
