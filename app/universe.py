from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import local

import httpx

from app.config import AppConfig, UniverseConfig, project_root
from app.models import AssetClass, DataFeed, WatchTarget

_thread_http = local()


@dataclass
class UniverseEntry:
    market_id: str  # BTCUSDT
    base: str  # BTC
    volume: float


class BinanceUniverseService:
    """
    바이낸스 현물 USDT 페어 중 최근 N일 거래대금 상위 X% 선정.
    (바이낸스에는 일반 주식/ETF 상장이 거의 없어 코인 USDT 페어가 대상.)
    """

    BASE = "https://api.binance.com"

    def __init__(self, cfg: UniverseConfig, cache_path: Path | None = None) -> None:
        self.cfg = cfg
        self.cache_path = cache_path or (project_root() / "data" / "universe.json")
        self._http = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def needs_refresh(self) -> bool:
        if not self.cache_path.exists():
            return True
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            updated = data.get("updated_at")
            if not updated:
                return True
            ts = datetime.fromisoformat(updated)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            return age_h >= float(self.cfg.refresh_hours)
        except Exception:
            return True

    def load_cached_targets(self) -> list[WatchTarget] | None:
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return [self._to_target(e) for e in data.get("symbols", [])]
        except Exception:
            return None

    def refresh(self, log=None) -> list[WatchTarget]:
        def _log(msg: str) -> None:
            if log:
                log(msg)

        quote = self.cfg.quote_asset.upper()
        _log(f"유니버스 갱신 시작 (Binance {quote}, {self.cfg.volume_lookback_days}일 거래량 상위 {self.cfg.top_percentile}%)")

        tradable = self._tradable_usdt_symbols(quote)
        _log(f"  거래가능 페어: {len(tradable)}개")

        tickers = self._http.get(f"{self.BASE}/api/v3/ticker/24hr").json()
        vol_24h: dict[str, float] = {}
        for row in tickers:
            sym = row.get("symbol")
            if sym not in tradable:
                continue
            try:
                vol_24h[sym] = float(row.get("quoteVolume") or 0.0)
            except (TypeError, ValueError):
                continue

        ranked_24h = sorted(vol_24h.items(), key=lambda x: x[1], reverse=True)
        if not ranked_24h:
            raise RuntimeError("Binance 거래량 데이터를 가져오지 못했습니다")

        # 7일 볼륨 계산 부하 완화: 24h 상위 후보만 상세 조회
        universe_n = len(ranked_24h)
        want_n = max(1, int(universe_n * (self.cfg.top_percentile / 100.0)))
        want_n = min(want_n, self.cfg.max_symbols)
        candidate_n = min(universe_n, max(want_n * 3, want_n + 20))
        candidates = [s for s, _ in ranked_24h[:candidate_n]]

        _log(f"  7일 거래량 조회 후보: {len(candidates)}개 → 최종 목표 {want_n}개")
        vol_7d = self._weekly_quote_volumes(candidates, self.cfg.volume_lookback_days, log=_log)

        scored = [(s, vol_7d.get(s, 0.0)) for s in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        # 거래량 0 제외
        scored = [(s, v) for s, v in scored if v > 0]
        selected = scored[:want_n]

        entries = [
            UniverseEntry(market_id=s, base=tradable[s], volume=v) for s, v in selected
        ]
        self._save_cache(entries, universe_n, want_n)
        _log(f"  유니버스 확정: {len(entries)}개 (전체 {universe_n}의 상위 {self.cfg.top_percentile}% / max={self.cfg.max_symbols})")
        return [self._to_target({"market_id": e.market_id, "base": e.base, "volume": e.volume}) for e in entries]

    def get_targets(self, log=None, force: bool = False) -> list[WatchTarget]:
        if not force and not self.needs_refresh():
            cached = self.load_cached_targets()
            if cached:
                if log:
                    log(f"유니버스 캐시 사용: {len(cached)}개 ({self.cache_path.name})")
                return cached
        return self.refresh(log=log)

    def _tradable_usdt_symbols(self, quote: str) -> dict[str, str]:
        info = self._http.get(f"{self.BASE}/api/v3/exchangeInfo").json()
        out: dict[str, str] = {}
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("quoteAsset") != quote:
                continue
            if not s.get("isSpotTradingAllowed", True):
                continue
            market_id = s["symbol"]
            base = s["baseAsset"]
            if self.cfg.exclude_leveraged and self._is_leveraged(base, market_id):
                continue
            if self.cfg.exclude_stablecoins and self._is_stablecoin(base):
                continue
            out[market_id] = base
        return out

    # 거래대금만 크고 시그널 의미가 약한 스테이블/페그 자산
    _STABLE_BASES = frozenset(
        {
            "USDC",
            "USD1",
            "FDUSD",
            "TUSD",
            "BUSD",
            "USDP",
            "DAI",
            "EUR",
            "EURI",
            "AEUR",
            "USDE",
            "BFUSD",
            "XUSD",
            "USD",
            "USDT",
        }
    )

    @classmethod
    def _is_stablecoin(cls, base: str) -> bool:
        b = base.upper()
        if b in cls._STABLE_BASES:
            return True
        # *USD 형태 스테이블 (예: PYUSD) — 단 BTC 등 제외는 길이로 대략 필터
        if b.endswith("USD") and len(b) <= 6 and b not in {"BTC", "ETH"}:
            return True
        return False

    @staticmethod
    def _is_leveraged(base: str, market_id: str) -> bool:
        b = base.upper()
        # UP/DOWN, BULL/BEAR 레버리지 토큰 제외
        for suf in ("UP", "DOWN", "BULL", "BEAR"):
            if b.endswith(suf) and len(b) > len(suf):
                return True
        return False

    def _weekly_quote_volumes(
        self,
        symbols: list[str],
        days: int,
        log=None,
    ) -> dict[str, float]:
        result: dict[str, float] = {}
        days = max(1, min(days, 30))

        def worker_http() -> httpx.Client:
            client = getattr(_thread_http, "client", None)
            if client is None:
                client = httpx.Client(timeout=30.0)
                _thread_http.client = client
            return client

        def one(sym: str) -> tuple[str, float]:
            http = worker_http()
            for attempt in range(3):
                try:
                    resp = http.get(
                        f"{self.BASE}/api/v3/klines",
                        params={"symbol": sym, "interval": "1d", "limit": days},
                    )
                    if resp.status_code == 429:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    rows = resp.json()
                    # kline[7] = quote asset volume
                    total = sum(float(r[7]) for r in rows)
                    return sym, total
                except Exception:
                    time.sleep(0.5 * (attempt + 1))
            return sym, 0.0

        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(one, s) for s in symbols]
            for fut in as_completed(futs):
                sym, vol = fut.result()
                result[sym] = vol
                done += 1
                if log and done % 40 == 0:
                    log(f"  7일 거래량 진행 {done}/{len(symbols)}")
        return result

    def _save_cache(self, entries: list[UniverseEntry], universe_n: int, want_n: int) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "universe_size": universe_n,
            "selected": len(entries),
            "top_percentile": self.cfg.top_percentile,
            "lookback_days": self.cfg.volume_lookback_days,
            "symbols": [
                {"market_id": e.market_id, "base": e.base, "volume": e.volume} for e in entries
            ],
        }
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _to_target(entry: dict) -> WatchTarget:
        market_id = entry["market_id"]
        base = entry.get("base") or market_id.replace("USDT", "")
        return WatchTarget(
            key=f"crypto:{base}",
            asset_class=AssetClass.CRYPTO,
            symbol=base,
            name=base,
            currency="USDT",
            market_id=market_id,
            feed=DataFeed.BINANCE_SPOT,
        )


def resolve_watch_targets(config: AppConfig, log=None, force_universe: bool = False) -> list[WatchTarget]:
    """유니버스 모드면 Binance 동적 목록, 아니면 config 고정 목록."""
    from app.fetchers.market import build_watch_targets

    if not config.universe.enabled:
        return build_watch_targets(config)

    svc = BinanceUniverseService(config.universe)
    try:
        targets = svc.get_targets(log=log, force=force_universe)
    finally:
        svc.close()

    # 옵션: 고정 주식도 함께
    if config.universe.include_static_stocks:
        extras = build_watch_targets(config)
        stocks = [t for t in extras if t.asset_class != AssetClass.CRYPTO]
        # crypto는 유니버스만 사용
        targets = targets + stocks
    return targets
