from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock, local
from typing import Any

import httpx

from app.series import Bar
from app.timeframes import BINANCE_INTERVAL


# 바이낸스 선물 IP당 2400 weight/분. 여유를 두고 이 값을 넘으면 다음 분까지 대기
WEIGHT_SOFT_LIMIT = 1600


@dataclass(frozen=True)
class FuturesSymbol:
    symbol: str  # BTCUSDT
    base: str  # BTC
    kind: str  # COIN / EQUITY / COMMODITY / ...
    quote_volume: float


class BinanceFutures:
    BASE = "https://fapi.binance.com"

    def __init__(self) -> None:
        self._weight_lock = Lock()
        self._used_weight = 0
        self._weight_minute = 0
        self._local = local()
        self._clients: list[httpx.Client] = []

    def close(self) -> None:
        with self._weight_lock:
            clients, self._clients = self._clients, []
        for c in clients:
            try:
                c.close()
            except Exception:
                pass
        self._local = local()

    def top_symbols(
        self,
        top_n: int,
        include_tradfi: bool,
        exclude_bases: list[str],
    ) -> list[FuturesSymbol]:
        info = self._get("/fapi/v1/exchangeInfo")
        allowed_types = {"PERPETUAL", "TRADIFI_PERPETUAL"} if include_tradfi else {"PERPETUAL"}
        excluded = {b.upper() for b in exclude_bases}
        meta: dict[str, tuple[str, str]] = {}
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            if s.get("contractType") not in allowed_types:
                continue
            base = str(s.get("baseAsset", "")).upper()
            if base in excluded:
                continue
            meta[s["symbol"]] = (base, str(s.get("underlyingType") or ""))

        tickers = self._get("/fapi/v1/ticker/24hr")
        ranked: list[FuturesSymbol] = []
        for row in tickers:
            sym = row.get("symbol")
            if sym not in meta:
                continue
            try:
                qv = float(row.get("quoteVolume") or 0.0)
            except (TypeError, ValueError):
                continue
            if qv <= 0:
                continue
            base, kind = meta[sym]
            ranked.append(FuturesSymbol(symbol=sym, base=base, kind=kind, quote_volume=qv))
        ranked.sort(key=lambda x: x.quote_volume, reverse=True)
        return ranked[:top_n]

    def klines(self, symbol: str, tf: str, limit: int) -> list[Bar]:
        rows = self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": BINANCE_INTERVAL[tf], "limit": min(limit, 1500)},
            weight=_klines_weight(limit),
        )
        return [Bar(open_time=int(r[0]) // 1000, close=float(r[4]), volume=float(r[5])) for r in rows]

    def _get(self, path: str, params: dict[str, Any] | None = None, weight: int = 40) -> Any:
        last_exc: Exception | None = None
        for attempt in range(4):
            self._reserve(weight)
            try:
                resp = self._client().get(f"{self.BASE}{path}", params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(1.0 * (attempt + 1))
                continue
            self._record(resp)
            if resp.status_code in (418, 429):
                retry = _int(resp.headers.get("Retry-After"), 10)
                time.sleep(min(max(retry, 5), 120))
                last_exc = RuntimeError(f"Binance rate limited ({resp.status_code})")
                continue
            if resp.status_code >= 500:
                last_exc = RuntimeError(f"Binance {resp.status_code}")
                time.sleep(1.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Binance {path} 실패: {last_exc}")

    def _reserve(self, weight: int) -> None:
        while True:
            with self._weight_lock:
                minute = int(time.time() // 60)
                if minute != self._weight_minute:
                    self._weight_minute = minute
                    self._used_weight = 0
                if self._used_weight + weight <= WEIGHT_SOFT_LIMIT:
                    self._used_weight += weight
                    return
                wait = 60.0 - (time.time() % 60.0) + 0.5
            time.sleep(wait)

    def _record(self, resp: httpx.Response) -> None:
        used = _int(resp.headers.get("X-MBX-USED-WEIGHT-1M"), -1)
        if used < 0:
            return
        with self._weight_lock:
            if int(time.time() // 60) == self._weight_minute:
                self._used_weight = max(self._used_weight, used)

    def _client(self) -> httpx.Client:
        client = getattr(self._local, "client", None)
        if client is None:
            client = httpx.Client(timeout=20.0)
            self._local.client = client
            with self._weight_lock:
                self._clients.append(client)
        return client


def _klines_weight(limit: int) -> int:
    if limit < 100:
        return 1
    if limit < 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
