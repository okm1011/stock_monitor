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
    # 예: SKHYUSDT — 설정 시 Yahoo 대신 Binance USDⓈ-M 선물 kline 사용
    binance_futures: str | None = None


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
    # True: 형성 중 봉 + 실시간 가격으로 RSI 돌파 즉시 알람 (봉 마감 대기 안 함)
    live: bool = True


class RsiMacdCrossRuleConfig(BaseModel):
    enabled: bool = False
    oversold: float = 30.0
    overbought: float = 70.0


class DivergenceRuleConfig(BaseModel):
    enabled: bool = False
    oversold: float = 30.0
    overbought: float = 70.0
    lookback: int = 60
    pivot_left: int = 3
    pivot_right: int = 3
    use_rsi: bool = True
    use_macd: bool = True


class BbSqueezeRuleConfig(BaseModel):
    enabled: bool = False
    squeeze_ratio: float = 0.05


DEFAULT_FUTURES_EXCLUDE_BASES = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "DOT",
    "TRX",
    "LTC",
    "BCH",
    "NEAR",
    "SUI",
    "PEPE",
    "WLD",
    "UNI",
    "AAVE",
    "FIL",
    "TON",
    "SHIB",
    "APT",
    "ARB",
    "OP",
    "ATOM",
    "ICP",
    "HYPE",
    "USDC",
    "FDUSD",
    "TUSD",
    "DAI",
    "USDE",
    "USDP",
    "BUSD",
    "PYUSD",
]


class VolumeSpikeRuleConfig(BaseModel):
    """
    잡코인 펌프 초입 알람 (1+2+3 필터 AND, 형성 중 봉 포함):
    1) 최근 window_bars 동안 가격 min_price_pct% 이상 + 최신봉 거래량 volume_mult배
    2) 그 직전 quiet_bars 동안 고저 폭이 quiet_range_pct% 이하 (바닥 횡보)
    3) exclude_bases 메이저 제외, 선물 USDT 퍼페추얼 전체
    """

    enabled: bool = True
    timeframe: Timeframe = "3m"
    window_bars: int = 5  # 가격 급등 구간 (3m×5=15분)
    min_price_pct: float = 15.0
    volume_lookback: int = 20
    volume_mult: float = 4.0
    quiet_bars: int = 40  # 횡보 구간 (3m×40≈2시간)
    quiet_range_pct: float = 10.0
    cooldown_seconds: int = 600
    poll_seconds: float = 30.0  # 형성 중 봉 감시 주기
    max_workers: int = 15
    symbol_refresh_hours: float = 24.0
    exclude_bases: list[str] = Field(default_factory=lambda: list(DEFAULT_FUTURES_EXCLUDE_BASES))

    @field_validator("window_bars", "volume_lookback", "quiet_bars")
    @classmethod
    def _bars_ok(cls, v: int) -> int:
        if v < 2:
            raise ValueError("bar counts must be >= 2")
        return v

    @field_validator("min_price_pct", "volume_mult", "quiet_range_pct")
    @classmethod
    def _pos_ok(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("poll_seconds")
    @classmethod
    def _poll_ok(cls, v: float) -> float:
        if v < 30:
            raise ValueError("volume_spike.poll_seconds must be >= 30")
        return v

    @property
    def history_bars(self) -> int:
        return self.quiet_bars + self.window_bars + self.volume_lookback + 5


class AccumulationRuleConfig(BaseModel):
    """
    알트코인 신호 1 — OI 변동 (프리랠리 관심, 펀딩 없음).

    게이트: 아직 수직 펌프 아님 + 최소 OI + 스테이블/메이저 제외.
    신호: **OI↑ 필수** + (타이트 박스 / 거래량 회복 / BTC 대비 강세) 중 1개 이상.
      - tight: 14일 고저가 타이트
      - oi: 7일 달러 OI 상승
      - vol: 최근 거래량이 직전 평균 대비 회복
      - rs: 같은 기간 BTC보다 덜 빠지거나 더 강함
    """

    enabled: bool = True
    range_days: int = 14
    range_pct: float = 38.0  # 게이트: 이미 수직으로 간 차트 제외
    tight_range_pct: float = 26.0  # 신호: 타이트 박스
    oi_days: int = 7
    oi_change_pct: float = 15.0
    min_oi_usdt: float = 1_500_000.0
    trend_days: int = 7
    trend_min_pct: float = -30.0
    trend_max_pct: float = 22.0
    vol_recent_bars: int = 3
    vol_lookback: int = 10
    vol_mult: float = 1.4
    btc_rs_pct: float = 4.5  # alt_7d - btc_7d (0이면 RS 신호 끔)
    cooldown_seconds: int = 86400  # 24h
    poll_seconds: float = 3600.0
    max_workers: int = 8
    symbol_refresh_hours: float = 24.0
    exclude_bases: list[str] = Field(default_factory=lambda: list(DEFAULT_FUTURES_EXCLUDE_BASES))

    @field_validator("range_days", "oi_days", "trend_days", "vol_recent_bars", "vol_lookback")
    @classmethod
    def _days_ok(cls, v: int) -> int:
        if v < 2:
            raise ValueError("counts must be >= 2")
        if v > 30:
            raise ValueError("lookback max 30 (Binance hist is ~1 month)")
        return v

    @field_validator("range_pct", "tight_range_pct", "oi_change_pct", "min_oi_usdt", "vol_mult")
    @classmethod
    def _pos_ok(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("poll_seconds")
    @classmethod
    def _poll_ok(cls, v: float) -> float:
        if v < 300:
            raise ValueError("accumulation.poll_seconds must be >= 300")
        return v


class AbsorptionBarRuleConfig(BaseModel):
    """
    알트코인 신호 2 — 매집봉(흡수봉):
    윗꼬리 김 + 거래량 급증 + 종가는 거의 안 움직임.
    형성 중 봉 포함, 메이저/스테이블 제외 선물 USDT 퍼페추얼.
    """

    enabled: bool = True
    timeframe: Timeframe = "4h"
    wick_ratio: float = 0.62  # 윗꼬리 / (고-저)
    max_body_ratio: float = 0.25  # 몸통 / (고-저)
    max_close_pct: float = 2.5  # |종가-전봉종가| %
    min_range_pct: float = 1.8  # 봉 자체 고저가 너무 작으면 제외
    volume_lookback: int = 20
    volume_mult: float = 3.5
    cooldown_seconds: int = 28800  # 8h, 연속 4h봉 중복 완화
    poll_seconds: float = 60.0
    max_workers: int = 10
    symbol_refresh_hours: float = 24.0
    exclude_bases: list[str] = Field(default_factory=lambda: list(DEFAULT_FUTURES_EXCLUDE_BASES))

    @field_validator("volume_lookback")
    @classmethod
    def _lookback_ok(cls, v: int) -> int:
        if v < 5:
            raise ValueError("volume_lookback must be >= 5")
        return v

    @field_validator("wick_ratio", "max_body_ratio")
    @classmethod
    def _ratio_ok(cls, v: float) -> float:
        if not (0.05 <= v <= 0.95):
            raise ValueError("ratio must be 0.05..0.95")
        return v

    @field_validator("max_close_pct", "min_range_pct", "volume_mult")
    @classmethod
    def _pos_ok(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("poll_seconds")
    @classmethod
    def _poll_ok(cls, v: float) -> float:
        if v < 30:
            raise ValueError("absorption_bar.poll_seconds must be >= 30")
        return v


class RulesConfig(BaseModel):
    extreme_rsi: ExtremeRsiRuleConfig = Field(default_factory=ExtremeRsiRuleConfig)
    rsi_macd_cross: RsiMacdCrossRuleConfig = Field(default_factory=RsiMacdCrossRuleConfig)
    divergence: DivergenceRuleConfig = Field(default_factory=DivergenceRuleConfig)
    bb_squeeze: BbSqueezeRuleConfig = Field(default_factory=BbSqueezeRuleConfig)
    volume_spike: VolumeSpikeRuleConfig = Field(default_factory=VolumeSpikeRuleConfig)
    accumulation: AccumulationRuleConfig = Field(default_factory=AccumulationRuleConfig)
    absorption_bar: AbsorptionBarRuleConfig = Field(default_factory=AbsorptionBarRuleConfig)


class HistoryConfig(BaseModel):
    max_candles: int = 300
    db_path: str = "data/candles.db"


class UniverseConfig(BaseModel):
    """바이낸스 상장 페어 중 거래량 상위 %를 매일 갱신해 감시."""

    enabled: bool = True
    # binance_spot: 현물 USDT (코인). 주식/ETF는 바이낸스에 거의 없음.
    source: Literal["binance_spot"] = "binance_spot"
    quote_asset: str = "USDT"
    volume_lookback_days: int = 7
    top_percentile: float = 30.0  # 부하 크면 15
    max_symbols: int = 60  # t3.micro 안전 상한
    refresh_hours: float = 24.0
    exclude_leveraged: bool = True
    exclude_stablecoins: bool = True
    include_static_stocks: bool = False  # True면 config의 kr/us 주식도 함께

    @field_validator("top_percentile")
    @classmethod
    def _pct_ok(cls, v: float) -> float:
        if not (1.0 <= v <= 100.0):
            raise ValueError("universe.top_percentile must be 1..100")
        return v


class AppConfig(BaseModel):
    crypto: list[CryptoSymbol] = Field(default_factory=list)
    kr_stocks: list[StockSymbol] = Field(default_factory=list)
    us_stocks: list[StockSymbol] = Field(default_factory=list)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
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
