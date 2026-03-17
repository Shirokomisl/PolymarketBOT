import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets


@dataclass
class OrderBookTop:
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    spread: Optional[float] = None
    tick_size: Optional[float] = None
    ts: Optional[datetime] = None


class PolymarketMarketWSClient:
    def __init__(
        self,
        ws_url: str,
        token_ids: list[str],
        on_update: Callable[[str, str, OrderBookTop, Dict[str, Any]], Awaitable[None]],
        logger,
    ):
        self.ws_url = ws_url
        self.token_ids = token_ids
        self.on_update = on_update
        self.logger = logger
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def _send_ping(self, ws) -> None:
        while not self._stop.is_set():
            try:
                await ws.send("PING")
            except Exception:
                return
            await asyncio.sleep(10)

    async def run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=None) as ws:
                    self.logger.info("Polymarket market WS подключен: %s", self.ws_url)
                    backoff = 1
                    sub = {
                        "type": "market",
                        "assets_ids": self.token_ids,
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(sub))

                    ping_task = asyncio.create_task(self._send_ping(ws))
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        if message == "PONG":
                            continue
                        data = json.loads(message)
                        event_type = data.get("event_type")
                        asset_id = data.get("asset_id") or data.get("assetId")
                        if not asset_id:
                            continue

                        top = OrderBookTop(ts=datetime.now(timezone.utc))
                        if event_type == "best_bid_ask":
                            best_bid = data.get("best_bid")
                            best_ask = data.get("best_ask")
                            top.best_bid = float(best_bid) if best_bid is not None else None
                            top.best_ask = float(best_ask) if best_ask is not None else None
                            top.bid_size = float(data.get("best_bid_size", 0))
                            top.ask_size = float(data.get("best_ask_size", 0))
                            spread = data.get("spread")
                            top.spread = float(spread) if spread is not None else None
                        elif event_type == "book":
                            bids = data.get("bids") or []
                            asks = data.get("asks") or []
                            if bids:
                                top.best_bid = float(bids[0].get("price"))
                                top.bid_size = float(bids[0].get("size"))
                            if asks:
                                top.best_ask = float(asks[0].get("price"))
                                top.ask_size = float(asks[0].get("size"))
                            if top.best_bid is not None and top.best_ask is not None:
                                top.spread = top.best_ask - top.best_bid
                        elif event_type == "price_change":
                            best_bid = data.get("best_bid")
                            best_ask = data.get("best_ask")
                            top.best_bid = float(best_bid) if best_bid is not None else None
                            top.best_ask = float(best_ask) if best_ask is not None else None
                            if top.best_bid is not None and top.best_ask is not None:
                                top.spread = top.best_ask - top.best_bid
                        elif event_type == "tick_size_change":
                            tick_size = data.get("tick_size")
                            top.tick_size = float(tick_size) if tick_size is not None else None
                        else:
                            continue

                        await self.on_update(asset_id, event_type, top, data)

                    ping_task.cancel()
            except Exception as exc:
                self.logger.warning("Polymarket market WS ошибка: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


class PolymarketUserWSClient:
    def __init__(
        self,
        ws_url: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        condition_id: str,
        on_event: Callable[[str, Dict[str, Any]], Awaitable[None]],
        logger,
    ):
        self.ws_url = ws_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.condition_id = condition_id
        self.on_event = on_event
        self.logger = logger
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def _send_ping(self, ws) -> None:
        while not self._stop.is_set():
            try:
                await ws.send("PING")
            except Exception:
                return
            await asyncio.sleep(10)

    async def run(self) -> None:
        if not (self.api_key and self.api_secret and self.api_passphrase):
            self.logger.info("User WS отключен: нет API ключей")
            return

        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=None) as ws:
                    self.logger.info("Polymarket user WS подключен: %s", self.ws_url)
                    backoff = 1
                    sub = {
                        "type": "user",
                        "auth": {
                            "apiKey": self.api_key,
                            "secret": self.api_secret,
                            "passphrase": self.api_passphrase,
                        },
                        "markets": [self.condition_id] if self.condition_id else [],
                    }
                    await ws.send(json.dumps(sub))

                    ping_task = asyncio.create_task(self._send_ping(ws))
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        if message == "PONG":
                            continue
                        data = json.loads(message)
                        event_type = data.get("event_type")
                        if not event_type:
                            continue
                        await self.on_event(event_type, data)

                    ping_task.cancel()
            except Exception as exc:
                self.logger.warning("Polymarket user WS ошибка: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
