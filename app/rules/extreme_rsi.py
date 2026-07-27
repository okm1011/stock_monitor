from __future__ import annotations

from app.rules.base import AlertRule, AlertSignal, IndicatorContext


class ExtremeRsiRule(AlertRule):
    """알람1: RSI 극단 과열/침체 진입."""

    id = "extreme_rsi"
    name = "극단적 과열 경보"

    def enabled(self, config) -> bool:
        return bool(config.rules.extreme_rsi.enabled)

    def evaluate(self, ctx: IndicatorContext, config) -> list[AlertSignal]:
        i = ctx.i
        if i < 1:
            return []
        rsi = ctx.rsi[i]
        prev = ctx.rsi[i - 1]
        if rsi is None or prev is None:
            return []

        cfg = config.rules.extreme_rsi
        out: list[AlertSignal] = []
        # 진입 순간만 (이전엔 밖, 지금 안)
        if prev < cfg.high <= rsi:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="watch_short",
                    title=f"⚠️ {ctx.timeframe}봉 RSI 극단적 과열 구간 진입 [{rsi:.2f}]",
                    detail="관망/스탠바이 (즉시 진입 금지)",
                    extras={"rsi": rsi},
                )
            )
        if prev > cfg.low >= rsi:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="watch_long",
                    title=f"⚠️ {ctx.timeframe}봉 RSI 극단적 침체 구간 진입 [{rsi:.2f}]",
                    detail="관망/스탠바이 (즉시 진입 금지)",
                    extras={"rsi": rsi},
                )
            )
        return out
