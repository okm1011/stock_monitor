from __future__ import annotations


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    series = calc_rsi_series(closes, period)
    if not series:
        return None
    return series[-1]


def calc_rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if period < 2 or n < period + 1:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from_avg(avg_gain, avg_loss)

    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        out[i] = _rsi_from_avg(avg_gain, avg_loss)
    return out


def _rsi_from_avg(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema_series(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period or period < 1:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def calc_macd_series(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(closes)
    macd: list[float | None] = [None] * n
    sig: list[float | None] = [None] * n
    hist: list[float | None] = [None] * n
    if n < slow:
        return macd, sig, hist

    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)
    macd_vals: list[float] = []
    macd_idx: list[int] = []
    for i in range(n):
        if fast_ema[i] is None or slow_ema[i] is None:
            continue
        v = float(fast_ema[i]) - float(slow_ema[i])
        macd[i] = v
        macd_vals.append(v)
        macd_idx.append(i)

    if len(macd_vals) < signal:
        return macd, sig, hist

    signal_ema = _ema_series(macd_vals, signal)
    for j, i in enumerate(macd_idx):
        if signal_ema[j] is None:
            continue
        sig[i] = signal_ema[j]
        hist[i] = float(macd[i]) - float(signal_ema[j])
    return macd, sig, hist


def calc_bollinger_series(
    closes: list[float],
    period: int = 20,
    stddev: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(closes)
    mid: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    if n < period or period < 2:
        return mid, upper, lower

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        sd = var**0.5
        mid[i] = m
        upper[i] = m + stddev * sd
        lower[i] = m - stddev * sd
    return mid, upper, lower


def calc_atr_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1 or period < 1:
        return out

    trs: list[float] = [0.0] * n
    trs[0] = highs[0] - lows[0]
    for i in range(1, n):
        trs[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    atr = sum(trs[1 : period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = ((atr * (period - 1)) + trs[i]) / period
        out[i] = atr
    return out


def find_pivots(
    values: list[float],
    left: int,
    right: int,
    mode: str,
) -> list[tuple[int, float]]:
    """mode: 'low' | 'high'. 반환: (index, value) 오름차순."""
    pivots: list[tuple[int, float]] = []
    n = len(values)
    if left < 1 or right < 1 or n < left + right + 1:
        return pivots
    for i in range(left, n - right):
        v = values[i]
        window_l = values[i - left : i]
        window_r = values[i + 1 : i + right + 1]
        if mode == "low":
            if all(v <= x for x in window_l) and all(v < x for x in window_r):
                pivots.append((i, v))
        else:
            if all(v >= x for x in window_l) and all(v > x for x in window_r):
                pivots.append((i, v))
    return pivots
