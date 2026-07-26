from __future__ import annotations

import time
from typing import Iterable

import httpx
import yfinance as yf

from app.models import AssetClass, Candle, Quote, WatchTarget
from app.timeframes import BINANCE_INTERVAL, YFINANCE_INTERVAL


class LivePriceFetcher:
    """폴링용 최신 가격. 코인=Binance(매 폴링), 주식=yfinance(캐시 간격)."""

    def __init__(self, stock_min_interval: float = 5.0) -> None:
        self._http = httpx.Client(timeout=15.0)
        self._stock_min_interval = stock_min_interval
        self._stock_cache: dict[str, float] = {}
        self._stock_cache_ts: float = 0.0

    def close(self) -> None:
        self._http.close()

    def fetch_prices(self, targets: Iterable[WatchTarget]) -> dict[str, float]:
        targets = list(targets)
        prices: dict[str, float] = {}
        crypto = [t for t in targets if t.asset_class == AssetClass.CRYPTO]
        stocks = [t for t in targets if t.asset_class != AssetClass.CRYPTO]

        if crypto:
            try:
                prices.update(self._fetch_crypto(crypto))
            except Exception:
                pass
        if stocks:
            prices.update(self._fetch_stocks_cached(stocks))
        return prices

    def _fetch_crypto(self, targets: list[WatchTarget]) -> dict[str, float]:
        resp = self._http.get("https://api.binance.com/api/v3/ticker/price")
        resp.raise_for_status()
        rows = {row["symbol"]: float(row["price"]) for row in resp.json()}
        return {t.key: rows[t.market_id] for t in targets if t.market_id in rows}

    def _fetch_stocks_cached(self, targets: list[WatchTarget]) -> dict[str, float]:
        now = time.monotonic()
        if self._stock_cache and (now - self._stock_cache_ts) < self._stock_min_interval:
            return {t.key: self._stock_cache[t.key] for t in targets if t.key in self._stock_cache}

        fresh = self._fetch_stocks(targets)
        if fresh:
            self._stock_cache.update(fresh)
            self._stock_cache_ts = now
        return {t.key: self._stock_cache[t.key] for t in targets if t.key in self._stock_cache}

    def _fetch_stocks(self, targets: list[WatchTarget]) -> dict[str, float]:
        """여러 종목을 한 번에 조회. 실패 시 개별 fast_info 폴백."""
        out: dict[str, float] = {}
        if not targets:
            return out

        tickers = [t.market_id for t in targets]
        try:
            data = yf.download(
                tickers=tickers,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            multi = len(targets) > 1
            for t in targets:
                price = self._extract_last_close(data, t.market_id, multi=multi)
                if price is not None:
                    out[t.key] = price
        except Exception:
            pass

        # 빠진 종목만 개별 폴백
        for t in targets:
            if t.key in out:
                continue
            price = self._stock_fast_price(t.market_id)
            if price is not None:
                out[t.key] = price
        return out

    @staticmethod
    def _extract_last_close(data, ticker: str, multi: bool) -> float | None:
        try:
            if data is None or getattr(data, "empty", True):
                return None
            if multi:
                cols = data.columns
                level0 = cols.get_level_values(0) if hasattr(cols, "get_level_values") else []
                if ticker not in level0:
                    return None
                series = data[ticker]["Close"].dropna()
            else:
                series = data["Close"].dropna()
            if series.empty:
                return None
            return float(series.iloc[-1])
        except Exception:
            return None

    @staticmethod
    def _stock_fast_price(ticker: str) -> float | None:
        try:
            info = getattr(yf.Ticker(ticker), "fast_info", None)
            if info is None:
                return None
            for key in ("lastPrice", "last_price", "regularMarketPrice"):
                if key in info and info[key] is not None:
                    return float(info[key])
        except Exception:
            return None
        return None


class OhlcvBackfiller:
    """시작 시 RSI 계산용 과거 봉 백필."""

    def __init__(self) -> None:
        self._http = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def backfill(self, target: WatchTarget, timeframe: str, limit: int) -> list[Candle]:
        if target.asset_class == AssetClass.CRYPTO:
            return self._binance_klines(target, timeframe, limit)
        return self._yahoo_history(target, timeframe, limit)

    def _binance_klines(
        self,
        target: WatchTarget,
        timeframe: str,
        limit: int,
    ) -> list[Candle]:
        interval = BINANCE_INTERVAL.get(timeframe)
        if not interval:
            raise ValueError(f"Unsupported timeframe for Binance: {timeframe}")
        params = {
            "symbol": target.market_id,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        resp = self._http.get("https://api.binance.com/api/v3/klines", params=params)
        resp.raise_for_status()
        candles: list[Candle] = []
        rows = resp.json()
        for i, row in enumerate(rows):
            open_time = int(row[0]) // 1000
            is_last = i == len(rows) - 1
            candles.append(
                Candle(
                    symbol_key=target.key,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    closed=not is_last,
                )
            )
        return candles

    def _yahoo_history(
        self,
        target: WatchTarget,
        timeframe: str,
        limit: int,
    ) -> list[Candle]:
        interval = YFINANCE_INTERVAL.get(timeframe, "1d")
        period = "7d" if interval == "1m" else ("60d" if interval.endswith("m") else "1y")
        hist = yf.Ticker(target.market_id).history(period=period, interval=interval, auto_adjust=True)
        if hist is None or hist.empty:
            return []

        candles: list[Candle] = []
        rows = hist.tail(limit)
        last_idx = len(rows) - 1
        for i, (idx, row) in enumerate(rows.iterrows()):
            ts = idx.to_pydatetime()
            open_time = int(ts.timestamp())
            vol = 0.0
            if "Volume" in row:
                try:
                    vol = float(row["Volume"])
                except (TypeError, ValueError):
                    vol = 0.0
            candles.append(
                Candle(
                    symbol_key=target.key,
                    timeframe=timeframe,
                    open_time=open_time,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=vol,
                    closed=i != last_idx,
                )
            )
        return candles


def build_watch_targets(config) -> list[WatchTarget]:
    from app.config import AppConfig

    assert isinstance(config, AppConfig)
    targets: list[WatchTarget] = []

    for c in config.crypto:
        targets.append(
            WatchTarget(
                key=f"crypto:{c.symbol}",
                asset_class=AssetClass.CRYPTO,
                symbol=c.symbol,
                name=c.name,
                currency="USDT",
                market_id=c.market_symbol(),
            )
        )
    for s in config.kr_stocks:
        targets.append(
            WatchTarget(
                key=f"kr:{s.ticker}",
                asset_class=AssetClass.KR_STOCK,
                symbol=s.ticker,
                name=s.name,
                currency="KRW",
                market_id=s.ticker,
            )
        )
    for s in config.us_stocks:
        targets.append(
            WatchTarget(
                key=f"us:{s.ticker}",
                asset_class=AssetClass.US_STOCK,
                symbol=s.ticker,
                name=s.name,
                currency="USD",
                market_id=s.ticker,
            )
        )
    return targets
