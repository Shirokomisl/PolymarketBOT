import json
from datetime import datetime
from typing import Any, Dict, Optional

import asyncpg


def _json_dumps(value: Any) -> str:
    def _default(obj: Any) -> str:
        if isinstance(obj, datetime):
            return obj.isoformat()
        return str(obj)

    return json.dumps(value, default=_default)


class Database:
    def __init__(self, dsn: str, enabled: bool = True):
        self.dsn = dsn
        self.enabled = enabled
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if not self.enabled:
            return
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        await self._init_schema()

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def _init_schema(self) -> None:
        assert self.pool is not None
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS orderbook_ticks (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                token_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                best_bid NUMERIC,
                best_ask NUMERIC,
                bid_size NUMERIC,
                ask_size NUMERIC,
                spread NUMERIC,
                raw JSONB
            );
            """
        )
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS btc_klines (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                start_ts TIMESTAMPTZ NOT NULL,
                close_ts TIMESTAMPTZ NOT NULL,
                open NUMERIC NOT NULL,
                high NUMERIC NOT NULL,
                low NUMERIC NOT NULL,
                close NUMERIC NOT NULL,
                volume NUMERIC NOT NULL,
                is_closed BOOLEAN NOT NULL,
                raw JSONB
            );
            """
        )
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS order_logs (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                side TEXT NOT NULL,
                price NUMERIC NOT NULL,
                size NUMERIC NOT NULL,
                fee_rate_bps NUMERIC,
                order_id TEXT,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                latency_ms NUMERIC,
                raw JSONB
            );
            """
        )
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_logs (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                order_id TEXT,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                side TEXT NOT NULL,
                price NUMERIC NOT NULL,
                size NUMERIC NOT NULL,
                raw JSONB
            );
            """
        )

    async def insert_orderbook(
        self,
        ts,
        token_id: str,
        event_type: str,
        best_bid: float | None,
        best_ask: float | None,
        bid_size: float | None,
        ask_size: float | None,
        spread: float | None,
        raw: Dict[str, Any],
    ) -> None:
        if not self.enabled or not self.pool:
            return
        await self.pool.execute(
            """
            INSERT INTO orderbook_ticks
            (ts, token_id, event_type, best_bid, best_ask, bid_size, ask_size, spread, raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9);
            """,
            ts,
            token_id,
            event_type,
            best_bid,
            best_ask,
            bid_size,
            ask_size,
            spread,
            _json_dumps(raw),
        )

    async def insert_kline(self, ts, kline: Dict[str, Any]) -> None:
        if not self.enabled or not self.pool:
            return
        await self.pool.execute(
            """
            INSERT INTO btc_klines
            (ts, start_ts, close_ts, open, high, low, close, volume, is_closed, raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10);
            """,
            ts,
            kline["start_ts"],
            kline["close_ts"],
            kline["open"],
            kline["high"],
            kline["low"],
            kline["close"],
            kline["volume"],
            kline["is_closed"],
            _json_dumps(kline),
        )

    async def insert_order_log(self, ts, payload: Dict[str, Any]) -> None:
        if not self.enabled or not self.pool:
            return
        await self.pool.execute(
            """
            INSERT INTO order_logs
            (ts, token_id, outcome, side, price, size, fee_rate_bps, order_id, action, status, latency_ms, raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12);
            """,
            ts,
            payload.get("token_id"),
            payload.get("outcome"),
            payload.get("side"),
            payload.get("price"),
            payload.get("size"),
            payload.get("fee_rate_bps"),
            payload.get("order_id"),
            payload.get("action"),
            payload.get("status"),
            payload.get("latency_ms"),
            _json_dumps(payload),
        )

    async def insert_trade_log(self, ts, payload: Dict[str, Any]) -> None:
        if not self.enabled or not self.pool:
            return
        await self.pool.execute(
            """
            INSERT INTO trade_logs
            (ts, order_id, token_id, outcome, side, price, size, raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8);
            """,
            ts,
            payload.get("order_id"),
            payload.get("token_id"),
            payload.get("outcome"),
            payload.get("side"),
            payload.get("price"),
            payload.get("size"),
            _json_dumps(payload),
        )

    async def fetch_klines(self, start_ts, end_ts):
        if not self.enabled or not self.pool:
            return []
        rows = await self.pool.fetch(
            """
            SELECT * FROM btc_klines
            WHERE ts BETWEEN $1 AND $2
            ORDER BY ts ASC;
            """,
            start_ts,
            end_ts,
        )
        return rows

    async def fetch_orderbook(self, start_ts, end_ts, token_id: str):
        if not self.enabled or not self.pool:
            return []
        rows = await self.pool.fetch(
            """
            SELECT * FROM orderbook_ticks
            WHERE ts BETWEEN $1 AND $2 AND token_id = $3
            ORDER BY ts ASC;
            """,
            start_ts,
            end_ts,
            token_id,
        )
        return rows