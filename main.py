import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from py_clob_client.order_builder.constants import BUY

from binance_client import BinanceWSClient
from config import Config
from database import Database
from logger import setup_logger
from order_manager import OrderManager, OrderSpec
from risk_manager import RiskManager
from websocket_client import OrderBookTop, PolymarketMarketWSClient, PolymarketUserWSClient


@dataclass
class MarketState:
    orderbooks: Dict[str, OrderBookTop] = field(default_factory=dict)
    tick_sizes: Dict[str, float] = field(default_factory=dict)
    kline: Optional[dict] = None
    active_round_close_ts: Optional[datetime] = None
    last_quote_close_ts: Optional[datetime] = None
    last_desired_prices: Dict[str, float] = field(default_factory=dict)


def compute_probability(open_price: float, high: float, low: float, close: float, scale: float) -> float:
    vol = max(high - low, 1e-9)
    move = abs(close - open_price)
    ratio = min(move / vol, 1.0)
    return 0.5 + 0.5 * math.tanh(ratio * scale)


def resolve_direction(open_price: float, close: float) -> str:
    return "UP" if close >= open_price else "DOWN"


def clamp(value: float, lo: float, hi: float) -> float:
    return max(min(value, hi), lo)


def adjust_buy_price(target: float, top: OrderBookTop, tick_size: float) -> float:
    if top.best_ask is None:
        return target
    return max(min(target, top.best_ask - tick_size), tick_size)


def make_order_specs(
    config: Config,
    state: MarketState,
    direction: str,
) -> Tuple[OrderSpec, OrderSpec]:
    high_price = clamp(config.high_price_target, config.high_price_min, config.high_price_max)
    other_price = config.other_side_price

    if config.yes_is_up:
        high_outcome = "YES" if direction == "UP" else "NO"
    else:
        high_outcome = "NO" if direction == "UP" else "YES"

    def outcome_token(outcome: str) -> str:
        return config.yes_token_id if outcome == "YES" else config.no_token_id

    def top_for(outcome: str) -> OrderBookTop:
        return state.orderbooks.get(outcome_token(outcome), OrderBookTop())

    def tick_for(outcome: str) -> float:
        return state.tick_sizes.get(outcome_token(outcome), 0.01)

    def build(outcome: str, target_price: float, usdc_amount: float) -> OrderSpec:
        top = top_for(outcome)
        tick_size = tick_for(outcome)
        price = adjust_buy_price(target_price, top, tick_size)
        size = usdc_amount / max(price, 1e-9)
        return OrderSpec(
            token_id=outcome_token(outcome),
            outcome=outcome,
            side=BUY,
            price=round(price, 4),
            size=round(size, 6),
        )

    high_spec = build(high_outcome, high_price, config.order_usdc_high)
    other_outcome = "NO" if high_outcome == "YES" else "YES"
    other_spec = build(other_outcome, other_price, config.order_usdc_other)

    return high_spec, other_spec


async def main() -> None:
    config = Config.load()
    logger = setup_logger("polymarket_bot", config.log_level, config.log_file)

    if not (config.yes_token_id and config.no_token_id):
        logger.error("Не заданы YES_TOKEN_ID/NO_TOKEN_ID")
        return
    if not config.dry_run and not config.poly_private_key:
        logger.error("Не задан POLY_PRIVATE_KEY для реальной торговли")
        return

    db = Database(config.db_dsn, enabled=config.db_write)
    await db.connect()

    risk = RiskManager(config, logger)
    order_manager = OrderManager(config, logger, db=db)

    state = MarketState()

    async def on_orderbook_update(token_id: str, event_type: str, top: OrderBookTop, raw: dict) -> None:
        if token_id not in state.orderbooks:
            state.orderbooks[token_id] = OrderBookTop()

        current = state.orderbooks[token_id]
        if top.best_bid is not None:
            current.best_bid = top.best_bid
        if top.best_ask is not None:
            current.best_ask = top.best_ask
        if top.bid_size is not None:
            current.bid_size = top.bid_size
        if top.ask_size is not None:
            current.ask_size = top.ask_size
        if top.spread is not None:
            current.spread = top.spread
        if top.tick_size is not None:
            state.tick_sizes[token_id] = top.tick_size
        current.ts = top.ts

        await db.insert_orderbook(
            ts=top.ts or datetime.now(timezone.utc),
            token_id=token_id,
            event_type=event_type,
            best_bid=current.best_bid,
            best_ask=current.best_ask,
            bid_size=current.bid_size,
            ask_size=current.ask_size,
            spread=current.spread,
            raw=raw,
        )

    async def on_kline_update(kline: dict) -> None:
        state.kline = kline
        await db.insert_kline(kline["event_ts"], kline)

    async def on_user_event(event_type: str, data: dict) -> None:
        if event_type != "trade":
            return
        asset_id = data.get("asset_id")
        outcome = data.get("outcome")
        if outcome is None:
            if asset_id == config.yes_token_id:
                outcome = "YES"
            elif asset_id == config.no_token_id:
                outcome = "NO"
            else:
                return

        side = str(data.get("side", "")).upper()
        if side not in ("BUY", "SELL"):
            return
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))

        await risk.on_trade(outcome=outcome, side=side, price=price, size=size)
        await db.insert_trade_log(
            ts=datetime.now(timezone.utc),
            payload={
                "order_id": data.get("taker_order_id"),
                "token_id": asset_id,
                "outcome": outcome,
                "side": side,
                "price": price,
                "size": size,
                "raw": data,
            },
        )

    market_ws = PolymarketMarketWSClient(
        config.poly_ws_url,
        [config.yes_token_id, config.no_token_id],
        on_orderbook_update,
        logger,
    )
    user_ws = PolymarketUserWSClient(
        config.poly_user_ws_url,
        config.poly_api_key,
        config.poly_api_secret,
        config.poly_api_passphrase,
        config.condition_id,
        on_user_event,
        logger,
    )
    binance_ws = BinanceWSClient(config.binance_ws_url, on_kline_update, logger)

    async def strategy_loop() -> None:
        while True:
            await asyncio.sleep(config.requote_interval_ms / 1000)
            if state.kline is None:
                continue

            now = datetime.now(timezone.utc)
            close_ts = state.kline["close_ts"]
            time_to_close = (close_ts - now).total_seconds()
            if time_to_close < 0:
                continue

            # Проверка stop-loss по цене BTC
            current_btc = state.kline["close"]
            if risk.check_stop_loss(current_btc):
                logger.warning("Stop-loss: отмена ордеров")
                all_ids = []
                for ids in order_manager.open_order_ids.values():
                    all_ids.extend(ids)
                if all_ids:
                    await order_manager.cancel_orders(all_ids)
                order_manager.open_order_ids = {}
                continue

            if time_to_close > config.t_minus_seconds:
                risk.set_reserved(0.0)
                continue

            if time_to_close <= config.t_minus_seconds:
                if config.yes_token_id not in state.orderbooks or config.no_token_id not in state.orderbooks:
                    continue
                direction = resolve_direction(state.kline["open"], state.kline["close"])
                prob = compute_probability(
                    state.kline["open"],
                    state.kline["high"],
                    state.kline["low"],
                    state.kline["close"],
                    config.prob_scale,
                )

                if prob < config.prob_threshold:
                    continue

                specs = make_order_specs(config, state, direction)
                total_usdc = 0.0
                for spec in specs:
                    total_usdc += spec.price * spec.size

                if not (risk.can_place_order(config.order_usdc_high) and risk.can_place_order(config.order_usdc_other)):
                    continue

                risk.set_reserved(total_usdc)
                risk.update_signal(direction, current_btc)

                desired_prices = {spec.token_id: spec.price for spec in specs}
                should_quote = state.last_quote_close_ts != close_ts
                should_requote = desired_prices != state.last_desired_prices

                if should_quote:
                    logger.info(
                        "T-%.0fs, prob=%.2f direction=%s -> размещаем ордера",
                        time_to_close,
                        prob,
                        direction,
                    )
                    await order_manager.cancel_and_replace(list(specs))
                    state.last_quote_close_ts = close_ts
                    state.last_desired_prices = desired_prices
                elif should_requote:
                    logger.info("Requote в окне T-10, обновляем цены")
                    await order_manager.cancel_and_replace(list(specs))
                    state.last_desired_prices = desired_prices

    await asyncio.gather(
        market_ws.run(),
        user_ws.run(),
        binance_ws.run(),
        strategy_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
