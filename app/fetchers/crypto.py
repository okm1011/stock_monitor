from __future__ import annotations

from typing import Iterable

import httpx

from app.config import CryptoSymbol
from app.models import AssetClass, Quote


class CryptoFetcher:
    """CoinGecko 공개 API로 암호화폐 시세를 조회합니다. (API 키 불필요)"""

    name = "coingecko"
    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self, symbols: Iterable[CryptoSymbol], vs_currency: str = "usd") -> None:
        self.symbols = list(symbols)
        self.vs_currency = vs_currency

    def fetch(self) -> list[Quote]:
        if not self.symbols:
            return []

        id_map = {s.id: s for s in self.symbols}
        ids = ",".join(id_map.keys())
        params = {
            "ids": ids,
            "vs_currencies": self.vs_currency,
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
            "include_market_cap": "true",
        }

        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{self.BASE_URL}/simple/price", params=params)
            resp.raise_for_status()
            payload: dict = resp.json()

        quotes: list[Quote] = []
        currency = self.vs_currency.upper()
        for coin_id, meta in id_map.items():
            row = payload.get(coin_id)
            if not row:
                continue
            price = row.get(self.vs_currency)
            if price is None:
                continue
            quotes.append(
                Quote(
                    asset_class=AssetClass.CRYPTO,
                    symbol=meta.symbol,
                    name=meta.name,
                    price=float(price),
                    currency=currency,
                    change_pct=_optional_float(row.get(f"{self.vs_currency}_24h_change")),
                    volume=_optional_float(row.get(f"{self.vs_currency}_24h_vol")),
                    market_cap=_optional_float(row.get(f"{self.vs_currency}_market_cap")),
                    source=self.name,
                    raw=row,
                )
            )
        return quotes


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
