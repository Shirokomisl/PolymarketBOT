import asyncio
import asyncpg
from urllib.parse import urlparse

async def check_connection():
    # Ваша DSN строка
    dsn = "postgresql://postgres:Misly_Shiroko1@localhost:5432/postgres"
    
    # Разбираем DSN на компоненты
    parsed = urlparse(dsn)
    
    print("Проверка параметров подключения:")
    print(f"Пользователь: {parsed.username}")
    print(f"Пароль: {'*' * len(parsed.password)}")  # скрываем пароль
    print(f"Хост: {parsed.hostname}")
    print(f"Порт: {parsed.port or 5432}")
    print(f"База данных: {parsed.path[1:] if parsed.path else 'postgres'}")
    print("-" * 40)
    
    # Проверка 1: Доступность хоста
    import socket
    try:
        ip = socket.gethostbyname(parsed.hostname)
        print(f"✓ Хост {parsed.hostname} разрешается в IP: {ip}")
    except socket.gaierror:
        print(f"✗ Хост {parsed.hostname} не найден!")
        return
    
    # Проверка 2: Доступность порта
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    result = sock.connect_ex((parsed.hostname, parsed.port or 5432))
    if result == 0:
        print(f"✓ Порт {parsed.port or 5432} открыт")
    else:
        print(f"✗ Порт {parsed.port or 5432} недоступен (код: {result})")
        print("  Возможно PostgreSQL не запущен или блокируется файерволом")
    sock.close()
    
    print("-" * 40)
    
    # Проверка 3: Непосредственное подключение к БД
    try:
        print("Попытка подключения к PostgreSQL...")
        conn = await asyncpg.connect(
            user=parsed.username,
            password=parsed.password,
            database=parsed.path[1:] if parsed.path else 'postgres',
            host=parsed.hostname,
            port=parsed.port or 5432,
            timeout=5
        )
        
        # Получаем информацию о сервере
        version = await conn.fetchval("SELECT version();")
        print(f"✓ Успешное подключение!")
        print(f"  Версия PostgreSQL: {version}")
        
        # Проверяем существующие базы данных
        databases = await conn.fetch("SELECT datname FROM pg_database WHERE datistemplate = false;")
        print(f"  Доступные базы данных: {', '.join([db['datname'] for db in databases])}")
        
        await conn.close()
        
    except asyncpg.InvalidCatalogNameError:
        print(f"✗ База данных '{parsed.path[1:]}' не существует!")
        print("  Попробуйте подключиться к другой базе данных")
    except asyncpg.InvalidPasswordError:
        print("✗ Неверный пароль!")
    except asyncpg.InvalidAuthorizationSpecificationError:
        print("✗ Неверное имя пользователя!")
    except asyncpg.CannotConnectNowError:
        print("✗ Сервер не принимает подключения")
    except ConnectionRefusedError:
        print("✗ Подключение отклонено. PostgreSQL запущен?")
    except asyncio.TimeoutError:
        print("✗ Таймаут подключения. Сервер не отвечает")
    except Exception as e:
        print(f"✗ Ошибка подключения: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(check_connection())