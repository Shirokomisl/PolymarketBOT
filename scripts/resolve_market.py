import argparse
import asyncio
import json
import sys
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlparse

from market_resolver import (
    extract_condition_id,
    extract_yes_no_token_ids,
    resolve_market_by_slug,
)


def _first_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def parse_url(url: str) -> Tuple[str, str, Dict[str, str]]:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]

    kind = ""
    slug = ""
    for i, part in enumerate(parts):
        if part in ("event", "events", "market", "markets"):
            kind = "event" if part in ("event", "events") else "market"
            if i + 1 < len(parts):
                slug = parts[i + 1]
            break

    qs = parse_qs(parsed.query)
    market_id = _first_of(qs.get("market")) or _first_of(qs.get("market_id"))
    market_slug = _first_of(qs.get("market_slug")) or _first_of(qs.get("marketSlug"))
    event_slug = _first_of(qs.get("slug"))

    if not slug and event_slug:
        slug = event_slug

    return kind or "event", slug, {
        "market_id": market_id,
        "market_slug": market_slug,
    }


def _print_market_list(markets: list[Dict[str, Any]]) -> None:
    print("Найдено несколько рынков. Уточни выбор через --market-id или --market-contains.")
    for m in markets:
        mid = m.get("id")
        question = m.get("question") or m.get("title") or ""
        slug = m.get("slug") or ""
        print(f"- id={mid} slug={slug} вопрос={question}")


def _format_env(condition_id: str, yes_id: str, no_id: str) -> str:
    return "\n".join(
        [
            f"CONDITION_ID={condition_id}",
            f"YES_TOKEN_ID={yes_id}",
            f"NO_TOKEN_ID={no_id}",
        ]
    )


def _write_env_file(path: str, condition_id: str, yes_id: str, no_id: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []

    def upsert(key: str, value: str, data: list[str]) -> list[str]:
        prefix = f"{key}="
        replaced = False
        new_lines = []
        for line in data:
            if line.startswith(prefix):
                new_lines.append(f"{key}={value}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"{key}={value}")
        return new_lines

    updated = upsert("CONDITION_ID", condition_id, lines)
    updated = upsert("YES_TOKEN_ID", yes_id, updated)
    updated = upsert("NO_TOKEN_ID", no_id, updated)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Получение YES/NO token_id и condition_id по URL рынка Polymarket",
    )
    parser.add_argument("url", help="URL рынка или события Polymarket")
    parser.add_argument("--market-id", default="", help="ID рынка (если событие содержит несколько рынков)")
    parser.add_argument("--market-slug", default="", help="Slug рынка (если событие содержит несколько рынков)")
    parser.add_argument(
        "--market-contains",
        default="",
        help="Подстрока вопроса рынка для выбора",
    )
    parser.add_argument(
        "--write-env",
        default="",
        help="Путь к .env, куда записать значения",
    )

    args = parser.parse_args()

    kind, slug, hints = parse_url(args.url)
    if not slug:
        print("Ошибка: не удалось извлечь slug из URL")
        sys.exit(1)

    market_id = args.market_id or hints.get("market_id") or ""
    market_slug = args.market_slug or hints.get("market_slug") or ""

    try:
        market, markets = asyncio.run(
            resolve_market_by_slug(
                slug,
                kind=kind,
                market_id=market_id,
                market_slug=market_slug,
                market_contains=args.market_contains,
            )
        )
    except Exception as exc:
        print(f"Ошибка: {exc}")
        sys.exit(1)

    if not market:
        _print_market_list(markets)
        sys.exit(2)

    condition_id = extract_condition_id(market)
    yes_id, no_id = extract_yes_no_token_ids(market)

    if not condition_id or not yes_id or not no_id:
        print("Не удалось извлечь все поля из ответа.")
        print(json.dumps(market, indent=2, ensure_ascii=False))
        sys.exit(3)

    print(_format_env(condition_id, yes_id, no_id))

    if args.write_env:
        _write_env_file(args.write_env, condition_id, yes_id, no_id)
        print(f"\nЗначения записаны в {args.write_env}")


if __name__ == "__main__":
    main()