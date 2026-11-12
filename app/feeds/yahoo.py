from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

from app.series import Bar


# yfinance 1h는 최근 730일까지만 제공
BOOT_PERIOD: dict[str, str] = {"1h": "720d", "1d": "10y", "1wk": "max", "1mo": "max"}
REFRESH_PERIOD: dict[str, str] = {"1h": "5d", "1d": "1mo", "1wk": "6mo", "1mo": "2y"}

FOUR_HOURS = 4 * 3600
# 1h 봉 시작 간격이 이 이상 벌어지면 새 세션 (주식 장 마감, 선물/환율 주말).
# CME 선물의 하루 1시간 휴장(2h 간격)은 세션으로 보지 않아야 4h 구간이 18:00 ET 기준으로 유지된다.
SESSION_GAP_SECONDS = 3 * 3600

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def download(tickers: list[str], interval: str, period: str) -> dict[str, list[Bar]]:
    if not tickers:
        return {}
    df = yf.download(
        tickers=tickers,
        period=period,
        interval=interval,
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    out: dict[str, list[Bar]] = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        sub = _ticker_frame(df, t, len(tickers))
        if sub is None or "Close" not in sub:
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            continue
        closes = sub["Close"].astype(float).tolist()
        if "Volume" in sub:
            vols = sub["Volume"].fillna(0.0).astype(float).tolist()
        else:
            vols = [0.0] * len(closes)
        times = [_open_time(pd.Timestamp(ts), interval) for ts in sub.index]
        # 정규화 후 같은 봉이 겹치면 뒤 행(최신)이 남는다
        merged = {ot: Bar(open_time=ot, close=c, volume=v) for ot, c, v in zip(times, closes, vols)}
        out[t] = [merged[k] for k in sorted(merged)]
    return out


def _open_time(ts: pd.Timestamp, interval: str) -> int:
    """
    1h는 실제 UTC 시각. 일/주/월봉은 거래소 현지 날짜를 UTC 자정으로 고정하고,
    야후가 진행 중인 주/월봉에 오늘 날짜를 붙여 주는 경우가 있어 주 시작(월요일)·월 1일로 내린다.
    """
    if interval == "1h":
        return int(ts.timestamp())
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    day = ts.normalize()
    if interval == "1wk":
        day = day - pd.Timedelta(days=day.weekday())
    elif interval == "1mo":
        day = day.replace(day=1)
    return int(day.tz_localize("UTC").timestamp())


def aggregate_4h(bars_1h: list[Bar]) -> list[Bar]:
    """
    1h → 4h. 세션 시작부터 4시간씩 묶음 (미국 주식 09:30/13:30, CME 선물 18:00 ET 기준 등).
    버퍼 맨 앞 세션은 잘려 있을 수 있어 버린다.
    """
    buckets: dict[int, list[float]] = {}
    session_start: int | None = None
    prev_ot: int | None = None
    started = False
    for b in bars_1h:
        if prev_ot is None or b.open_time - prev_ot >= SESSION_GAP_SECONDS:
            if prev_ot is not None:
                started = True
            session_start = b.open_time
        prev_ot = b.open_time
        if not started or session_start is None:
            continue
        ot = session_start + ((b.open_time - session_start) // FOUR_HOURS) * FOUR_HOURS
        agg = buckets.get(ot)
        if agg is None:
            buckets[ot] = [b.close, b.volume]
        else:
            agg[0] = b.close
            agg[1] += b.volume
    return [Bar(open_time=ot, close=c, volume=v) for ot, (c, v) in sorted(buckets.items())]


def _ticker_frame(df: pd.DataFrame, ticker: str, n_tickers: int) -> pd.DataFrame | None:
    cols = df.columns
    if isinstance(cols, pd.MultiIndex):
        if ticker in cols.get_level_values(0):
            return df[ticker]
        if ticker in cols.get_level_values(1):
            return df.xs(ticker, axis=1, level=1)
        return None
    return df if n_tickers == 1 else None
