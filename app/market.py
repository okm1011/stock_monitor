from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import MacroAsset
from app.feeds import yahoo
from app.feeds.binance import BinanceFutures, FuturesSymbol
from app.series import Asset, Bar, BarSeries
from app.timeframes import TIMEFRAMES, YAHOO_INTERVAL


LogFn = Callable[[str], None]
StopFn = Callable[[], bool]

# 4h/1d는 1000봉(EMA200 워밍업 800봉), 주/월봉은 상장 이후 전체가 499봉 이내
BINANCE_BOOT_LIMIT: dict[str, int] = {"4h": 1000, "1d": 1000, "1w": 499, "1mo": 499}
# 형성 중 봉 + 직전 마감 봉 갱신 (weight 1)
BINANCE_REFRESH_LIMIT = 3
BINANCE_WORKERS = 6
MAX_BARS = 1000
# 4h 합성용 1h 버퍼 (미국 주식 기준 4h 약 850봉)
YAHOO_1H_MAX_BARS = 3000

KIND_TAG: dict[str, str] = {
    "EQUITY": "주식",
    "KR_EQUITY": "한국주식",
    "HK_EQUITY": "홍콩주식",
    "CN_EQUITY": "중국주식",
    "COMMODITY": "원자재",
    "INDEX": "지수",
    "FX": "환율",
    "PREMARKET": "프리마켓",
}


class MarketData:
    """바이낸스 선물 상위 N + 야후 큰 자산의 4h/1d/1w/1mo 봉을 메모리에 유지."""

    def __init__(self, log: LogFn | None = None) -> None:
        self._log = log or (lambda _m: None)
        self.binance = BinanceFutures()
        self._bn_assets: dict[str, Asset] = {}
        self._bn_series: dict[tuple[str, str], BarSeries] = {}
        self._yf_assets: dict[str, Asset] = {}
        # (asset key, yahoo interval) → 1h / 1d / 1wk / 1mo
        self._yf_series: dict[tuple[str, str], BarSeries] = {}

    def close(self) -> None:
        self.binance.close()

    # ---------- 공통 ----------

    def assets(self) -> list[Asset]:
        return list(self._bn_assets.values()) + list(self._yf_assets.values())

    def bars(self, asset: Asset, tf: str) -> list[Bar]:
        if asset.source == "binance":
            s = self._bn_series.get((asset.key, tf))
            return s.bars() if s else []
        if tf == "4h":
            s = self._yf_series.get((asset.key, "1h"))
            return yahoo.aggregate_4h(s.bars()) if s else []
        s = self._yf_series.get((asset.key, YAHOO_INTERVAL[tf]))
        return s.bars() if s else []

    # ---------- 바이낸스 ----------

    def binance_assets(self) -> list[Asset]:
        return list(self._bn_assets.values())

    def set_binance_universe(self, symbols: list[FuturesSymbol]) -> tuple[list[Asset], list[Asset]]:
        """새 목록으로 교체. (추가된 자산, 빠진 자산) 반환. 추가분은 bootstrap_binance 필요."""
        wanted: dict[str, Asset] = {}
        for s in symbols:
            key = f"bn:{s.symbol}"
            wanted[key] = Asset(
                key=key,
                symbol=s.symbol,
                name=s.symbol,
                source="binance",
                tag=KIND_TAG.get(s.kind, ""),
            )
        added = [a for k, a in wanted.items() if k not in self._bn_assets]
        removed = [a for k, a in self._bn_assets.items() if k not in wanted]
        for a in removed:
            for tf in TIMEFRAMES:
                self._bn_series.pop((a.key, tf), None)
        self._bn_assets = wanted
        return added, removed

    def bootstrap_binance(self, assets: list[Asset], should_stop: StopFn) -> int:
        jobs = [(a, tf, BINANCE_BOOT_LIMIT[tf]) for a in assets for tf in TIMEFRAMES]
        self._log(f"바이낸스 백필 시작: {len(assets)}종목 × {len(TIMEFRAMES)}TF")
        ok = self._run_binance_jobs(jobs, should_stop, replace=True)
        self._log(f"바이낸스 백필 완료: {ok}/{len(jobs)}")
        return ok

    def refresh_binance(self, should_stop: StopFn) -> tuple[int, int]:
        jobs: list[tuple[Asset, str, int]] = []
        retry: list[tuple[Asset, str, int]] = []
        for a in self._bn_assets.values():
            for tf in TIMEFRAMES:
                if (a.key, tf) in self._bn_series:
                    jobs.append((a, tf, BINANCE_REFRESH_LIMIT))
                else:
                    retry.append((a, tf, BINANCE_BOOT_LIMIT[tf]))
        ok = self._run_binance_jobs(jobs, should_stop, replace=False)
        if retry:
            ok += self._run_binance_jobs(retry, should_stop, replace=True)
        return ok, len(jobs) + len(retry)

    def _run_binance_jobs(
        self,
        jobs: list[tuple[Asset, str, int]],
        should_stop: StopFn,
        replace: bool,
    ) -> int:
        ok = 0
        if not jobs:
            return ok
        with ThreadPoolExecutor(max_workers=BINANCE_WORKERS) as pool:
            futs = {
                pool.submit(self.binance.klines, a.symbol, tf, limit): (a, tf)
                for a, tf, limit in jobs
            }
            for fut in as_completed(futs):
                if should_stop():
                    for f in futs:
                        f.cancel()
                    break
                a, tf = futs[fut]
                try:
                    bars = fut.result()
                except Exception:
                    continue
                if a.key not in self._bn_assets:
                    continue
                series = self._bn_series.get((a.key, tf))
                if series is None:
                    series = BarSeries(MAX_BARS)
                    self._bn_series[(a.key, tf)] = series
                if replace:
                    series.replace(bars)
                else:
                    series.merge(bars)
                ok += 1
        return ok

    # ---------- 야후 ----------

    def macro_assets(self) -> list[Asset]:
        return list(self._yf_assets.values())

    def set_macro_assets(self, items: list[MacroAsset]) -> tuple[list[Asset], list[Asset]]:
        wanted: dict[str, Asset] = {}
        for m in items:
            key = f"yf:{m.ticker}"
            wanted[key] = Asset(key=key, symbol=m.ticker, name=m.name, source="yahoo")
        added = [a for k, a in wanted.items() if k not in self._yf_assets]
        removed = [a for k, a in self._yf_assets.items() if k not in wanted]
        for a in removed:
            for iv in ("1h", "1d", "1wk", "1mo"):
                self._yf_series.pop((a.key, iv), None)
        self._yf_assets = wanted
        return added, removed

    def bootstrap_macro(self, assets: list[Asset]) -> int:
        if not assets:
            return 0
        self._log(f"야후 백필 시작: {len(assets)}종목")
        got = self._yahoo_round(assets, yahoo.BOOT_PERIOD, replace=True)
        self._log(f"야후 백필 완료: {got}/{len(assets) * 4}")
        return got

    def refresh_macro(self) -> tuple[int, int]:
        assets = list(self._yf_assets.values())
        got = 0
        for interval in yahoo.REFRESH_PERIOD:
            have = [a for a in assets if (a.key, interval) in self._yf_series]
            missing = [a for a in assets if (a.key, interval) not in self._yf_series]
            got += self._yahoo_round(have, {interval: yahoo.REFRESH_PERIOD[interval]}, replace=False)
            if missing:
                got += self._yahoo_round(missing, {interval: yahoo.BOOT_PERIOD[interval]}, replace=True)
        return got, len(assets) * 4

    def _yahoo_round(self, assets: list[Asset], periods: dict[str, str], replace: bool) -> int:
        by_ticker = {a.symbol: a for a in assets}
        got = 0
        if not by_ticker:
            return got
        for interval, period in periods.items():
            try:
                data = yahoo.download(list(by_ticker), interval, period)
            except Exception as exc:
                self._log(f"야후 {interval} 조회 실패: {exc}")
                continue
            for ticker, bars in data.items():
                a = by_ticker.get(ticker)
                if a is None or a.key not in self._yf_assets or not bars:
                    continue
                key = (a.key, interval)
                series = self._yf_series.get(key)
                if series is None:
                    series = BarSeries(YAHOO_1H_MAX_BARS if interval == "1h" else MAX_BARS)
                    self._yf_series[key] = series
                if replace:
                    series.replace(bars)
                else:
                    series.merge(bars)
                got += 1
        return got
