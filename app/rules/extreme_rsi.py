from __future__ import annotations

from app.rules.base import AlertRule, AlertSignal, IndicatorContext


class ExtremeRsiRule(AlertRule):
    """알람1: RSI 극단 과열/침체 — 형성 중 봉 포함, 폴링 간 RSI 돌파 시 즉시."""

    id = "extreme_rsi"
    name = "극단적 과열 경보"

    def __init__(self) -> None:
        # 심볼별 직전 관측 RSI (라이브 돌파 감지용)
        self._last_rsi: dict[str, float] = {}

    def enabled(self, config) -> bool:
        return bool(config.rules.extreme_rsi.enabled)

    def evaluate(self, ctx: IndicatorContext, config) -> list[AlertSignal]:
        i = ctx.i
        if i < 0:
            return []
        rsi = ctx.rsi[i]
        if rsi is None:
            return []

        key = ctx.target.key
        prev = self._last_rsi.get(key)
        self._last_rsi[key] = float(rsi)
        # 첫 관측은 기준만 잡고 알람 없음 (백필/기동 폭주 방지)
        if prev is None:
            return []

        cfg = config.rules.extreme_rsi
        out: list[AlertSignal] = []
        # 라이브: 직전 폴링 대비 임계 돌파 순간만
        if prev < cfg.high <= rsi:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="watch_short",
                    title=f"⚠️ {ctx.timeframe}봉 RSI 극단적 과열 구간 진입 [{rsi:.2f}]",
                    detail="관망/스탠바이 (즉시 진입 금지)",
                    extras={"rsi": rsi, "prev_rsi": prev},
                )
            )
        if prev > cfg.low >= rsi:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="watch_long",
                    title=f"⚠️ {ctx.timeframe}봉 RSI 극단적 침체 구간 진입 [{rsi:.2f}]",
                    detail="관망/스탠바이 (즉시 진입 금지)",
                    extras={"rsi": rsi, "prev_rsi": prev},
                )
            )
        return out
