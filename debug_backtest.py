import asyncio
from datetime import datetime, timezone
from config import Config
from database import Database

async def debug():
    config = Config.load()
    db = Database(config.db_dsn, enabled=True)
    await db.connect()
    
    # Проверим, сколько свечей в БД
    start = datetime.fromisoformat("2026-03-18T19:36:18.089168+09:00")
    end = datetime.fromisoformat("2026-03-19T12:34:13.476627+09:00")
    
    klines = await db.fetch_klines(start, end)
    print(f"Найдено свечей: {len(klines)}")
    
    if klines:
        print(f"Первая свеча: {klines[0]['ts']}")
        print(f"Последняя свеча: {klines[-1]['ts']}")
        print(f"Всего свечей: {len(klines)}")
        
        # Посмотрим на несколько свечей
        for i, row in enumerate(klines[:5]):
            print(f"\nСвеча {i}:")
            print(f"  open: {row['open']}")
            print(f"  high: {row['high']}")
            print(f"  low: {row['low']}")
            print(f"  close: {row['close']}")
            print(f"  volume: {row['volume']}")
    
    await db.close()

asyncio.run(debug())