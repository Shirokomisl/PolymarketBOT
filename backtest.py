import argparse
from datetime import datetime, timezone

import asyncio

from config import Config
from database import Database


def compute_probability(open_price: float, high: float, low: float, close: float, scale: float) -> float:
    vol = max(high - low, 1e-9)
    move = abs(close - open_price)
    ratio = min(move / vol, 1.0)
    # Преобразуем в "уверенность" от 0.5 до ~1.0
    import math

    return 0.5 + 0.5 * math.tanh(ratio * scale)


def resolve_direction(open_price: float, close: float) -> str:
    return "UP" if close >= open_price else "DOWN"


def pnl_for_buy(price: float, outcome_hit: bool) -> float:
    return (1 - price) if outcome_hit else -price


async def run_backtest(start: datetime, end: datetime) -> None:
    config = Config.load()
    db = Database(config.db_dsn, enabled=True)
    await db.connect()

    klines = await db.fetch_klines(start, end)
    if not klines:
        print("Нет данных для бэктеста")
        await db.close()
        return

    total_pnl = 0.0
    trades = 0

    for row in klines:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        prob = compute_probability(open_price, high, low, close, config.prob_scale)
        if prob < config.prob_threshold:
            continue

        direction = resolve_direction(open_price, close)
        high_price = max(min(config.high_price_target, config.high_price_max), config.high_price_min)
        other_price = config.other_side_price

        # Предполагаем buy на обе стороны
        yes_hit = direction == "UP" if config.yes_is_up else direction == "DOWN"
        no_hit = not yes_hit

        pnl_yes = pnl_for_buy(high_price if yes_hit else other_price, yes_hit)
        pnl_no = pnl_for_buy(other_price if yes_hit else high_price, no_hit)

        trade_pnl = pnl_yes + pnl_no
        total_pnl += trade_pnl
        trades += 1

    print(f"Сделок: {trades}")
    print(f"PnL (без комиссий): {total_pnl:.4f}")

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
