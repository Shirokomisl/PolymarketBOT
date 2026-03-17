import asyncio
import json
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict

import websockets


class BinanceWSClient:
    def __init__(
        self,
        ws_url: str,
        on_kline: Callable[[Dict], Awaitable[None]],
        logger,
    ):
        self.ws_url = ws_url
        self.on_kline = on_kline
        self.logger = logger
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    self.logger.info("Binance WS подключен: %s", self.ws_url)
                    backoff = 1
                    async for message in ws:
                        if self._stop.is_set():
                            break
                        data = json.loads(message)
                        if data.get("e") != "kline":
                            continue
                        k = data.get("k", {})
                        kline = {
                            "start_ts": datetime.fromtimestamp(k.get("t", 0) / 1000, tz=timezone.utc),
                            "close_ts": datetime.fromtimestamp(k.get("T", 0) / 1000, tz=timezone.utc),
                            "open": float(k.get("o", 0)),
                            "high": float(k.get("h", 0)),
                            "low": float(k.get("l", 0)),
                            "close": float(k.get("c", 0)),
                            "volume": float(k.get("v", 0)),
                            "is_closed": bool(k.get("x", False)),
                            "event_ts": datetime.fromtimestamp(data.get("E", 0) / 1000, tz=timezone.utc),
                            "raw": data,
                        }
                        await self.on_kline(kline)
            except Exception as exc:
                self.logger.warning("Binance WS ошибка: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)