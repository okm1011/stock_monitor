from __future__ import annotations

from app.rules.base import AlertRule, AlertSignal, IndicatorContext


class BbSqueezeRule(AlertRule):
    """알람4: 볼린저 스퀴즈 후 상/하단 돌파 마감."""

    id = "bb_squeeze"
    name = "BB 스퀴즈 돌파"

    def enabled(self, config) -> bool:
        return bool(config.rules.bb_squeeze.enabled)

    def evaluate(self, ctx: IndicatorContext, config) -> list[AlertSignal]:
        i = ctx.i
        mid, up, low = ctx.bb_mid[i], ctx.bb_upper[i], ctx.bb_lower[i]
        if mid is None or up is None or low is None or mid == 0:
            return []

        width_ratio = (up - low) / mid
        cfg = config.rules.bb_squeeze
        if width_ratio > cfg.squeeze_ratio:
            return []

        close = ctx.closes[i]
        out: list[AlertSignal] = []
        if close > up:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="long",
                    title=f"🚀 볼린저 밴드 스퀴즈 상단 돌파",
                    detail=f"압축비={width_ratio:.4f} (<= {cfg.squeeze_ratio})",
                    extras={"width_ratio": width_ratio, "bb_upper": up},
                )
            )
        elif close < low:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="short",
                    title=f"🚀 볼린저 밴드 스퀴즈 하단 돌파",
                    detail=f"압축비={width_ratio:.4f} (<= {cfg.squeeze_ratio})",
                    extras={"width_ratio": width_ratio, "bb_lower": low},
                )
            )
        return out
