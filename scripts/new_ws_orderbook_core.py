#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import new_sports_market_filters as market_filters

SCALE = Decimal("1000000")
PM_GAMMA = "https://gamma-api.polymarket.com"
KS_REST = "https://api.elections.kalshi.com/trade-api/v2"
PM_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
KS_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
DEFAULT_WS_DB_PATH = Path("data/new/new_arb_research.sqlite")
FORBIDDEN_LEGACY_DB_PATH = Path("data/old/old_arb_research.sqlite")
DEFAULT_WS_SPORTS = "tennis,esports,baseball"

PM_SPORT_TAG_SLUGS = (
    "mlb",
    "nba",
    "wnba",
    "nfl",
    "cfb",
    "cfl",
    "tennis",
    "soccer",
    "world-cup",
    "cricket",
    "ufc",
    "boxing",
    "golf",
    "pickleball",
    "lacrosse",
    "pll",
    "hockey",
    "nhl",
    "baseball",
    "kbo",
    "rugby",
    "table-tennis",
    "esports",
    "cs2",
    "lol",
    "valorant",
)

SPORT_PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "tennis": {
        "aliases": ("tennis", "atp", "wta", "itf"),
        "pm_tag_slugs": ("tennis",),
        "pm_source_values": ("tennis", "atp", "wta", "itf"),
        "ks_series": ("KXATPMATCH", "KXATPCHALLENGERMATCH", "KXWTAMATCH", "KXITFMATCH", "KXITFWMATCH"),
    },
    "esports": {
        "aliases": ("esports", "valorant", "cs2", "lol", "league-of-legends", "league_of_legends"),
        "pm_tag_slugs": ("esports", "valorant", "cs2", "lol"),
        "pm_source_values": ("esports", "valorant", "cs2", "lol"),
        "ks_series": ("KXVALORANTGAME", "KXCS2GAME", "KXLOLGAME"),
    },
    "baseball": {
        "aliases": ("baseball", "mlb", "kbo"),
        "pm_tag_slugs": ("baseball", "mlb", "kbo"),
        "pm_source_values": ("baseball", "mlb", "kbo"),
        "ks_series": ("KXMLBGAME", "KXBASEBALLGAME"),
    },
}

KS_SPORT_SERIES = (
    "KXMLBGAME",
    "KXBASEBALLGAME",
    "KXNBAGAME",
    "KXWNBAGAME",
    "KXWNBA",
    "KXNFLGAME",
    "KXNCAAFGAME",
    "KXCFBGAME",
    "KXATPMATCH",
    "KXATPCHALLENGERMATCH",
    "KXWTAMATCH",
    "KXITFMATCH",
    "KXITFWMATCH",
    "KXWCGAME",
    "KXUFCFIGHT",
    "KXBOXING",
    "KXBOXINGGAME",
    "KXCS2GAME",
    "KXLOLGAME",
    "KXVALORANTGAME",
    "KXF1",
)

KS_SPORT_CATEGORY_CANDIDATES = ("sports", "Sports")

SPORT_TEXT_MARKERS = (
    "sport",
    "baseball",
    "basketball",
    "football",
    "soccer",
    "tennis",
    "golf",
    "hockey",
    "boxing",
    "ufc",
    "cricket",
    "rugby",
    "formula 1",
    "f1",
    "esports",
    "cs2",
    "league of legends",
    "valorant",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_path_text(path: Path | str) -> str:
    return Path(path).expanduser().as_posix().rstrip("/")


def is_forbidden_legacy_db_path(path: Path | str) -> bool:
    text = normalized_path_text(path)
    return text == FORBIDDEN_LEGACY_DB_PATH.as_posix() or text.endswith(f"/{FORBIDDEN_LEGACY_DB_PATH.as_posix()}")


def ensure_ws_db_path(path: Path | str) -> Path:
    db_path = Path(path)
    if is_forbidden_legacy_db_path(db_path):
        raise ValueError(
            "new WebSocket scripts must not write to data/old/old_arb_research.sqlite; "
            "use data/new/new_arb_research.sqlite for the new pipeline."
        )
    return db_path


def utc_from_ms(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.startswith("."):
        text = f"0{text}"
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def to_scaled(value: Decimal | str | int | float | None) -> int | None:
    decimal = decimal_or_none(value)
    if decimal is None:
        return None
    return int((decimal * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_payload_hash(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def normalize_sports_arg(sports: str | list[str] | tuple[str, ...] | set[str] | None) -> tuple[str, ...] | None:
    if sports is None:
        return None
    if isinstance(sports, str):
        raw_values = [part.strip().lower() for part in sports.split(",")]
    else:
        raw_values = [str(part).strip().lower() for part in sports]
    requested = [value for value in raw_values if value]
    if not requested or "all" in requested:
        return None

    alias_to_sport: dict[str, str] = {}
    for sport, profile in SPORT_PROFILES.items():
        alias_to_sport[sport] = sport
        for alias in profile.get("aliases", ()):
            alias_to_sport[alias] = sport

    selected: list[str] = []
    unknown: list[str] = []
    for value in requested:
        sport = alias_to_sport.get(value)
        if sport is None:
            unknown.append(value)
            continue
        if sport not in selected:
            selected.append(sport)
    if unknown:
        known = ", ".join(sorted(SPORT_PROFILES))
        raise ValueError(f"unknown sports value(s): {', '.join(unknown)}; supported: all, {known}")
    return tuple(selected)


def selected_pm_tag_slugs(selected_sports: tuple[str, ...] | None) -> tuple[str, ...]:
    if selected_sports is None:
        return PM_SPORT_TAG_SLUGS
    values: list[str] = []
    for sport in selected_sports:
        for value in SPORT_PROFILES[sport]["pm_tag_slugs"]:
            if value not in values:
                values.append(value)
    return tuple(values)


def selected_pm_source_values(selected_sports: tuple[str, ...] | None) -> tuple[str, ...]:
    if selected_sports is None:
        return ()
    values: list[str] = []
    for sport in selected_sports:
        for value in SPORT_PROFILES[sport]["pm_source_values"]:
            if value not in values:
                values.append(value)
    return tuple(values)


def selected_ks_series_tickers(selected_sports: tuple[str, ...] | None) -> tuple[str, ...]:
    if selected_sports is None:
        return ()
    values: list[str] = []
    for sport in selected_sports:
        for value in SPORT_PROFILES[sport]["ks_series"]:
            if value not in values:
                values.append(value)
    return tuple(values)


def http_get_json(base_url: str, path: str, params: dict[str, Any], timeout: float = 20.0, retries: int = 2) -> Any:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "arb-ws-collector/1.0"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt >= retries:
                raise
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
            except ValueError:
                delay = 2.0 * (attempt + 1)
            time.sleep(min(max(delay, 1.0), 30.0))
    raise RuntimeError(f"unreachable HTTP retry state for {url}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS pm_events (
            event_slug TEXT PRIMARY KEY,
            event_id TEXT,
            title TEXT,
            start_date TEXT,
            end_date TEXT,
            active INTEGER NOT NULL DEFAULT 0,
            closed INTEGER NOT NULL DEFAULT 0,
            tag_slug TEXT,
            tag_id TEXT,
            raw_json TEXT NOT NULL,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pm_event_sources (
            event_slug TEXT NOT NULL REFERENCES pm_events(event_slug) ON DELETE CASCADE,
            source_type TEXT NOT NULL,
            source_value TEXT NOT NULL,
            source_label TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY (event_slug, source_type, source_value)
        );

        CREATE TABLE IF NOT EXISTS pm_markets (
            market_id TEXT PRIMARY KEY,
            event_slug TEXT NOT NULL REFERENCES pm_events(event_slug) ON DELETE CASCADE,
            question TEXT,
            market_slug TEXT,
            condition_id TEXT,
            active INTEGER NOT NULL DEFAULT 0,
            closed INTEGER NOT NULL DEFAULT 0,
            enable_order_book INTEGER NOT NULL DEFAULT 0,
            outcomes_json TEXT NOT NULL,
            clob_token_ids_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pm_tokens (
            token_id TEXT PRIMARY KEY,
            market_id TEXT NOT NULL REFERENCES pm_markets(market_id) ON DELETE CASCADE,
            event_slug TEXT NOT NULL REFERENCES pm_events(event_slug) ON DELETE CASCADE,
            outcome_index INTEGER NOT NULL,
            outcome_name TEXT,
            active INTEGER NOT NULL DEFAULT 0,
            closed INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ks_events (
            event_ticker TEXT PRIMARY KEY,
            series_ticker TEXT NOT NULL,
            title TEXT,
            status TEXT,
            raw_json TEXT NOT NULL,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ks_markets (
            market_ticker TEXT PRIMARY KEY,
            event_ticker TEXT NOT NULL REFERENCES ks_events(event_ticker) ON DELETE CASCADE,
            series_ticker TEXT NOT NULL,
            title TEXT,
            yes_sub_title TEXT,
            status TEXT,
            close_time TEXT,
            yes_bid_scaled INTEGER,
            yes_ask_scaled INTEGER,
            raw_json TEXT NOT NULL,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orderbook_payloads (
            payload_hash TEXT PRIMARY KEY,
            raw_json TEXT NOT NULL,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orderbook_observations (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue TEXT NOT NULL CHECK (venue IN ('pm', 'ks')),
            instrument_id TEXT NOT NULL,
            collected_ts_utc TEXT NOT NULL,
            source_ts_utc TEXT,
            source_ts_raw TEXT,
            status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
            error_message TEXT,
            request_path TEXT,
            depth INTEGER,
            latency_ms INTEGER,
            payload_hash TEXT REFERENCES orderbook_payloads(payload_hash),
            best_bid_scaled INTEGER,
            best_bid_size_scaled INTEGER,
            best_ask_scaled INTEGER,
            best_ask_size_scaled INTEGER
        );

        CREATE TABLE IF NOT EXISTS orderbook_levels (
            level_id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL REFERENCES orderbook_observations(observation_id) ON DELETE CASCADE,
            side TEXT NOT NULL,
            level_index INTEGER NOT NULL,
            price_scaled INTEGER NOT NULL,
            size_scaled INTEGER NOT NULL,
            order_count INTEGER,
            UNIQUE(observation_id, side, level_index)
        );

        CREATE INDEX IF NOT EXISTS idx_orderbook_obs_lookup
            ON orderbook_observations(venue, instrument_id, status, collected_ts_utc);
        CREATE INDEX IF NOT EXISTS idx_orderbook_levels_obs
            ON orderbook_levels(observation_id, side, level_index);
        """
    )
    conn.commit()


def connect_db(path: Path) -> sqlite3.Connection:
    path = ensure_ws_db_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def upsert_pm_event(conn: sqlite3.Connection, event: dict[str, Any], tag_slug: str) -> int:
    slug = str(event.get("slug") or event.get("event_slug") or "")
    if not slug:
        return 0
    filter_event = dict(event)
    if tag_slug:
        filter_event["_source_tag_slug"] = tag_slug
    markets = market_filters.eligible_pm_markets(filter_event)
    if not markets:
        return 0
    now = utc_now()
    conn.execute(
        """
        INSERT INTO pm_events (
            event_slug, event_id, title, start_date, end_date, active, closed, tag_slug,
            tag_id, raw_json, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_slug) DO UPDATE SET
            event_id=excluded.event_id,
            title=excluded.title,
            start_date=excluded.start_date,
            end_date=excluded.end_date,
            active=excluded.active,
            closed=excluded.closed,
            tag_slug=excluded.tag_slug,
            tag_id=excluded.tag_id,
            raw_json=excluded.raw_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            slug,
            str(event.get("id") or event.get("event_id") or ""),
            str(event.get("title") or event.get("question") or ""),
            str(event.get("startDate") or event.get("start_date") or ""),
            str(event.get("endDate") or event.get("end_date") or ""),
            1 if event.get("active") else 0,
            1 if event.get("closed") else 0,
            tag_slug,
            str(event.get("tag_id") or ""),
            json_text(filter_event),
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO pm_event_sources (
            event_slug, source_type, source_value, source_label, first_seen_ts, last_seen_ts
        ) VALUES (?, 'tag_slug', ?, ?, ?, ?)
        ON CONFLICT(event_slug, source_type, source_value) DO UPDATE SET
            source_label=excluded.source_label,
            last_seen_ts=excluded.last_seen_ts
        """,
        (slug, tag_slug, tag_slug, now, now),
    )
    count = 1
    for market in markets:
        count += upsert_pm_market(conn, slug, market)
    return count


def upsert_pm_market(conn: sqlite3.Connection, event_slug: str, market: dict[str, Any]) -> int:
    market_id = str(market.get("id") or market.get("market_id") or market.get("conditionId") or "")
    if not market_id:
        return 0
    now = utc_now()
    outcomes = parse_json_list(market.get("outcomes"))
    token_ids = parse_json_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    active = 1 if market.get("active") else 0
    closed = 1 if market.get("closed") else 0
    enable_order_book = 1 if market.get("enableOrderBook") or market.get("enable_order_book") else 0
    conn.execute(
        """
        INSERT INTO pm_markets (
            market_id, event_slug, question, market_slug, condition_id, active, closed,
            enable_order_book, outcomes_json, clob_token_ids_json, raw_json,
            first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            event_slug=excluded.event_slug,
            question=excluded.question,
            market_slug=excluded.market_slug,
            condition_id=excluded.condition_id,
            active=excluded.active,
            closed=excluded.closed,
            enable_order_book=excluded.enable_order_book,
            outcomes_json=excluded.outcomes_json,
            clob_token_ids_json=excluded.clob_token_ids_json,
            raw_json=excluded.raw_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            market_id,
            event_slug,
            str(market.get("question") or ""),
            str(market.get("slug") or market.get("market_slug") or ""),
            str(market.get("conditionId") or market.get("condition_id") or ""),
            active,
            closed,
            enable_order_book,
            json_text(outcomes),
            json_text(token_ids),
            json_text(market),
            now,
            now,
        ),
    )
    for index, token_id in enumerate(token_ids):
        token = str(token_id or "")
        if not token:
            continue
        outcome_name = str(outcomes[index]) if index < len(outcomes) else ""
        conn.execute(
            """
            INSERT INTO pm_tokens (
                token_id, market_id, event_slug, outcome_index, outcome_name,
                active, closed, raw_json, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                market_id=excluded.market_id,
                event_slug=excluded.event_slug,
                outcome_index=excluded.outcome_index,
                outcome_name=excluded.outcome_name,
                active=excluded.active,
                closed=excluded.closed,
                raw_json=excluded.raw_json,
                last_seen_ts=excluded.last_seen_ts
            """,
            (
                token,
                market_id,
                event_slug,
                index,
                outcome_name,
                active,
                closed,
                json_text({"token_id": token, "outcome_index": index, "outcome_name": outcome_name}),
                now,
                now,
            ),
        )
    return 1


def upsert_ks_market(conn: sqlite3.Connection, series_ticker: str, market: dict[str, Any]) -> int:
    market_ticker = str(market.get("ticker") or market.get("market_ticker") or "")
    event_ticker = str(market.get("event_ticker") or "")
    if not market_ticker or not event_ticker:
        return 0
    now = utc_now()
    event_title = str(market.get("event_title") or market.get("title") or "")
    status = str(market.get("status") or "")
    conn.execute(
        """
        INSERT INTO ks_events (
            event_ticker, series_ticker, title, status, raw_json, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_ticker) DO UPDATE SET
            series_ticker=excluded.series_ticker,
            title=excluded.title,
            status=excluded.status,
            raw_json=excluded.raw_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (event_ticker, series_ticker, event_title, status, json_text(market), now, now),
    )
    conn.execute(
        """
        INSERT INTO ks_markets (
            market_ticker, event_ticker, series_ticker, title, yes_sub_title, status,
            close_time, yes_bid_scaled, yes_ask_scaled, raw_json, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_ticker) DO UPDATE SET
            event_ticker=excluded.event_ticker,
            series_ticker=excluded.series_ticker,
            title=excluded.title,
            yes_sub_title=excluded.yes_sub_title,
            status=excluded.status,
            close_time=excluded.close_time,
            yes_bid_scaled=excluded.yes_bid_scaled,
            yes_ask_scaled=excluded.yes_ask_scaled,
            raw_json=excluded.raw_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            market_ticker,
            event_ticker,
            series_ticker,
            str(market.get("title") or ""),
            str(market.get("yes_sub_title") or market.get("subtitle") or ""),
            status,
            str(market.get("close_time") or ""),
            to_scaled(market.get("yes_bid_dollars")),
            to_scaled(market.get("yes_ask_dollars")),
            json_text(market),
            now,
            now,
        ),
    )
    return 1


def discover_pm_sports(
    conn: sqlite3.Connection,
    *,
    page_limit: int = 200,
    max_pages: int = 0,
    selected_sports: tuple[str, ...] | None = None,
) -> int:
    seen: set[str] = set()
    total = 0
    for tag_slug in selected_pm_tag_slugs(selected_sports):
        offset = 0
        page = 0
        while True:
            if max_pages and page >= max_pages:
                break
            payload = http_get_json(
                PM_GAMMA,
                "/events",
                {
                    "tag_slug": tag_slug,
                    "active": "true",
                    "closed": "false",
                    "limit": page_limit,
                    "offset": offset,
                    "order": "startDate",
                    "ascending": "true",
                },
            )
            events = payload if isinstance(payload, list) else []
            if not events:
                break
            page += 1
            for event in events:
                if not isinstance(event, dict):
                    continue
                slug = str(event.get("slug") or "")
                if not slug or (tag_slug, slug) in seen:
                    continue
                seen.add((tag_slug, slug))
                total += upsert_pm_event(conn, event, tag_slug)
            conn.commit()
            if len(events) < page_limit:
                break
            offset += len(events)
    return total


def ks_series_looks_sports(series: dict[str, Any]) -> bool:
    category = str(series.get("category") or "").strip().lower()
    if category == "sports":
        return True
    tags = series.get("tags") if isinstance(series.get("tags"), list) else []
    text_parts = [
        str(series.get("ticker") or ""),
        str(series.get("title") or ""),
        category,
        *[str(tag) for tag in tags],
    ]
    text = " ".join(text_parts).lower()
    return any(marker in text for marker in SPORT_TEXT_MARKERS)


def extract_ks_sports_series_tickers(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    tickers: list[str] = []
    seen: set[str] = set()
    for series in payload.get("series") or []:
        if not isinstance(series, dict) or not ks_series_looks_sports(series):
            continue
        ticker = str(series.get("ticker") or "").strip()
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def discover_ks_sports_series_tickers(selected_sports: tuple[str, ...] | None = None) -> list[str]:
    selected_series = selected_ks_series_tickers(selected_sports)
    if selected_series:
        return list(selected_series)

    tickers: list[str] = []
    seen: set[str] = set()

    def add(values: list[str]) -> None:
        for value in values:
            ticker = str(value or "").strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)

    for category in KS_SPORT_CATEGORY_CANDIDATES:
        try:
            payload = http_get_json(KS_REST, "/series", {"category": category, "include_product_metadata": "true"})
        except Exception:  # noqa: BLE001 - metadata discovery must fall back to the known sports series list.
            continue
        add(extract_ks_sports_series_tickers(payload))

    if not tickers:
        try:
            payload = http_get_json(KS_REST, "/series", {"include_product_metadata": "true"})
        except Exception:  # noqa: BLE001
            payload = None
        add(extract_ks_sports_series_tickers(payload))

    add(list(KS_SPORT_SERIES))
    return tickers


def discover_ks_sports(
    conn: sqlite3.Connection,
    *,
    page_limit: int = 200,
    max_pages: int = 0,
    selected_sports: tuple[str, ...] | None = None,
) -> int:
    total = 0
    for series_ticker in discover_ks_sports_series_tickers(selected_sports):
        cursor = ""
        page = 0
        series_markets: list[dict[str, Any]] = []
        while True:
            if max_pages and page >= max_pages:
                break
            params: dict[str, Any] = {
                "series_ticker": series_ticker,
                "status": "open",
                "limit": min(max(page_limit, 1), 200),
            }
            if cursor:
                params["cursor"] = cursor
            payload = http_get_json(KS_REST, "/markets", params)
            markets = payload.get("markets", []) if isinstance(payload, dict) else []
            if not markets:
                break
            page += 1
            series_markets.extend(market for market in markets if isinstance(market, dict))
            cursor = str(payload.get("cursor") or "")
            if not cursor:
                break
        for market in market_filters.eligible_ks_market_groups(series_markets):
            total += upsert_ks_market(conn, series_ticker, market)
        conn.commit()
    return total


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pm_filter_payload_from_row(row: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any]]:
    data = dict(row)
    event = _json_object(data.get("event_raw_json"))
    event.setdefault("slug", data.get("event_slug") or "")
    event.setdefault("title", data.get("event_title") or "")
    event.setdefault("active", data.get("event_active"))
    event.setdefault("closed", data.get("event_closed"))
    event.setdefault("_source_tag_slug", data.get("event_tag_slug") or "")

    market = _json_object(data.get("market_raw_json"))
    market.setdefault("id", data.get("market_id") or "")
    market.setdefault("question", data.get("market_question") or "")
    market.setdefault("slug", data.get("market_slug") or "")
    market.setdefault("active", data.get("market_active"))
    market.setdefault("closed", data.get("market_closed"))
    market.setdefault("enableOrderBook", data.get("market_enable_order_book"))
    market.setdefault("outcomes", data.get("outcomes_json") or "[]")
    market.setdefault("clobTokenIds", data.get("clob_token_ids_json") or "[]")
    return event, market


def _ks_filter_market_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    market = _json_object(data.get("raw_json"))
    market.setdefault("ticker", data.get("market_ticker") or "")
    market.setdefault("market_ticker", data.get("market_ticker") or "")
    market.setdefault("event_ticker", data.get("event_ticker") or "")
    market.setdefault("series_ticker", data.get("series_ticker") or "")
    market.setdefault("title", data.get("title") or "")
    market.setdefault("yes_sub_title", data.get("yes_sub_title") or "")
    market.setdefault("status", data.get("status") or "")
    return market


def active_pm_token_ids(conn: sqlite3.Connection, selected_sports: tuple[str, ...] | None = None) -> list[str]:
    params: list[Any] = []
    sport_filter = ""
    source_values = selected_pm_source_values(selected_sports)
    if source_values:
        placeholders = ",".join("?" for _ in source_values)
        sport_filter = f"""
          AND (
            event.tag_slug IN ({placeholders})
            OR EXISTS (
              SELECT 1
              FROM pm_event_sources source
              WHERE source.event_slug = event.event_slug
                AND source.source_type IN ('tag_slug', 'series_slug')
                AND source.source_value IN ({placeholders})
            )
          )
        """
        params.extend(source_values)
        params.extend(source_values)
    rows = conn.execute(
        f"""
        SELECT
          token.token_id,
          token.event_slug,
          event.title AS event_title,
          event.active AS event_active,
          event.closed AS event_closed,
          event.tag_slug AS event_tag_slug,
          event.raw_json AS event_raw_json,
          market.market_id,
          market.question AS market_question,
          market.market_slug,
          market.active AS market_active,
          market.closed AS market_closed,
          market.enable_order_book AS market_enable_order_book,
          market.outcomes_json,
          market.clob_token_ids_json,
          market.raw_json AS market_raw_json
        FROM pm_tokens token
        JOIN pm_markets market ON market.market_id = token.market_id
        JOIN pm_events event ON event.event_slug = token.event_slug
        WHERE token.active = 1
          AND token.closed = 0
          AND market.active = 1
          AND market.closed = 0
          AND market.enable_order_book = 1
          AND event.active = 1
          AND event.closed = 0
          {sport_filter}
        ORDER BY event.tag_slug, event.start_date, token.token_id
        """,
        params,
    ).fetchall()
    token_ids: list[str] = []
    for row in rows:
        event, market = _pm_filter_payload_from_row(row)
        if market_filters.is_pm_binary_winner_market(event, market):
            token_ids.append(str(row["token_id"]))
    return token_ids


def active_ks_market_tickers(conn: sqlite3.Connection, selected_sports: tuple[str, ...] | None = None) -> list[str]:
    params: list[Any] = []
    sport_filter = ""
    series_tickers = selected_ks_series_tickers(selected_sports)
    if series_tickers:
        placeholders = ",".join("?" for _ in series_tickers)
        sport_filter = f"AND series_ticker IN ({placeholders})"
        params.extend(series_tickers)
    rows = conn.execute(
        f"""
        SELECT market_ticker, event_ticker, series_ticker, title, yes_sub_title, status, raw_json
        FROM ks_markets
        WHERE LOWER(COALESCE(status, 'open')) IN ('', 'open', 'active')
          {sport_filter}
        ORDER BY series_ticker, event_ticker, market_ticker
        """,
        params,
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        market = _ks_filter_market_from_row(row)
        grouped.setdefault(str(market.get("event_ticker") or ""), []).append(market)
    eligible_tickers: set[str] = set()
    for markets in grouped.values():
        for market in market_filters.eligible_ks_binary_event_markets(markets):
            eligible_tickers.add(str(market.get("market_ticker") or market.get("ticker") or ""))
    return [str(row["market_ticker"]) for row in rows if str(row["market_ticker"]) in eligible_tickers]


@dataclass
class BookSnapshot:
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]
    source_ts_utc: str | None = None
    source_ts_raw: str | None = None


@dataclass
class PriceBook:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_update_monotonic: float = 0.0
    last_update_utc: str | None = None
    source_ts_utc: str | None = None
    source_ts_raw: str | None = None

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.last_update_monotonic = 0.0
        self.last_update_utc = None
        self.source_ts_utc = None
        self.source_ts_raw = None

    def replace(self, bids: list[Any], asks: list[Any], source_ts_raw: Any = None) -> None:
        self.bids = levels_to_map(bids)
        self.asks = levels_to_map(asks)
        self.touch(source_ts_raw)

    def set_level(self, side: str, price: Any, size: Any, source_ts_raw: Any = None) -> None:
        price_decimal = decimal_or_none(price)
        size_decimal = decimal_or_none(size)
        if price_decimal is None or size_decimal is None:
            return
        levels = self.bids if side == "bid" else self.asks
        if size_decimal <= 0:
            levels.pop(price_decimal, None)
        else:
            levels[price_decimal] = size_decimal
        self.touch(source_ts_raw)

    def touch(self, source_ts_raw: Any = None) -> None:
        self.last_update_monotonic = time.monotonic()
        self.last_update_utc = utc_now()
        if source_ts_raw not in (None, ""):
            self.source_ts_raw = str(source_ts_raw)
            self.source_ts_utc = utc_from_ms(source_ts_raw) or str(source_ts_raw)

    def top(self, depth: int) -> BookSnapshot:
        bids = sorted(((p, s) for p, s in self.bids.items() if s > 0), key=lambda item: item[0], reverse=True)[:depth]
        asks = sorted(((p, s) for p, s in self.asks.items() if s > 0), key=lambda item: item[0])[:depth]
        return BookSnapshot(bids=bids, asks=asks, source_ts_utc=self.source_ts_utc, source_ts_raw=self.source_ts_raw)

    def is_fresh(self, max_stale_seconds: float) -> bool:
        return self.last_update_monotonic > 0 and (time.monotonic() - self.last_update_monotonic) <= max_stale_seconds


class KalshiPriceBook(PriceBook):
    def __init__(self) -> None:
        super().__init__()
        self.no_bids: dict[Decimal, Decimal] = {}

    def reset(self) -> None:
        super().reset()
        self.no_bids.clear()

    def replace_kalshi(self, yes_levels: list[Any], no_levels: list[Any], source_ts_raw: Any = None) -> None:
        self.bids = levels_to_map(yes_levels)
        self.no_bids = levels_to_map(no_levels)
        self.asks.clear()
        self.touch(source_ts_raw)

    def apply_delta(self, side: str, price: Any, delta: Any, source_ts_raw: Any = None) -> None:
        price_decimal = decimal_or_none(price)
        delta_decimal = decimal_or_none(delta)
        if price_decimal is None or delta_decimal is None:
            return
        levels = self.bids if side == "yes" else self.no_bids
        new_size = levels.get(price_decimal, Decimal("0")) + delta_decimal
        if new_size <= 0:
            levels.pop(price_decimal, None)
        else:
            levels[price_decimal] = new_size
        self.touch(source_ts_raw)

    def top(self, depth: int) -> BookSnapshot:
        bids = sorted(((p, s) for p, s in self.bids.items() if s > 0), key=lambda item: item[0], reverse=True)[:depth]
        asks = []
        for no_price, size in self.no_bids.items():
            if size <= 0:
                continue
            yes_ask = Decimal("1") - no_price
            if Decimal("0") <= yes_ask <= Decimal("1"):
                asks.append((yes_ask, size))
        asks = sorted(asks, key=lambda item: item[0])[:depth]
        return BookSnapshot(bids=bids, asks=asks, source_ts_utc=self.source_ts_utc, source_ts_raw=self.source_ts_raw)


def levels_to_map(levels: list[Any]) -> dict[Decimal, Decimal]:
    result: dict[Decimal, Decimal] = {}
    for level in levels or []:
        if isinstance(level, dict):
            price = level.get("price")
            size = level.get("size")
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price, size = level[0], level[1]
        else:
            continue
        price_decimal = decimal_or_none(price)
        size_decimal = decimal_or_none(size)
        if price_decimal is None or size_decimal is None or size_decimal <= 0:
            continue
        result[price_decimal] = size_decimal
    return result


def handle_pm_message(books: dict[str, PriceBook], payload: dict[str, Any]) -> None:
    messages = payload if isinstance(payload, list) else [payload]
    for message in messages:
        if not isinstance(message, dict):
            continue
        event_type = str(message.get("event_type") or message.get("type") or "")
        if event_type == "book":
            asset_id = str(message.get("asset_id") or "")
            if not asset_id:
                continue
            book = books.setdefault(asset_id, PriceBook())
            book.replace(message.get("bids") or [], message.get("asks") or [], message.get("timestamp"))
        elif event_type == "price_change":
            timestamp = message.get("timestamp")
            for change in message.get("price_changes") or []:
                if not isinstance(change, dict):
                    continue
                asset_id = str(change.get("asset_id") or "")
                if not asset_id:
                    continue
                side = "bid" if str(change.get("side") or "").upper() == "BUY" else "ask"
                books.setdefault(asset_id, PriceBook()).set_level(side, change.get("price"), change.get("size"), timestamp)


def handle_ks_message(books: dict[str, KalshiPriceBook], payload: dict[str, Any]) -> None:
    messages = payload if isinstance(payload, list) else [payload]
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_type = str(message.get("type") or "")
        msg = message.get("msg") if isinstance(message.get("msg"), dict) else message
        ticker = str(msg.get("market_ticker") or "")
        if not ticker:
            continue
        book = books.setdefault(ticker, KalshiPriceBook())
        if message_type == "orderbook_snapshot":
            source_ts = msg.get("ts_ms") or msg.get("ts")
            book.replace_kalshi(msg.get("yes_dollars_fp") or [], msg.get("no_dollars_fp") or [], source_ts)
        elif message_type == "orderbook_delta":
            source_ts = msg.get("ts_ms") or msg.get("ts")
            book.apply_delta(str(msg.get("side") or ""), msg.get("price_dollars"), msg.get("delta_fp"), source_ts)


def insert_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    payload_hash = stable_payload_hash(payload)
    now = utc_now()
    conn.execute(
        """
        INSERT INTO orderbook_payloads (payload_hash, raw_json, first_seen_ts, last_seen_ts)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(payload_hash) DO UPDATE SET last_seen_ts=excluded.last_seen_ts
        """,
        (payload_hash, json_text(payload), now, now),
    )
    return payload_hash


def record_orderbook_snapshot(
    conn: sqlite3.Connection,
    *,
    venue: str,
    instrument_id: str,
    snapshot: BookSnapshot,
    depth: int,
    request_path: str,
    collected_ts_utc: str,
) -> int:
    best_bid = snapshot.bids[0] if snapshot.bids else (None, None)
    best_ask = snapshot.asks[0] if snapshot.asks else (None, None)
    payload = {
        "venue": venue,
        "instrument_id": instrument_id,
        "bids": [[str(price), str(size)] for price, size in snapshot.bids],
        "asks": [[str(price), str(size)] for price, size in snapshot.asks],
        "source_ts_utc": snapshot.source_ts_utc,
        "source_ts_raw": snapshot.source_ts_raw,
    }
    payload_hash = insert_payload(conn, payload)
    cursor = conn.execute(
        """
        INSERT INTO orderbook_observations (
            venue, instrument_id, collected_ts_utc, source_ts_utc, source_ts_raw,
            status, error_message, request_path, depth, latency_ms, payload_hash,
            best_bid_scaled, best_bid_size_scaled, best_ask_scaled, best_ask_size_scaled
        ) VALUES (?, ?, ?, ?, ?, 'ok', NULL, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            venue,
            instrument_id,
            collected_ts_utc,
            snapshot.source_ts_utc,
            snapshot.source_ts_raw,
            request_path,
            depth,
            payload_hash,
            to_scaled(best_bid[0]),
            to_scaled(best_bid[1]),
            to_scaled(best_ask[0]),
            to_scaled(best_ask[1]),
        ),
    )
    observation_id = int(cursor.lastrowid)
    for side, levels in (("bid", snapshot.bids), ("ask", snapshot.asks)):
        for index, (price, size) in enumerate(levels[:depth]):
            conn.execute(
                """
                INSERT INTO orderbook_levels (
                    observation_id, side, level_index, price_scaled, size_scaled, order_count
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (observation_id, side, index, to_scaled(price), to_scaled(size)),
            )
    return observation_id


def record_orderbook_error(
    conn: sqlite3.Connection,
    *,
    venue: str,
    instrument_id: str,
    error_message: str,
    request_path: str,
    depth: int,
    collected_ts_utc: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO orderbook_observations (
            venue, instrument_id, collected_ts_utc, source_ts_utc, source_ts_raw,
            status, error_message, request_path, depth, latency_ms, payload_hash,
            best_bid_scaled, best_bid_size_scaled, best_ask_scaled, best_ask_size_scaled
        ) VALUES (?, ?, ?, NULL, NULL, 'error', ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)
        """,
        (venue, instrument_id, collected_ts_utc, error_message, request_path, depth),
    )
    return int(cursor.lastrowid)


def next_observation_id(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'orderbook_observations'").fetchone()
        if row is not None and row[0] is not None:
            return int(row[0]) + 1
    except sqlite3.OperationalError:
        pass
    row = conn.execute("SELECT COALESCE(MAX(observation_id), 0) + 1 FROM orderbook_observations").fetchone()
    return int(row[0])


def record_orderbook_rows_bulk(
    conn: sqlite3.Connection,
    observation_rows: list[tuple[Any, ...]],
    level_rows: list[tuple[Any, ...]],
) -> None:
    if not observation_rows:
        conn.commit()
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            """
            INSERT INTO orderbook_observations (
                observation_id, venue, instrument_id, collected_ts_utc, source_ts_utc, source_ts_raw,
                status, error_message, request_path, depth, latency_ms, payload_hash,
                best_bid_scaled, best_bid_size_scaled, best_ask_scaled, best_ask_size_scaled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
            """,
            observation_rows,
        )
        if level_rows:
            conn.executemany(
                """
                INSERT INTO orderbook_levels (
                    observation_id, side, level_index, price_scaled, size_scaled, order_count
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                level_rows,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@dataclass(frozen=True)
class FlushStats:
    venue: str
    subscribed: int
    ok: int
    fresh: int
    stale: int
    missing: int


def flush_books(
    conn: sqlite3.Connection,
    *,
    venue: str,
    instrument_ids: list[str],
    books: dict[str, PriceBook],
    depth: int,
    max_stale_seconds: float,
    request_path: str,
    write_stale_errors: bool = False,
    write_payloads: bool = True,
    collected_ts_utc: str | None = None,
) -> FlushStats:
    collected = collected_ts_utc or utc_now()
    ok = fresh = stale = missing = 0
    if not write_payloads:
        next_id = next_observation_id(conn)
        observation_rows: list[tuple[Any, ...]] = []
        level_rows: list[tuple[Any, ...]] = []
        for instrument_id in instrument_ids:
            book = books.get(instrument_id)
            if book is None or book.last_update_monotonic <= 0:
                missing += 1
                if write_stale_errors:
                    observation_rows.append(
                        (
                            next_id,
                            venue,
                            instrument_id,
                            collected,
                            None,
                            None,
                            "error",
                            "missing_ws_book",
                            request_path,
                            depth,
                            None,
                            None,
                            None,
                            None,
                        )
                    )
                    next_id += 1
                continue

            if book.is_fresh(max_stale_seconds):
                fresh += 1
            else:
                stale += 1

            snapshot = book.top(depth)
            best_bid = snapshot.bids[0] if snapshot.bids else (None, None)
            best_ask = snapshot.asks[0] if snapshot.asks else (None, None)
            observation_id = next_id
            next_id += 1
            observation_rows.append(
                (
                    observation_id,
                    venue,
                    instrument_id,
                    collected,
                    snapshot.source_ts_utc,
                    snapshot.source_ts_raw,
                    "ok",
                    None,
                    request_path,
                    depth,
                    to_scaled(best_bid[0]),
                    to_scaled(best_bid[1]),
                    to_scaled(best_ask[0]),
                    to_scaled(best_ask[1]),
                )
            )
            for side, levels in (("bid", snapshot.bids), ("ask", snapshot.asks)):
                for index, (price, size) in enumerate(levels[:depth]):
                    level_rows.append((observation_id, side, index, to_scaled(price), to_scaled(size)))
            ok += 1
        record_orderbook_rows_bulk(conn, observation_rows, level_rows)
        return FlushStats(venue=venue, subscribed=len(instrument_ids), ok=ok, fresh=fresh, stale=stale, missing=missing)

    for instrument_id in instrument_ids:
        book = books.get(instrument_id)
        if book is None or book.last_update_monotonic <= 0:
            missing += 1
            if write_stale_errors:
                record_orderbook_error(
                    conn,
                    venue=venue,
                    instrument_id=instrument_id,
                    error_message="missing_ws_book",
                    request_path=request_path,
                    depth=depth,
                    collected_ts_utc=collected,
                )
            continue

        if book.is_fresh(max_stale_seconds):
            fresh += 1
        else:
            stale += 1

        record_orderbook_snapshot(
            conn,
            venue=venue,
            instrument_id=instrument_id,
            snapshot=book.top(depth),
            depth=depth,
            request_path=request_path,
            collected_ts_utc=collected,
        )
        ok += 1
    conn.commit()
    return FlushStats(venue=venue, subscribed=len(instrument_ids), ok=ok, fresh=fresh, stale=stale, missing=missing)


def write_status_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ts_utc",
        "venue",
        "subscribed",
        "ok",
        "fresh",
        "stale",
        "missing",
        "last_error",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def chunks(items: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [items]
    return [items[index : index + size] for index in range(0, len(items), size)]


def kalshi_auth_headers(path: str = "/trade-api/ws/v2") -> dict[str, str]:
    raw_headers = os.getenv("KALSHI_WS_HEADERS_JSON")
    if raw_headers:
        parsed = json.loads(raw_headers)
        if not isinstance(parsed, dict):
            raise ValueError("KALSHI_WS_HEADERS_JSON must decode to an object")
        return {str(key): str(value) for key, value in parsed.items()}

    access_key = os.getenv("KALSHI_ACCESS_KEY")
    access_signature = os.getenv("KALSHI_ACCESS_SIGNATURE")
    access_timestamp = os.getenv("KALSHI_ACCESS_TIMESTAMP")
    if access_key and access_signature and access_timestamp:
        return {
            "KALSHI-ACCESS-KEY": access_key,
            "KALSHI-ACCESS-SIGNATURE": access_signature,
            "KALSHI-ACCESS-TIMESTAMP": access_timestamp,
        }

    key_id = os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_API_KEY")
    private_key_file = os.getenv("KALSHI_PRIVATE_KEY_FILE")
    if not key_id or not private_key_file:
        raise RuntimeError(
            "Kalshi WebSocket auth requires KALSHI_WS_HEADERS_JSON, explicit "
            "KALSHI_ACCESS_* headers, or KALSHI_API_KEY_ID plus KALSHI_PRIVATE_KEY_FILE."
        )
    timestamp_ms = str(int(time.time() * 1000))
    message = f"{timestamp_ms}GET{path}"
    signature = sign_with_openssl_pss(private_key_file, message)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
    }


def sign_with_openssl_pss(private_key_file: str, message: str) -> str:
    proc = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            private_key_file,
            "-sigopt",
            "rsa_padding_mode:pss",
            "-sigopt",
            "rsa_pss_saltlen:-1",
        ],
        input=message.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return base64.b64encode(proc.stdout).decode("ascii")
