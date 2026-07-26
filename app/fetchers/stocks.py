from __future__ import annotations

from typing import Iterable

import yfinance as yf

from app.config import StockSymbol
from app.models import AssetClass, Quote


class StockFetcher:
    """Yahoo Finance(yfinance)로 한국/미국 주식 시세를 조회합니다."""

    name = "yfinance"

    def __init__(
        self,
        symbols: Iterable[StockSymbol],
        asset_class: AssetClass,
        default_currency: str,
    ) -> None:
        self.symbols = list(symbols)
        self.asset_class = asset_class
        self.default_currency = default_currency

    def fetch(self) -> list[Quote]:
        if not self.symbols:
            return []

        quotes: list[Quote] = []
        for item in self.symbols:
            quote = self._fetch_one(item)
            if quote is not None:
                quotes.append(quote)
        return quotes

    def _fetch_one(self, item: StockSymbol) -> Quote | None:
        ticker = yf.Ticker(item.ticker)
        info: dict = {}
        try:
            # fast_info 우선 (가벼움). 실패 시 info로 폴백.
            fast = getattr(ticker, "fast_info", None)
            if fast is not None:
                info = dict(fast)
        except Exception:
            info = {}

        price = _first_number(info, ("lastPrice", "last_price", "regularMarketPrice"))
        prev_close = _first_number(info, ("previousClose", "previous_close", "regularMarketPreviousClose"))
        currency = info.get("currency") or self.default_currency

        # fast_info에 가격이 없으면 최근 1일 히스토리로 폴백
        if price is None:
            hist = ticker.history(period="5d", interval="1d")
            if hist is None or hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
            currency = self.default_currency

        change_pct = None
        if price is not None and prev_close not in (None, 0):
            change_pct = ((float(price) - float(prev_close)) / float(prev_close)) * 100.0

        volume = _first_number(info, ("lastVolume", "last_volume", "regularMarketVolume"))
        day_high = _first_number(info, ("dayHigh", "day_high", "regularMarketDayHigh"))
        day_low = _first_number(info, ("dayLow", "day_low", "regularMarketDayLow"))
        market_cap = _first_number(info, ("marketCap", "market_cap"))

        return Quote(
            asset_class=self.asset_class,
            symbol=item.ticker,
            name=item.name,
            price=float(price),
            currency=str(currency).upper(),
            change_pct=change_pct,
            volume=volume,
            high_24h=day_high,
            low_24h=day_low,
            market_cap=market_cap,
            source=self.name,
            raw={k: _jsonable(v) for k, v in info.items()} if info else None,
        )


def _first_number(data: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    return None


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return float(value)  # numpy scalars 등
    except (TypeError, ValueError):
        return str(value)
