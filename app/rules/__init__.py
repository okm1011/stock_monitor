from __future__ import annotations

from app.rules.base import AlertRule
from app.rules.bb_squeeze import BbSqueezeRule
from app.rules.divergence import DivergenceRule
from app.rules.extreme_rsi import ExtremeRsiRule
from app.rules.rsi_macd_cross import RsiMacdCrossRule

# 새 조건 추가 시 여기 리스트에 클래스만 추가하면 됨
RULE_CLASSES: list[type[AlertRule]] = [
    ExtremeRsiRule,
    RsiMacdCrossRule,
    DivergenceRule,
    BbSqueezeRule,
]


def build_rules() -> list[AlertRule]:
    return [cls() for cls in RULE_CLASSES]
