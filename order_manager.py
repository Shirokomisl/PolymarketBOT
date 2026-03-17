import asyncio
from datetime import datetime, time, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY, SELL


@dataclass
class OrderSpec:
    token_id: str
    outcome: str
    side: str
    price: float
    size: float


class OrderManager:
    def __init__(self, config, logger, db=None):
        self.config = config
        self.logger = logger
        self.db = db
        self.client = ClobClient(
            host=self.config.poly_clob_host,
            key=self.config.poly_private_key,
            chain_id=self.config.poly_chain_id,
            signature_type=self.config.poly_signature_type,
            funder=self.config.poly_funder or None,
        )
        creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(creds)
        self.open_order_ids: Dict[str, List[str]] = {}
        self._http: Optional[aiohttp.ClientSession] = None

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.closed:
            await self._http.close()

    async def fetch_fee_rate_bps(self, token_id: str) -> float:
        # Явно запрашиваем feeRateBps перед каждым ордером
        url = f"{self.config.poly_clob_host}/fee-rate?token_id={token_id}"
        http = await self._get_http()
        async with http.get(url) as resp:
            data = await resp.json()
            fee_rate = float(data.get("base_fee", 0))

        # Синхронизируем с внутренним кэшем клиента, чтобы create_order не спорил
        try:
            self.client._ClobClient__fee_rates[token_id] = fee_rate
        except Exception:
            pass
        return fee_rate

    async def _create_signed_order(self, spec: OrderSpec) -> Dict[str, Any]:
        fee_rate_bps = await self.fetch_fee_rate_bps(spec.token_id)
        order_args = OrderArgs(
            token_id=spec.token_id,
            price=spec.price,
            size=spec.size,
            side=spec.side,
            fee_rate_bps=fee_rate_bps,
        )
        signed = await asyncio.to_thread(self.client.create_order, order_args)
        return {"signed": signed, "fee_rate_bps": fee_rate_bps}

    async def cancel_orders(self, order_ids: List[str]) -> None:
        if not order_ids:
            return
        if self.config.dry_run:
            self.logger.info("DRY_RUN отмена ордеров: %s", order_ids)
            return
        await asyncio.to_thread(self.client.cancel_orders, order_ids)

    async def post_orders(self, specs: List[OrderSpec], post_only: bool = True) -> Dict[str, Any]:
        signed_orders = []
        fee_rates: Dict[str, float] = {}
        for spec in specs:
            if self.config.dry_run:
                fee_rates[spec.token_id] = await self.fetch_fee_rate_bps(spec.token_id)
            else:
                signed_info = await self._create_signed_order(spec)
                signed_orders.append(signed_info["signed"])
                fee_rates[spec.token_id] = signed_info["fee_rate_bps"]

        if self.config.dry_run:
            self.logger.info("DRY_RUN размещение ордеров: %s", specs)
            result = {"orders": [], "dry_run": True}
        else:
            post_args = [
                PostOrdersArgs(order=o, orderType=OrderType.GTC, postOnly=post_only)
                for o in signed_orders
            ]
            result = await asyncio.to_thread(self.client.post_orders, post_args)

        # Запись в БД
        if self.db:
            now = datetime.now(timezone.utc)
            for spec in specs:
                await self.db.insert_order_log(
                    ts=now,
                    payload={
                        "token_id": spec.token_id,
                        "outcome": spec.outcome,
                        "side": spec.side,
                        "price": spec.price,
                        "size": spec.size,
                        "fee_rate_bps": fee_rates.get(spec.token_id),
                        "action": "post",
                        "status": "sent",
                        "raw": result,
                    },
                )

        return result

    async def cancel_and_replace(self, specs: List[OrderSpec]) -> Dict[str, Any]:
        start = time.perf_counter()

        # Собираем все открытые ордера
        existing = []
        for ids in self.open_order_ids.values():
            existing.extend(ids)

        if existing:
            await self.cancel_orders(existing)

        result = await self.post_orders(specs, post_only=True)

        # Пытаемся извлечь order_id из ответа
        order_ids = []
        if isinstance(result, dict):
            for item in result.get("orders", []) or []:
                oid = item.get("orderID") or item.get("order_id")
                if oid:
                    order_ids.append(oid)

        # Обновляем локальное состояние
        self.open_order_ids = {}
        for spec in specs:
            self.open_order_ids.setdefault(spec.token_id, []).extend(order_ids)

        latency_ms = (time.perf_counter() - start) * 1000
        if latency_ms > self.config.replace_target_ms:
            self.logger.warning(
                "Cancel/replace медленнее цели: %.1fms (target=%sms)",
                latency_ms,
                self.config.replace_target_ms,
            )
        else:
            self.logger.info("Cancel/replace %.1fms", latency_ms)

        if self.db:
            now = datetime.now(timezone.utc)
            for spec in specs:
                await self.db.insert_order_log(
                    ts=now,
                    payload={
                        "token_id": spec.token_id,
                        "outcome": spec.outcome,
                        "side": spec.side,
                        "price": spec.price,
                        "size": spec.size,
                        "action": "cancel_replace",
                        "status": "sent",
                        "latency_ms": latency_ms,
                        "raw": result,
                    },
                )

        return result

    async def shutdown(self) -> None:
        await self.close()
