from __future__ import annotations

from app.models import Quote


class QuoteFetcher:
    """시세 조회기 공통 인터페이스."""

    name: str = "base"

    def fetch(self) -> list[Quote]:
        raise NotImplementedError
