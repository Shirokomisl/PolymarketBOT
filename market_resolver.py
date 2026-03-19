import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

GAMMA_BASE = "https://gamma-api.polymarket.com"


@dataclass
class MarketWindow:
    yes_token_id: str
    no_token_id: str
    condition_id: str
    slug: str
    window_start: datetime


def _unwrap_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "events", "markets"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _json_load_maybe(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("[") or value.startswith("{"):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
    return value


def extract_yes_no_token_ids(market: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    yes_id = None
    no_id = None

    tokens = market.get("tokens")
    if isinstance(tokens, list):
        for token in tokens:
            outcome = str(token.get("outcome") or token.get("name") or "").strip().lower()
            token_id = token.get("tokenId") or token.get("token_id") or token.get("id")
            if token_id is None:
                continue
            if outcome == "yes":
                yes_id = str(token_id)
            elif outcome == "no":
                no_id = str(token_id)

    if yes_id and no_id:
        return yes_id, no_id

    outcomes = _json_load_maybe(market.get("outcomes"))
    token_ids = _json_load_maybe(market.get("clobTokenIds"))

    if isinstance(outcomes, list) and isinstance(token_ids, list) and len(outcomes) == len(token_ids):
        for outcome, token_id in zip(outcomes, token_ids):
            if str(outcome).strip().lower() == "yes":
                yes_id = str(token_id)
            elif str(outcome).strip().lower() == "no":
                no_id = str(token_id)

    if yes_id and no_id:
        return yes_id, no_id

    if isinstance(token_ids, list) and len(token_ids) >= 2:
        return str(token_ids[0]), str(token_ids[1])

    return yes_id, no_id


def extract_condition_id(market: Dict[str, Any]) -> Optional[str]:
    for key in ("conditionId", "condition_id", "conditionID"):
        value = market.get(key)
        if value:
            return str(value)
    return None


def _select_market_from_event(
    event: Dict[str, Any],
    market_id: str = "",
    market_slug: str = "",
    market_contains: str = "",
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    markets = event.get("markets") or []
    markets = _unwrap_list(markets) if not isinstance(markets, list) else markets

    if market_id:
        for m in markets:
            if str(m.get("id")) == market_id:
                return m, markets
    if market_slug:
        for m in markets:
            if str(m.get("slug")) == market_slug:
                return m, markets
    if market_contains:
        needle = market_contains.lower()
        for m in markets:
            question = str(m.get("question") or m.get("title") or "").lower()
            if needle in question:
                return m, markets

    if len(markets) == 1:
        return markets[0], markets
    return None, markets


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status == 404:
            return None
        resp.raise_for_status()
        return await resp.json()


async def resolve_market_by_slug(
    slug: str,
    kind: str = "event",
    market_id: str = "",
    market_slug: str = "",
    market_contains: str = "",
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    async with aiohttp.ClientSession() as session:
        if kind == "market":
            market = await _fetch_json(session, f"{GAMMA_BASE}/markets/slug/{slug}")
            if market is None:
                markets = await _fetch_json(session, f"{GAMMA_BASE}/markets?slug={slug}")
                lst = _unwrap_list(markets)
                market = lst[0] if lst else None
            return market, [market] if market else []

        event = await _fetch_json(session, f"{GAMMA_BASE}/events/slug/{slug}")
        if event is None:
            events = await _fetch_json(session, f"{GAMMA_BASE}/events?slug={slug}")
            lst = _unwrap_list(events)
            event = lst[0] if lst else None
        if not event:
            return None, []

        market, markets = _select_market_from_event(event, market_id, market_slug, market_contains)
        return market, markets


def _floor_to_5m(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = dt.minute - (dt.minute % 5)
    return dt.replace(minute=minute)


async def resolve_btc_5m_window(
    prefix: str,
    market_contains: str = "",
    now: Optional[datetime] = None,
) -> Optional[MarketWindow]:
    now = now or datetime.now(timezone.utc)
    base = _floor_to_5m(now)
    candidates = [base, base - timedelta(minutes=5), base + timedelta(minutes=5)]

    for dt in candidates:
        slug = f"{prefix}-{int(dt.timestamp())}"
        market, _ = await resolve_market_by_slug(slug, kind="event", market_contains=market_contains)
        if not market:
            continue

        yes_id, no_id = extract_yes_no_token_ids(market)
        condition_id = extract_condition_id(market)
        if yes_id and no_id and condition_id:
            return MarketWindow(
                yes_token_id=yes_id,
                no_token_id=no_id,
                condition_id=condition_id,
                slug=slug,
                window_start=dt,
            )

    return None