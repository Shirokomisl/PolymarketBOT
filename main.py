import asyncio
import contextlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from py_clob_client.order_builder.constants import BUY

from binance_client import BinanceWSClient
from config import Config
from database import Database
from logger import setup_logger
from market_resolver import MarketWindow, resolve_btc_5m_window
from order_manager import OrderManager, OrderSpec
from risk_manager import RiskManager
from websocket_client import OrderBookTop, PolymarketMarketWSClient, PolymarketUserWSClient
from utils import compute_probability, resolve_direction, predict_direction


@dataclass
class MarketState:
    yes_token_id: str = ""
    no_token_id: str = ""
    condition_id: str = ""
    current_slug: str = ""
    orderbooks: Dict[str, OrderBookTop] = field(default_factory=dict)
    tick_sizes: Dict[str, float] = field(default_factory=dict)
    kline: Optional[dict] = None
    active_round_close_ts: Optional[datetime] = None
    last_quote_close_ts: Optional[datetime] = None
    last_desired_prices: Dict[str, float] = field(default_factory=dict)
    
    # Новые поля для истории цен
    price_history: list = field(default_factory=list)  # список цен за последние минуты
    volume_history: list = field(default_factory=list)  # список объёмов
    last_prediction: Optional[str] = None  # последний прогноз (UP/DOWN)
    prediction_confidence: float = 0.0  # уверенность в прогнозе


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
        return state.yes_token_id if outcome == "YES" else state.no_token_id

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

    state = MarketState(
        yes_token_id=config.yes_token_id,
        no_token_id=config.no_token_id,
        condition_id=config.condition_id,
    )

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
        await update_price_history(kline)  # добавить эту строку
        await db.insert_kline(kline["event_ts"], kline)
            
    async def update_price_history(kline: dict) -> None:
        """Обновляет историю цен и объёмов при получении новой свечи"""
        state.price_history.append({
            'close': kline['close'],
            'high': kline['high'],
            'low': kline['low'],
            'volume': kline['volume'],
            'ts': kline['close_ts']
        })
        state.volume_history.append(kline['volume'])
        
        # Оставляем только последние N записей (настраивается в конфиге)
        max_history = config.prediction_lookback_minutes + 2
        if len(state.price_history) > max_history:
            state.price_history = state.price_history[-max_history:]
        if len(state.volume_history) > max_history:
            state.volume_history = state.volume_history[-max_history:]

    async def on_user_event(event_type: str, data: dict) -> None:
        if event_type != "trade":
            return
        asset_id = data.get("asset_id")
        outcome = data.get("outcome")
        if outcome is None:
            if asset_id == state.yes_token_id:
                outcome = "YES"
            elif asset_id == state.no_token_id:
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

    market_ws: Optional[PolymarketMarketWSClient] = None
    market_task: Optional[asyncio.Task] = None
    user_ws: Optional[PolymarketUserWSClient] = None
    user_task: Optional[asyncio.Task] = None

    async def start_market_ws() -> None:
        nonlocal market_ws, market_task
        market_ws = PolymarketMarketWSClient(
            config.poly_ws_url,
            [state.yes_token_id, state.no_token_id],
            on_orderbook_update,
            logger,
        )
        market_task = asyncio.create_task(market_ws.run())

    async def restart_market_ws() -> None:
        nonlocal market_ws, market_task
        if market_ws:
            await market_ws.stop()
        if market_task:
            market_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await market_task
        await start_market_ws()

    async def start_user_ws() -> None:
        nonlocal user_ws, user_task
        user_ws = PolymarketUserWSClient(
            config.poly_user_ws_url,
            config.poly_api_key,
            config.poly_api_secret,
            config.poly_api_passphrase,
            state.condition_id,
            on_user_event,
            logger,
        )
        user_task = asyncio.create_task(user_ws.run())

    async def restart_user_ws() -> None:
        nonlocal user_ws, user_task
        if user_ws:
            await user_ws.stop()
        if user_task:
            user_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await user_task
        await start_user_ws()

    binance_ws = BinanceWSClient(config.binance_ws_url, on_kline_update, logger)

    async def apply_market_window(window: MarketWindow) -> None:
        if (
            window.yes_token_id == state.yes_token_id
            and window.no_token_id == state.no_token_id
            and window.condition_id == state.condition_id
        ):
            return

        logger.info(
            "Market change: %s yes=%s no=%s",
            window.slug,
            window.yes_token_id,
            window.no_token_id,
        )

        # Отменяем старые ордера
        all_ids = []
        for ids in order_manager.open_order_ids.values():
            all_ids.extend(ids)
        if all_ids:
            await order_manager.cancel_orders(all_ids)
        order_manager.open_order_ids = {}

        state.yes_token_id = window.yes_token_id
        state.no_token_id = window.no_token_id
        state.condition_id = window.condition_id
        state.current_slug = window.slug
        state.orderbooks.clear()
        state.tick_sizes.clear()
        state.last_desired_prices.clear()
        state.last_quote_close_ts = None

        await restart_market_ws()
        if config.poly_api_key and config.poly_api_secret and config.poly_api_passphrase:
            await restart_user_ws()

    async def auto_rotate_loop() -> None:
        if not config.auto_rotate_market:
            return
        last_slug = ""
        while True:
            await asyncio.sleep(config.rotate_check_sec)
            try:
                window = await resolve_btc_5m_window(
                    prefix=config.btc_5m_prefix,
                    market_contains=config.market_contains,
                )
            except Exception as exc:
                logger.warning("Auto-rotate error: %s", exc)
                continue
            if not window:
                continue
            if window.slug != last_slug:
                last_slug = window.slug
                await apply_market_window(window)

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(config.heartbeat_sec)
            now = datetime.now(timezone.utc)
            kline = state.kline
            time_to_close = None
            if kline:
                time_to_close = (kline["close_ts"] - now).total_seconds()
            yes_top = state.orderbooks.get(state.yes_token_id)
            no_top = state.orderbooks.get(state.no_token_id)

            logger.info(
                "Heartbeat: slug=%s yes=%s bid/ask=%s/%s no=%s bid/ask=%s/%s ttc=%s",
                state.current_slug or "-",
                state.yes_token_id or "-",
                f"{yes_top.best_bid:.3f}" if yes_top and yes_top.best_bid is not None else "-",
                f"{yes_top.best_ask:.3f}" if yes_top and yes_top.best_ask is not None else "-",
                state.no_token_id or "-",
                f"{no_top.best_bid:.3f}" if no_top and no_top.best_bid is not None else "-",
                f"{no_top.best_ask:.3f}" if no_top and no_top.best_ask is not None else "-",
                f"{time_to_close:.1f}s" if time_to_close is not None else "-",
            )

    async def strategy_loop() -> None:
        while True:
            await asyncio.sleep(config.requote_interval_ms / 1000)
            
            # 1. Проверяем, есть ли данные
            if state.kline is None or len(state.price_history) < 2:
                continue
            
            now = datetime.now(timezone.utc)
            close_ts = state.kline["close_ts"]
            time_to_close = (close_ts - now).total_seconds()
            
            # 2. Обновляем прогноз каждые 30 секунд (не так часто, как requote)
            if int(now.timestamp()) % 30 == 0:  # примерно каждые 30 секунд
                direction, confidence = predict_direction(
                    state.price_history,
                    state.volume_history,
                    config
                )
                if direction:
                    state.last_prediction = direction
                    state.prediction_confidence = confidence
                    logger.debug(f"Prediction: {direction} with {confidence:.2f} confidence")
            
            # 3. Проверяем stop-loss (оставляем как есть)
            current_btc = state.kline["close"]
            if risk.check_stop_loss(current_btc):
                logger.warning("Stop-loss: canceling orders")
                all_ids = []
                for ids in order_manager.open_order_ids.values():
                    all_ids.extend(ids)
                if all_ids:
                    await order_manager.cancel_orders(all_ids)
                order_manager.open_order_ids = {}
                continue
            
            # 4. Ждём окна T-10 (оставляем как есть)
            if time_to_close > config.t_minus_seconds:
                risk.set_reserved(0.0)
                continue
            
            # 5. В окне T-10 проверяем прогноз
            if time_to_close <= config.t_minus_seconds:
                if state.yes_token_id not in state.orderbooks or state.no_token_id not in state.orderbooks:
                    continue
                
                # Если нет прогноза или уверенность太低 - пропускаем
                if state.last_prediction is None or state.prediction_confidence < config.prob_threshold:
                    continue
                
                direction = state.last_prediction
                prob = state.prediction_confidence
                
                # Создаём ордера
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
                        "T-%.0fs, prediction=%s confidence=%.2f -> place orders",
                        time_to_close,
                        direction,
                        prob,
                    )
                    await order_manager.cancel_and_replace(list(specs))
                    state.last_quote_close_ts = close_ts
                    state.last_desired_prices = desired_prices
                elif should_requote:
                    logger.info("Requote in window T-10, update prices")
                    await order_manager.cancel_and_replace(list(specs))
                    state.last_desired_prices = desired_prices

    await start_market_ws()
    if config.poly_api_key and config.poly_api_secret and config.poly_api_passphrase:
        await start_user_ws()

    tasks = [
        asyncio.create_task(binance_ws.run()),
        asyncio.create_task(strategy_loop()),
    ]
    if config.heartbeat_sec > 0:
        tasks.append(asyncio.create_task(heartbeat_loop()))
    if config.auto_rotate_market:
        tasks.append(asyncio.create_task(auto_rotate_loop()))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
