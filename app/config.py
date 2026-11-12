from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


DEFAULT_EXCLUDE_BASES = [
    "USDC",
    "FDUSD",
    "TUSD",
    "BUSD",
    "USDP",
    "DAI",
    "USDE",
    "USD1",
    "PYUSD",
    "BFUSD",
    "XUSD",
]


class UniverseConfig(BaseModel):
    """바이낸스 USDⓈ-M 선물 24시간 거래대금 상위 N개 (코인 + 주식/원자재 토큰)."""

    enabled: bool = True
    top_n: int = 100
    # False면 코인(PERPETUAL)만, True면 주식·원자재 토큰(TRADIFI_PERPETUAL)도 포함
    include_tradfi: bool = True
    refresh_hours: float = 24.0
    exclude_bases: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_BASES))

    @field_validator("top_n")
    @classmethod
    def _top_ok(cls, v: int) -> int:
        if not (1 <= v <= 300):
            raise ValueError("universe.top_n must be 1..300")
        return v

    @field_validator("refresh_hours")
    @classmethod
    def _refresh_ok(cls, v: float) -> float:
        if v < 1:
            raise ValueError("universe.refresh_hours must be >= 1")
        return v

    @field_validator("exclude_bases")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s and s.strip()]


class MacroAsset(BaseModel):
    ticker: str
    name: str

    @field_validator("ticker", "name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("macro asset ticker/name must not be empty")
        return v


DEFAULT_MACRO_ASSETS: list[dict[str, str]] = [
    {"ticker": "NQ=F", "name": "나스닥100 선물"},
    {"ticker": "ES=F", "name": "S&P500 선물"},
    {"ticker": "RTY=F", "name": "러셀2000 선물"},
    {"ticker": "SOXX", "name": "SOXX 반도체 ETF"},
    {"ticker": "GC=F", "name": "금 선물"},
    {"ticker": "SI=F", "name": "은 선물"},
    {"ticker": "PL=F", "name": "백금 선물"},
    {"ticker": "HG=F", "name": "구리 선물"},
    {"ticker": "CL=F", "name": "WTI 원유"},
    {"ticker": "BZ=F", "name": "브렌트유"},
    {"ticker": "NG=F", "name": "천연가스"},
    {"ticker": "DX-Y.NYB", "name": "달러인덱스"},
    {"ticker": "^TNX", "name": "미 10년물 금리"},
    {"ticker": "TLT", "name": "TLT 미 장기채 ETF"},
    {"ticker": "^VIX", "name": "VIX"},
    {"ticker": "JPY=X", "name": "달러/엔"},
    {"ticker": "KRW=X", "name": "달러/원"},
    {"ticker": "^KS11", "name": "코스피"},
    {"ticker": "^N225", "name": "니케이225"},
    {"ticker": "^HSI", "name": "항셍"},
]


class MacroConfig(BaseModel):
    """야후 파이낸스로 받는 큰 자산 (지수·원자재·금리·환율)."""

    enabled: bool = True
    poll_seconds: float = 300.0
    assets: list[MacroAsset] = Field(
        default_factory=lambda: [MacroAsset(**a) for a in DEFAULT_MACRO_ASSETS]
    )

    @field_validator("poll_seconds")
    @classmethod
    def _poll_ok(cls, v: float) -> float:
        if v < 120:
            raise ValueError("macro.poll_seconds must be >= 120 (Yahoo 차단 방지)")
        return v

    @model_validator(mode="after")
    def _unique(self) -> MacroConfig:
        seen: set[str] = set()
        for a in self.assets:
            if a.ticker in seen:
                raise ValueError(f"macro.assets 중복 티커: {a.ticker}")
            seen.add(a.ticker)
        return self


class RsiTfConfig(BaseModel):
    enabled: bool = True
    overbought_enabled: bool = True
    overbought: float = 70.0
    oversold_enabled: bool = True
    oversold: float = 30.0

    @model_validator(mode="after")
    def _levels_ok(self) -> RsiTfConfig:
        if not (50.0 < self.overbought < 100.0):
            raise ValueError("RSI 과매수 기준은 50~100 사이여야 합니다")
        if not (0.0 < self.oversold < 50.0):
            raise ValueError("RSI 과매도 기준은 0~50 사이여야 합니다")
        return self


def _rsi_tf(ob: float, os_: float) -> RsiTfConfig:
    return RsiTfConfig(overbought=ob, oversold=os_)


class RsiAlertConfig(BaseModel):
    """알람 1: RSI가 과매수 기준을 상향 돌파 / 과매도 기준을 하향 돌파하는 순간 (형성 중 봉 포함)."""

    enabled: bool = True
    period: int = 14
    # 알람 후 RSI가 기준에서 이만큼 되돌아가야(과매수면 기준-이 값 이하) 같은 알람이 다시 울림
    rearm_gap: float = 5.0
    tf_4h: RsiTfConfig = Field(default_factory=lambda: _rsi_tf(75.0, 25.0))
    tf_1d: RsiTfConfig = Field(default_factory=lambda: _rsi_tf(70.0, 30.0))
    tf_1w: RsiTfConfig = Field(default_factory=lambda: _rsi_tf(70.0, 30.0))
    tf_1mo: RsiTfConfig = Field(default_factory=lambda: _rsi_tf(70.0, 30.0))

    @field_validator("period")
    @classmethod
    def _period_ok(cls, v: int) -> int:
        if not (2 <= v <= 100):
            raise ValueError("rsi_alert.period must be 2..100")
        return v

    @field_validator("rearm_gap")
    @classmethod
    def _gap_ok(cls, v: float) -> float:
        if not (0.0 <= v <= 30.0):
            raise ValueError("RSI 재무장 간격은 0~30 사이여야 합니다")
        return v

    def tf(self, tf: str) -> RsiTfConfig:
        return getattr(self, f"tf_{tf}")


class MaTfConfig(BaseModel):
    enabled: bool = True
    # 이격% = (가격-선)/선*100 의 절댓값이 이 값 이내로 들어오면 근접
    proximity_pct: float = 1.0
    from_above: bool = True
    from_below: bool = True

    @field_validator("proximity_pct")
    @classmethod
    def _prox_ok(cls, v: float) -> float:
        if not (0.05 <= v <= 30.0):
            raise ValueError("근접 %는 0.05~30 사이여야 합니다")
        return v


def _ma_tf(pct: float) -> MaTfConfig:
    return MaTfConfig(proximity_pct=pct)


class MaLineConfig(BaseModel):
    enabled: bool = True
    tf_4h: MaTfConfig = Field(default_factory=lambda: _ma_tf(1.0))
    tf_1d: MaTfConfig = Field(default_factory=lambda: _ma_tf(1.5))
    tf_1w: MaTfConfig = Field(default_factory=lambda: _ma_tf(3.0))
    tf_1mo: MaTfConfig = Field(default_factory=lambda: _ma_tf(5.0))

    def tf(self, tf: str) -> MaTfConfig:
        return getattr(self, f"tf_{tf}")


# (설정 키, 종류, 기간, 표시 이름)
MA_LINES: tuple[tuple[str, str, int, str], ...] = (
    ("ema50", "ema", 50, "EMA50"),
    ("ema200", "ema", 200, "EMA200"),
    ("vwma100", "vwma", 100, "VWMA100"),
)


class MaAlertConfig(BaseModel):
    """알람 2: 가격이 EMA50 / EMA200 / VWMA100 위·아래에서 근접 밴드 안으로 들어오는 순간."""

    enabled: bool = True
    # 알람 후 이격이 근접%×이 배수 이상으로 벌어져야 같은 알람이 다시 울림 (밴드 경계 잔진동 방지)
    rearm_mult: float = 3.0
    ema50: MaLineConfig = Field(default_factory=MaLineConfig)
    ema200: MaLineConfig = Field(default_factory=MaLineConfig)
    vwma100: MaLineConfig = Field(default_factory=MaLineConfig)

    @field_validator("rearm_mult")
    @classmethod
    def _mult_ok(cls, v: float) -> float:
        if not (1.0 <= v <= 10.0):
            raise ValueError("이평 재무장 배수는 1~10 사이여야 합니다")
        return v

    def line(self, key: str) -> MaLineConfig:
        return getattr(self, key)


class AppConfig(BaseModel):
    # 바이낸스 선물 시세·봉 갱신 주기
    poll_seconds: float = 60.0
    status_log_seconds: float = 600.0
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    rsi_alert: RsiAlertConfig = Field(default_factory=RsiAlertConfig)
    ma_alert: MaAlertConfig = Field(default_factory=MaAlertConfig)

    @field_validator("poll_seconds")
    @classmethod
    def _poll_ok(cls, v: float) -> float:
        if v < 30:
            raise ValueError("poll_seconds must be >= 30 (바이낸스 요청 한도)")
        return v

    @field_validator("status_log_seconds")
    @classmethod
    def _status_ok(cls, v: float) -> float:
        if v < 0:
            raise ValueError("status_log_seconds must be >= 0")
        return v


def default_config_path() -> Path:
    return project_root() / "config.yaml"


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with config_path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return AppConfig.model_validate(data)


CONFIG_HEADER = (
    "# stock-monitor config (웹 설정 페이지에서 저장하면 이 파일이 다시 쓰입니다)\n"
    "# 타임프레임: tf_4h=4시간봉, tf_1d=일봉, tf_1w=주봉, tf_1mo=월봉\n"
    "# 알람 1 rsi_alert: RSI 과매수 상향 돌파 / 과매도 하향 돌파 (형성 중 봉, 봉당 1회)\n"
    "# 알람 2 ma_alert: EMA50 / EMA200 / VWMA100 위·아래에서 근접% 이내 진입 (형성 중 봉, 봉당 1회)\n"
)


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    config_path = Path(path) if path else default_config_path()
    payload = config.model_dump(mode="python")
    text = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    config_path.write_text(CONFIG_HEADER + text, encoding="utf-8")
    return config_path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent
