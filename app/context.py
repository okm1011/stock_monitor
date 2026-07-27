from __future__ import annotations

from app.config import AppConfig
from app.indicators import (
    calc_atr_series,
    calc_bollinger_series,
    calc_macd_series,
    calc_rsi_series,
)
from app.models import Candle, WatchTarget
from app.rules.base import IndicatorContext


def build_context(
    target: WatchTarget,
    timeframe: str,
    candles: list[Candle],
    config: AppConfig,
) -> IndicatorContext | None:
    if len(candles) < 30:
        return None
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    rsi = calc_rsi_series(closes, config.rsi.period)
    macd, signal, hist = calc_macd_series(
        closes,
        config.macd.fast,
        config.macd.slow,
        config.macd.signal,
    )
    mid, upper, lower = calc_bollinger_series(
        closes,
        config.bollinger.period,
        config.bollinger.stddev,
    )
    atr = calc_atr_series(highs, lows, closes, config.atr.period)
    return IndicatorContext(
        target=target,
        timeframe=timeframe,
        candles=candles,
        closes=closes,
        highs=highs,
        lows=lows,
        rsi=rsi,
        macd=macd,
        macd_signal=signal,
        macd_hist=hist,
        bb_mid=mid,
        bb_upper=upper,
        bb_lower=lower,
        atr=atr,
    )
