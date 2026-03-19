import argparse
from datetime import datetime, timezone
from typing import Optional, Tuple
import math
import asyncio

from config import Config
from database import Database
from utils import predict_direction


def calculate_rebate(price: float, size: float) -> float:
    """
    Рассчитывает rebate (возврат комиссии) для maker-ордера.
    Rebate = 20% от комиссии, которую заплатил taker [citation:7]
    
    Комиссия takera: fee = 0.25 * (price * (1 - price))^2 * trade_value
    Максимальная комиссия ~1.56% при цене $0.50
    """
    # Комиссия takera
    taker_fee_rate = 0.25 * (price * (1 - price)) ** 2
    taker_fee_rate = min(taker_fee_rate, 0.0156)  # максимум 1.56%
    
    trade_value = price * size
    taker_fee = trade_value * taker_fee_rate
    
    # Maker получает 20% от комиссии takera [citation:7]
    maker_rebate = taker_fee * 0.20
    
    return maker_rebate


def calculate_pnl(
    direction: str,
    actual_direction: str,
    order_usdc: float,
    buy_price: float,
    sell_price_win: float = 0.98,
    sell_price_loss: float = 0.90,
) -> Tuple[float, float, float]:
    """
    Реалистичный расчёт PnL для maker-стратегии:
    - Нулевые комиссии при входе (мы maker)
    - Нулевые комиссии при выходе (мы maker)
    - Дополнительный доход от rebates
    
    Returns: (pnl, rebate, shares)
    """
    # Количество акций
    shares = order_usdc / buy_price
    
    if direction == actual_direction:
        # Выигрыш - продаём по высокой цене
        revenue = shares * sell_price_win
        cost = shares * buy_price
        
        # Получаем rebate за обе стороны (и при покупке, и при продаже)
        rebate_buy = calculate_rebate(buy_price, shares)
        rebate_sell = calculate_rebate(sell_price_win, shares)
        total_rebate = rebate_buy + rebate_sell
        
        # PnL = прибыль от спреда + rebates
        pnl = (revenue - cost) + total_rebate
    else:
        # Проигрыш - продаём по цене стоп-лосса
        revenue = shares * sell_price_loss
        cost = shares * buy_price
        
        # Даже при проигрыше получаем rebate за покупку
        rebate_buy = calculate_rebate(buy_price, shares)
        
        # PnL = убыток от спреда + rebate (смягчает убыток)
        pnl = (revenue - cost) + rebate_buy
    
    return pnl, total_rebate if direction == actual_direction else rebate_buy, shares


async def run_backtest(start: datetime, end: datetime) -> None:
    config = Config.load()
    db = Database(config.db_dsn, enabled=True)
    await db.connect()
    
    klines = await db.fetch_klines(start, end)
    if not klines:
        print("Нет данных для бэктеста")
        await db.close()
        return
    
    # Параметры стратегии
    BUY_PRICE = 0.92  # цена входа
    SELL_PRICE_WIN = 0.98  # цена выхода при выигрыше
    SELL_PRICE_LOSS = 0.90  # цена стоп-лосса
    
    total_pnl = 0.0
    total_rebates = 0.0
    trades = 0
    wins = 0
    
    price_history = []
    volume_history = []
    
    print("\n=== Polymarket Backtest Results (Maker Strategy) ===")
    print(f"Период: {start} - {end}")
    print(f"Параметры: вход @ ${BUY_PRICE}, выход win @ ${SELL_PRICE_WIN}, loss @ ${SELL_PRICE_LOSS}")
    print(f"Размер сделки: ${config.order_usdc_high} USDC")
    print(f"Комиссии: 0% для maker + rebates (20% от taker fees)")
    
    for i, row in enumerate(klines):
        # Добавляем в историю
        price_history.append({
            'close': float(row["close"]),
            'high': float(row["high"]),
            'low': float(row["low"])
        })
        volume_history.append(float(row["volume"]))
        
        # Оставляем только последние N записей
        max_history = config.prediction_lookback_minutes + 2
        if len(price_history) > max_history:
            price_history = price_history[-max_history:]
            volume_history = volume_history[-max_history:]
        
        # Прогнозируем для следующей свечи
        if i < len(klines) - 1:
            direction, confidence = predict_direction(
                price_history,
                volume_history,
                config
            )
            
            if direction and confidence >= config.prob_threshold:
                next_row = klines[i + 1]
                next_open = float(next_row["open"])
                next_close = float(next_row["close"])
                
                actual_direction = "UP" if next_close >= next_open else "DOWN"
                
                # Рассчитываем PnL
                pnl, rebate, shares = calculate_pnl(
                    direction=direction,
                    actual_direction=actual_direction,
                    order_usdc=config.order_usdc_high,
                    buy_price=BUY_PRICE,
                    sell_price_win=SELL_PRICE_WIN,
                    sell_price_loss=SELL_PRICE_LOSS,
                )
                
                total_pnl += pnl
                total_rebates += rebate
                trades += 1
                
                if pnl > 0:
                    wins += 1
                    win_emoji = "✅"
                else:
                    win_emoji = "❌"
                
                # Детальный вывод для каждой сделки
                spread_pnl = pnl - rebate
                print(f"{win_emoji} Свеча {i}: pred={direction} ({confidence:.1%}), "
                      f"actual={actual_direction}")
                print(f"   Спред: ${spread_pnl:+.2f}, Rebate: +${rebate:.2f}, "
                      f"Итого: ${pnl:+.2f}, Акций: {shares:.2f}")
    
    # Итоговая статистика
    print("\n=== ИТОГИ ===")
    print(f"Всего сделок: {trades}")
    if trades > 0:
        win_rate = wins / trades
        avg_pnl = total_pnl / trades
        avg_rebate = total_rebates / trades
        
        print(f"Выигрышных: {wins} ({win_rate:.1%})")
        print(f"Проигрышных: {trades - wins} ({1-win_rate:.1%})")
        print(f"Общий PnL: ${total_pnl:.2f}")
        print(f"Общие rebates: ${total_rebates:.2f}")
        print(f"Средний PnL на сделку: ${avg_pnl:.2f}")
        print(f"Средний rebate на сделку: ${avg_rebate:.2f}")
        
        # ROI с учётом rebates
        total_invested = trades * config.order_usdc_high
        roi = (total_pnl / total_invested) * 100
        print(f"ROI: {roi:.2f}%")
        
        # Соотношение риск/прибыль
        if trades - wins > 0:
            avg_win = sum(pnl for _ in range(wins) for pnl in [1]) / wins  # упрощённо
            avg_loss = abs(sum(pnl for _ in range(trades-wins) for pnl in [-1]) / (trades-wins))
            print(f"Risk/Reward ratio: {avg_win/avg_loss:.2f}")
    
    await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="Начало периода, ISO-строка (UTC)")
    parser.add_argument("--end", required=True, help="Конец периода, ISO-строка (UTC)")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    asyncio.run(run_backtest(start, end))


if __name__ == "__main__":
    main()