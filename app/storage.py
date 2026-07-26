from __future__ import annotations

import sqlite3
from pathlib import Path

from app.models import Candle


class CandleStore:
    """SQLite에 OHLCV 봉을 저장/조회. 재시작 후에도 RSI용 히스토리 유지."""

    def __init__(self, db_path: Path, max_candles: int = 300) -> None:
        self.db_path = db_path
        self.max_candles = max_candles
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                symbol_key TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                closed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol_key, timeframe, open_time)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candles_lookup
            ON candles (symbol_key, timeframe, open_time)
            """
        )
        self._conn.commit()

    def upsert(self, candle: Candle) -> None:
        self._conn.execute(
            """
            INSERT INTO candles (
                symbol_key, timeframe, open_time, open, high, low, close, volume, closed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol_key, timeframe, open_time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                closed=excluded.closed
            """,
            (
                candle.symbol_key,
                candle.timeframe,
                candle.open_time,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                1 if candle.closed else 0,
            ),
        )
        self._conn.commit()
        self._trim(candle.symbol_key, candle.timeframe)

    def upsert_many(self, candles: list[Candle]) -> None:
        if not candles:
            return
        self._conn.executemany(
            """
            INSERT INTO candles (
                symbol_key, timeframe, open_time, open, high, low, close, volume, closed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol_key, timeframe, open_time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                closed=excluded.closed
            """,
            [
                (
                    c.symbol_key,
                    c.timeframe,
                    c.open_time,
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.volume,
                    1 if c.closed else 0,
                )
                for c in candles
            ],
        )
        self._conn.commit()
        seen: set[tuple[str, str]] = set()
        for c in candles:
            key = (c.symbol_key, c.timeframe)
            if key not in seen:
                seen.add(key)
                self._trim(c.symbol_key, c.timeframe)

    def _trim(self, symbol_key: str, timeframe: str) -> None:
        self._conn.execute(
            """
            DELETE FROM candles
            WHERE symbol_key = ? AND timeframe = ?
              AND open_time NOT IN (
                SELECT open_time FROM candles
                WHERE symbol_key = ? AND timeframe = ?
                ORDER BY open_time DESC
                LIMIT ?
              )
            """,
            (symbol_key, timeframe, symbol_key, timeframe, self.max_candles),
        )
        self._conn.commit()

    def get_candles(
        self,
        symbol_key: str,
        timeframe: str,
        limit: int | None = None,
        closed_only: bool = False,
    ) -> list[Candle]:
        limit = limit or self.max_candles
        closed_clause = "AND closed = 1" if closed_only else ""
        rows = self._conn.execute(
            f"""
            SELECT * FROM (
                SELECT * FROM candles
                WHERE symbol_key = ? AND timeframe = ? {closed_clause}
                ORDER BY open_time DESC
                LIMIT ?
            ) ORDER BY open_time ASC
            """,
            (symbol_key, timeframe, limit),
        ).fetchall()
        return [
            Candle(
                symbol_key=r["symbol_key"],
                timeframe=r["timeframe"],
                open_time=r["open_time"],
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                closed=bool(r["closed"]),
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()
