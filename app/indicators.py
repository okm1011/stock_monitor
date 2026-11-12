from __future__ import annotations


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """
    Wilder RSI (RMA). Binance/TradingView 기본 RSI와 동일.
    (과거 EMA(k=2/(n+1)) 방식은 차트와 수치가 어긋남)
    """
    n = len(closes)
    if period < 2 or n < period + 1:
        return None

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    # seed: SMA of first `period` changes, then Wilder smooth
    avg_gain = gains / period
    avg_loss = losses / period

    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    return _rsi_from_avg(avg_gain, avg_loss)


def _rsi_from_avg(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_sma(values: list[float], period: int) -> float | None:
    if period < 1 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def calc_ema(values: list[float], period: int) -> float | None:
    """TradingView ta.ema 와 같은 방식: 첫 period개 SMA로 시작 후 k=2/(n+1)."""
    n = len(values)
    if period < 1 or n < period:
        return None
    ema = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    for i in range(period, n):
        ema = values[i] * k + ema * (1.0 - k)
    return ema


# 거래량이 0인 봉(지수·금리·환율 등)이 이 비율을 넘으면 VWMA를 계산하지 않음
VWMA_MIN_VOLUME_COVERAGE = 0.9


def calc_vwma(closes: list[float], volumes: list[float], period: int) -> float | None:
    if period < 1 or len(closes) < period or len(volumes) < period:
        return None
    c = closes[-period:]
    v = volumes[-period:]
    if sum(1 for x in v if x > 0) < period * VWMA_MIN_VOLUME_COVERAGE:
        return None
    vol_sum = sum(v)
    if vol_sum <= 0:
        return None
    return sum(ci * vi for ci, vi in zip(c, v)) / vol_sum
