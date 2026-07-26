from __future__ import annotations

from app.config import AppConfig
from app.fetchers.crypto import CryptoFetcher
from app.fetchers.stocks import StockFetcher
from app.models import AssetClass, Quote


def fetch_all(config: AppConfig) -> list[Quote]:
    """설정된 모든 자산 시세를 모아 반환 (verify용)."""
    fetchers = [
        CryptoFetcher(config.crypto),
        StockFetcher(config.kr_stocks, AssetClass.KR_STOCK, default_currency="KRW"),
        StockFetcher(config.us_stocks, AssetClass.US_STOCK, default_currency="USD"),
    ]

    quotes: list[Quote] = []
    errors: list[str] = []
    for fetcher in fetchers:
        try:
            quotes.extend(fetcher.fetch())
        except Exception as exc:
            errors.append(f"{fetcher.name}: {exc}")

    if errors and not quotes:
        raise RuntimeError("모든 시세 조회 실패:\n" + "\n".join(errors))
    return quotes
