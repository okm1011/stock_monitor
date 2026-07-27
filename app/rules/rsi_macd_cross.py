from __future__ import annotations

from app.rules.base import AlertRule, AlertSignal, IndicatorContext


class RsiMacdCrossRule(AlertRule):
    """알람2: RSI 과열 탈출 + MACD 크로스."""

    id = "rsi_macd_cross"
    name = "RSI탈출+MACD크로스"

    def enabled(self, config) -> bool:
        return bool(config.rules.rsi_macd_cross.enabled)

    def evaluate(self, ctx: IndicatorContext, config) -> list[AlertSignal]:
        i = ctx.i
        if i < 1:
            return []
        rsi, prev_rsi = ctx.rsi[i], ctx.rsi[i - 1]
        macd, prev_macd = ctx.macd[i], ctx.macd[i - 1]
        sig, prev_sig = ctx.macd_signal[i], ctx.macd_signal[i - 1]
        if None in (rsi, prev_rsi, macd, prev_macd, sig, prev_sig):
            return []

        cfg = config.rules.rsi_macd_cross
        out: list[AlertSignal] = []

        rsi_exit_up = prev_rsi <= cfg.oversold < rsi
        rsi_exit_down = prev_rsi >= cfg.overbought > rsi
        golden = prev_macd <= prev_sig and macd > sig
        death = prev_macd >= prev_sig and macd < sig

        if rsi_exit_up and golden:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="long",
                    title=f"🟢 {ctx.timeframe}봉 MACD 상방 크로스",
                    detail=f"RSI {cfg.oversold} 상향탈출 + 골든크로스 (RSI={rsi:.2f})",
                    extras={"rsi": rsi, "macd": macd, "signal": sig},
                )
            )
        if rsi_exit_down and death:
            out.append(
                AlertSignal(
                    rule_id=self.id,
                    side="short",
                    title=f"🟢 {ctx.timeframe}봉 MACD 하방 크로스",
                    detail=f"RSI {cfg.overbought} 하향탈출 + 데드크로스 (RSI={rsi:.2f})",
                    extras={"rsi": rsi, "macd": macd, "signal": sig},
                )
            )
        return out
