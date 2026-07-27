from __future__ import annotations

from app.indicators import find_pivots
from app.rules.base import AlertRule, AlertSignal, IndicatorContext


class DivergenceRule(AlertRule):
    """알람3: 과열 구간 다이버전스."""

    id = "divergence"
    name = "과열 다이버전스"

    def enabled(self, config) -> bool:
        return bool(config.rules.divergence.enabled)

    def evaluate(self, ctx: IndicatorContext, config) -> list[AlertSignal]:
        i = ctx.i
        rsi = ctx.rsi[i]
        if rsi is None or i < 10:
            return []

        cfg = config.rules.divergence
        start = max(0, i - cfg.lookback + 1)
        # lookback 윈도우를 0-based 로컬 인덱스로
        closes = ctx.closes[start : i + 1]
        rsi_vals = ctx.rsi[start : i + 1]
        macd_vals = ctx.macd[start : i + 1]

        out: list[AlertSignal] = []

        if rsi <= cfg.oversold:
            bull = self._bullish(closes, rsi_vals, macd_vals, cfg)
            if bull:
                out.append(
                    AlertSignal(
                        rule_id=self.id,
                        side="long",
                        title=f"🔥 상승 다이버전스 포착",
                        detail=f"RSI 과매도({rsi:.2f}) + {bull}",
                        extras={"rsi": rsi, "kind": bull},
                    )
                )

        if rsi >= cfg.overbought:
            bear = self._bearish(closes, rsi_vals, macd_vals, cfg)
            if bear:
                out.append(
                    AlertSignal(
                        rule_id=self.id,
                        side="short",
                        title=f"🔥 하강 다이버전스 포착",
                        detail=f"RSI 과매수({rsi:.2f}) + {bear}",
                        extras={"rsi": rsi, "kind": bear},
                    )
                )
        return out

    def _bullish(self, closes, rsi_vals, macd_vals, cfg) -> str | None:
        price_pivots = find_pivots(closes, cfg.pivot_left, cfg.pivot_right, "low")
        if len(price_pivots) < 2:
            return None
        (_, p1), (_, p2) = price_pivots[-2], price_pivots[-1]
        if not (p2 < p1):
            return None

        if cfg.use_rsi:
            rsi_for_pivot = [v if v is not None else 999.0 for v in rsi_vals]
            rp = find_pivots(rsi_for_pivot, cfg.pivot_left, cfg.pivot_right, "low")
            if len(rp) >= 2 and rp[-1][1] > rp[-2][1] and rp[-1][1] < 900:
                return "RSI 상승 다이버전스"

        if cfg.use_macd:
            macd_for_pivot = [v if v is not None else 999.0 for v in macd_vals]
            mp = find_pivots(macd_for_pivot, cfg.pivot_left, cfg.pivot_right, "low")
            if len(mp) >= 2 and mp[-1][1] > mp[-2][1] and mp[-1][1] < 900:
                return "MACD 상승 다이버전스"
        return None

    def _bearish(self, closes, rsi_vals, macd_vals, cfg) -> str | None:
        price_pivots = find_pivots(closes, cfg.pivot_left, cfg.pivot_right, "high")
        if len(price_pivots) < 2:
            return None
        (_, p1), (_, p2) = price_pivots[-2], price_pivots[-1]
        if not (p2 > p1):
            return None

        if cfg.use_rsi:
            rsi_for_pivot = [v if v is not None else -999.0 for v in rsi_vals]
            rp = find_pivots(rsi_for_pivot, cfg.pivot_left, cfg.pivot_right, "high")
            if len(rp) >= 2 and rp[-1][1] < rp[-2][1] and rp[-1][1] > -900:
                return "RSI 하락 다이버전스"

        if cfg.use_macd:
            macd_for_pivot = [v if v is not None else -999.0 for v in macd_vals]
            mp = find_pivots(macd_for_pivot, cfg.pivot_left, cfg.pivot_right, "high")
            if len(mp) >= 2 and mp[-1][1] < mp[-2][1] and mp[-1][1] > -900:
                return "MACD 하락 다이버전스"
        return None
