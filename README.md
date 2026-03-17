**Overview**
Этот проект — каркас торгового бота для Polymarket с maker‑стратегией и WebSocket‑данными ордербука, подключением Binance WS для BTC 5‑минутных рынков, логированием и хранением истории в PostgreSQL.

**Requirements**
- Python 3.10+
- PostgreSQL 13+ (или Docker)
- Полные доступы к Polymarket CLOB (API ключи и приватный ключ)

**Quick Start (PowerShell)**
1. Установка зависимостей:
```powershell
pip install -r requirements.txt
```
2. Создание `.env`:
```powershell
Copy-Item .env.example .env
```
3. Заполнить переменные окружения в `.env`.
4. Запуск:
```powershell
python main.py
```

**Quick Start (bash)**
1. Установка зависимостей:
```bash
pip install -r requirements.txt
```
2. Создание `.env`:
```bash
cp .env.example .env
```
3. Заполнить переменные окружения в `.env`.
4. Запуск:
```bash
python main.py
```

**Environment Variables**
Ниже список ключевых переменных и их смысл (все задаются в `.env`).
- `POLY_CLOB_HOST`: базовый URL CLOB.
- `POLY_WS_URL`: WS market channel (ордербук).
- `POLY_USER_WS_URL`: WS user channel (ордера/сделки).
- `POLY_API_KEY`: API‑ключ Polymarket для user channel.
- `POLY_API_SECRET`: секрет API‑ключа.
- `POLY_API_PASSPHRASE`: passphrase API‑ключа.
- `POLY_PRIVATE_KEY`: приватный ключ кошелька (нужен для подписи ордеров).
- `POLY_FUNDER`: адрес funder (если используете отдельный кошелёк‑спонсор).
- `POLY_SIGNATURE_TYPE`: тип подписи (обычно `0`).
- `POLY_CHAIN_ID`: chain id сети (Polygon = `137`).
- `YES_TOKEN_ID`: token_id для YES.
- `NO_TOKEN_ID`: token_id для NO.
- `CONDITION_ID`: condition_id рынка (нужно для user channel и merge).
- `YES_IS_UP`: `true`, если YES соответствует направлению BTC UP.
- `HIGH_PRICE_TARGET`: целевая цена для вероятной стороны.
- `HIGH_PRICE_MIN`: минимум диапазона (по умолчанию 0.90).
- `HIGH_PRICE_MAX`: максимум диапазона (по умолчанию 0.95).
- `OTHER_SIDE_PRICE`: цена для второй стороны (для rebate).
- `ORDER_USDC_HIGH`: объём USDC на вероятной стороне.
- `ORDER_USDC_OTHER`: объём USDC на второй стороне.
- `PROB_THRESHOLD`: порог уверенности (по умолчанию 0.85).
- `PROB_SCALE`: масштаб в расчёте уверенности.
- `T_MINUS_SECONDS`: T‑10 окно до закрытия (секунды).
- `REQUOTE_INTERVAL_MS`: частота пересмотра цен.
- `REPLACE_TARGET_MS`: целевой SLA на cancel/replace.
- `DRY_RUN`: если `true`, ордера не отправляются, только логируются.
- `CAPITAL_USDC`: размер капитала для риск‑лимитов.
- `MAX_POSITION_PCT`: лимит на одну сделку (доля капитала).
- `STOP_LOSS_PCT`: стоп‑лосс по движению BTC против сигнала.
- `AUTO_MERGE`: авто‑объединение YES/NO (on‑chain).
- `MERGE_MIN_SHARES`: минимальное количество для merge.
- `BINANCE_SYMBOL`: символ Binance (например `btcusdt`).
- `BINANCE_WS_URL`: прямой WS поток Binance.
- `DB_DSN`: строка подключения к PostgreSQL.
- `DB_WRITE`: включение записи истории в БД.
- `LOG_LEVEL`: уровень логирования.
- `LOG_FILE`: файл логов.
- `POLYGON_RPC_URL`: RPC для Polygon (нужно для merge).
- `CTF_CONTRACT_ADDRESS`: адрес CTF контракта.
- `USDC_CONTRACT_ADDRESS`: адрес USDC.e.

**Polymarket Credentials**
- Для реальной торговли `DRY_RUN=false` и обязательно заполненный `POLY_PRIVATE_KEY`.
- Для user channel нужны `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_API_PASSPHRASE`.

**Database Setup**
Вариант 1: локальная PostgreSQL.
1. Создать пользователя и базу.
2. Обновить `DB_DSN` в `.env`.
3. При запуске таблицы будут созданы автоматически.

Вариант 2: Docker Compose.
```bash
docker compose up --build
```

**Strategy Logic (вкратце)**
- Бот слушает ордербук Polymarket и поток Binance `@kline_5m`.
- За `T_MINUS_SECONDS` до закрытия 5‑минутного окна оценивается «уверенность» направления.
- При уверенности ≥ `PROB_THRESHOLD` выставляются maker‑ордера на YES и NO.
- Cancel/replace выполняется в окне T‑10 и логирует фактическую задержку.

**Backtesting**
1. Сначала соберите данные, дав боту поработать с `DB_WRITE=true`.
2. Запустите бэктест:
```bash
python backtest.py --start 2026-03-01T00:00:00+00:00 --end 2026-03-02T00:00:00+00:00
```

**Dry Run**
- При `DRY_RUN=true` бот не отправляет ордера, но логирует сигналы и обращения к fee‑rate.

**Security Notes**
- Никогда не коммитьте `.env`.
- Логи могут содержать чувствительные детали, храните их осторожно.

**Troubleshooting**
- Нет сделок: проверьте `YES_TOKEN_ID`, `NO_TOKEN_ID`, `CONDITION_ID`.
- Нет user channel событий: проверьте API ключи.
- Ошибки merge: проверьте `POLYGON_RPC_URL` и наличие газа на кошельке.