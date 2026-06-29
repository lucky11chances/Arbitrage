from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterator

import pipeline_core as core


DEFAULT_DB_PATH = Path("data/staging/arb_research.sqlite")
SCALE = 1_000_000
ONE = Decimal("1")
PM_FEE_RATE = Decimal("0.03")
KS_FEE_RATE = Decimal("0.07")


@dataclass(frozen=True)
class ParsedLevel:
    side: str
    price: Decimal
    size: Decimal
    level_index: int
    order_count: int | None = None


@dataclass(frozen=True)
class ParsedBook:
    levels: list[ParsedLevel]
    best_bid: Decimal | None
    best_bid_size: Decimal | None
    best_ask: Decimal | None
    best_ask_size: Decimal | None
    source_ts_raw: str = ""
    source_ts_utc: str = ""


@dataclass(frozen=True)
class EdgeResult:
    best_leg: str | None
    net_edge: Decimal | None
    gross_cost: Decimal | None
    best_leg_bbo_size: Decimal | None
    net_profit_at_bbo: Decimal | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(
        """
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
            source_label TEXT,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(event_slug, source_type, source_value)
        );

        CREATE INDEX IF NOT EXISTS idx_pm_event_sources_value
            ON pm_event_sources(source_type, source_value);

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
            outcome_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0,
            closed INTEGER NOT NULL DEFAULT 0,
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

        CREATE INDEX IF NOT EXISTS idx_orderbook_obs_lookup
            ON orderbook_observations(venue, instrument_id, status, collected_ts_utc);

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

        CREATE TABLE IF NOT EXISTS normalized_contracts (
            normalized_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue TEXT NOT NULL CHECK (venue IN ('pm', 'ks')),
            category_key TEXT NOT NULL,
            universe TEXT,
            event_date TEXT,
            canonical_event_id TEXT,
            market_type TEXT,
            competition_gender TEXT NOT NULL DEFAULT 'unknown'
                CHECK (competition_gender IN ('men', 'women', 'mixed', 'open', 'unknown')),
            gender_source TEXT NOT NULL DEFAULT '',
            gender_confidence TEXT NOT NULL DEFAULT 'low',
            match_name TEXT,
            outcome_name TEXT,
            entity_key TEXT,
            outcome_key TEXT,
            instrument_id TEXT NOT NULL,
            pm_event_slug TEXT,
            pm_market_id TEXT,
            pm_token_id TEXT,
            pm_question TEXT,
            ks_series_ticker TEXT,
            ks_event_ticker TEXT,
            ks_market_ticker TEXT,
            ks_title TEXT,
            safe_pair_candidate INTEGER NOT NULL DEFAULT 0,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            UNIQUE(venue, category_key, instrument_id, outcome_key, canonical_event_id)
        );

        CREATE TABLE IF NOT EXISTS paired_contracts (
            paired_contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_key TEXT NOT NULL UNIQUE,
            universe TEXT NOT NULL,
            category TEXT NOT NULL,
            match_name TEXT NOT NULL,
            event_date TEXT NOT NULL,
            canonical_event_id TEXT NOT NULL,
            market_type TEXT NOT NULL,
            pm_yes_outcome TEXT NOT NULL,
            ks_yes_outcome TEXT NOT NULL,
            pm_event_slug TEXT NOT NULL,
            pm_market_id TEXT NOT NULL,
            pm_token_id TEXT NOT NULL,
            ks_event_ticker TEXT NOT NULL,
            ks_market_ticker TEXT NOT NULL,
            match_format TEXT,
            schedule_source TEXT,
            safe_paired INTEGER NOT NULL DEFAULT 1,
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_paired_contracts_tokens
            ON paired_contracts(pm_token_id, ks_market_ticker, safe_paired);

        CREATE TABLE IF NOT EXISTS edge_snapshots (
            edge_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            paired_contract_id INTEGER NOT NULL REFERENCES paired_contracts(paired_contract_id) ON DELETE CASCADE,
            ts_utc TEXT NOT NULL,
            pm_observation_id INTEGER NOT NULL REFERENCES orderbook_observations(observation_id),
            ks_observation_id INTEGER NOT NULL REFERENCES orderbook_observations(observation_id),
            pm_bid_scaled INTEGER NOT NULL,
            pm_ask_scaled INTEGER NOT NULL,
            pm_bid_size_scaled INTEGER NOT NULL,
            pm_ask_size_scaled INTEGER NOT NULL,
            ks_bid_scaled INTEGER NOT NULL,
            ks_ask_scaled INTEGER NOT NULL,
            ks_bid_size_scaled INTEGER NOT NULL,
            ks_ask_size_scaled INTEGER NOT NULL,
            best_leg TEXT,
            gross_cost_scaled INTEGER,
            net_edge_scaled INTEGER,
            best_leg_bbo_size_scaled INTEGER,
            net_profit_at_bbo_scaled INTEGER,
            alert TEXT,
            alert_reason TEXT,
            book_age_seconds REAL,
            snapshot_skew_seconds REAL
        );

        CREATE TABLE IF NOT EXISTS data_quality_warnings (
            warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            stage TEXT NOT NULL,
            category_key TEXT,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            context_json TEXT NOT NULL
        );
        """
    )
    ensure_column(
        conn,
        "normalized_contracts",
        "competition_gender",
        "TEXT NOT NULL DEFAULT 'unknown' CHECK (competition_gender IN ('men', 'women', 'mixed', 'open', 'unknown'))",
    )
    ensure_column(conn, "normalized_contracts", "gender_source", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "normalized_contracts", "gender_confidence", "TEXT NOT NULL DEFAULT 'low'")
    maybe_backfill_pm_event_sources(conn)
    conn.commit()


def bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def json_hash(value: Any) -> tuple[str, str]:
    text = json_text(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def to_scaled(value: Any) -> int | None:
    decimal = to_decimal(value)
    if decimal is None:
        return None
    return int((decimal * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def scaled_to_decimal(value: int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value) / Decimal(SCALE)


def format_decimal(value: Decimal | None, places: int = 6) -> str:
    if value is None:
        return ""
    quant = Decimal(1).scaleb(-places)
    return format(value.quantize(quant, rounding=ROUND_HALF_UP).normalize(), "f")


def scaled_to_text(value: int | None, places: int = 6) -> str:
    return format_decimal(scaled_to_decimal(value), places)


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name in table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def upsert_pm_event_source(
    conn: sqlite3.Connection,
    event_slug: str,
    source_type: str,
    source_value: str,
    source_label: str = "",
) -> None:
    if not event_slug or not source_type or not source_value:
        return
    now = utc_now()
    conn.execute(
        """
        INSERT INTO pm_event_sources (
            event_slug, source_type, source_value, source_label, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_slug, source_type, source_value) DO UPDATE SET
            source_label=excluded.source_label,
            last_seen_ts=excluded.last_seen_ts
        """,
        (event_slug, source_type, source_value, source_label, now, now),
    )


def record_pm_event_sources(conn: sqlite3.Connection, event_slug: str, event: dict[str, Any], tag_slug: str = "", tag_id: str = "") -> None:
    if tag_slug and not tag_slug.startswith("tag_id:"):
        upsert_pm_event_source(conn, event_slug, "tag_slug", tag_slug, tag_slug)
    if tag_id:
        upsert_pm_event_source(conn, event_slug, "tag_id", str(tag_id), str(tag_id))
    for tag in event.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        slug = str(tag.get("slug") or "")
        tag_id_value = str(tag.get("id") or "")
        label = str(tag.get("label") or "")
        if slug:
            upsert_pm_event_source(conn, event_slug, "tag_slug", slug, label)
        if tag_id_value:
            upsert_pm_event_source(conn, event_slug, "tag_id", tag_id_value, label)
    for series in event.get("series") or []:
        if not isinstance(series, dict):
            continue
        slug = str(series.get("slug") or series.get("ticker") or "")
        series_id = str(series.get("id") or "")
        label = str(series.get("title") or "")
        if slug:
            upsert_pm_event_source(conn, event_slug, "series_slug", slug, label)
        if series_id:
            upsert_pm_event_source(conn, event_slug, "series_id", series_id, label)


def backfill_pm_event_sources(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT event_slug, tag_slug, tag_id, raw_json FROM pm_events"):
        event_slug = str(row["event_slug"] or "")
        try:
            event = json.loads(str(row["raw_json"]))
        except json.JSONDecodeError:
            event = {}
        record_pm_event_sources(
            conn,
            event_slug,
            event if isinstance(event, dict) else {},
            tag_slug=str(row["tag_slug"] or ""),
            tag_id=str(row["tag_id"] or ""),
        )


def maybe_backfill_pm_event_sources(conn: sqlite3.Connection) -> None:
    event_count = int(conn.execute("SELECT COUNT(*) AS count FROM pm_events").fetchone()["count"])
    source_count = int(conn.execute("SELECT COUNT(*) AS count FROM pm_event_sources").fetchone()["count"])
    if event_count > 0 and source_count == 0:
        backfill_pm_event_sources(conn)


def upsert_pm_event(conn: sqlite3.Connection, event: dict[str, Any], tag_slug: str = "", tag_id: str = "") -> None:
    slug = str(event.get("slug") or "")
    if not slug:
        return
    now = utc_now()
    conn.execute(
        """
        INSERT INTO pm_events (
            event_slug, event_id, title, start_date, end_date, active, closed,
            tag_slug, tag_id, raw_json, first_seen_ts, last_seen_ts
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
            str(event.get("id") or ""),
            str(event.get("title") or ""),
            str(event.get("startDate") or ""),
            str(event.get("endDate") or ""),
            bool_int(event.get("active")),
            bool_int(event.get("closed")),
            tag_slug,
            tag_id,
            json_text(event),
            now,
            now,
        ),
    )
    record_pm_event_sources(conn, slug, event, tag_slug=tag_slug, tag_id=tag_id)
    for market in event.get("markets") or []:
        if isinstance(market, dict):
            upsert_pm_market(conn, slug, market)


def upsert_pm_market(conn: sqlite3.Connection, event_slug: str, market: dict[str, Any]) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return
    now = utc_now()
    outcomes = parse_json_list(market.get("outcomes"))
    token_ids = parse_json_list(market.get("clobTokenIds"))
    conn.execute(
        """
        INSERT INTO pm_markets (
            market_id, event_slug, question, market_slug, condition_id, active, closed,
            enable_order_book, outcomes_json, clob_token_ids_json, raw_json, first_seen_ts, last_seen_ts
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
            str(market.get("question") or market.get("groupItemTitle") or ""),
            str(market.get("slug") or ""),
            str(market.get("conditionId") or market.get("condition_id") or ""),
            bool_int(market.get("active")),
            bool_int(market.get("closed")),
            bool_int(market.get("enableOrderBook", True)),
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
                active, closed, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                market_id=excluded.market_id,
                event_slug=excluded.event_slug,
                outcome_index=excluded.outcome_index,
                outcome_name=excluded.outcome_name,
                active=excluded.active,
                closed=excluded.closed,
                last_seen_ts=excluded.last_seen_ts
            """,
            (
                token,
                market_id,
                event_slug,
                index,
                outcome_name,
                bool_int(market.get("active")),
                bool_int(market.get("closed")),
                now,
                now,
            ),
        )


def upsert_ks_market(conn: sqlite3.Connection, series_ticker: str, market: dict[str, Any]) -> None:
    market_ticker = str(market.get("ticker") or "")
    event_ticker = str(market.get("event_ticker") or market_ticker)
    if not market_ticker or not event_ticker:
        return
    now = utc_now()
    event_raw = {
        "event_ticker": event_ticker,
        "series_ticker": series_ticker,
        "title": market.get("title") or "",
        "source": "market_row",
    }
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
        (
            event_ticker,
            series_ticker,
            str(market.get("title") or ""),
            str(market.get("status") or ""),
            json_text(event_raw),
            now,
            now,
        ),
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
            str(market.get("yes_sub_title") or ""),
            str(market.get("status") or ""),
            str(market.get("close_time") or ""),
            to_scaled(market.get("yes_bid_dollars")),
            to_scaled(market.get("yes_ask_dollars")),
            json_text(market),
            now,
            now,
        ),
    )


def parse_pm_orderbook(payload: dict[str, Any]) -> ParsedBook:
    bids = parse_pm_levels(payload.get("bids"), "bid")
    asks = parse_pm_levels(payload.get("asks"), "ask")
    best_bid = max(bids, key=lambda level: level.price) if bids else None
    best_ask = min(asks, key=lambda level: level.price) if asks else None
    source_ts_raw = str(payload.get("timestamp") or "")
    return ParsedBook(
        levels=[*bids, *asks],
        best_bid=best_bid.price if best_bid else None,
        best_bid_size=best_bid.size if best_bid else None,
        best_ask=best_ask.price if best_ask else None,
        best_ask_size=best_ask.size if best_ask else None,
        source_ts_raw=source_ts_raw,
        source_ts_utc=source_ts_to_utc(source_ts_raw),
    )


def parse_pm_levels(levels: Any, side: str) -> list[ParsedLevel]:
    if not isinstance(levels, list):
        return []
    parsed: list[ParsedLevel] = []
    for index, level in enumerate(levels):
        if not isinstance(level, dict):
            continue
        price = to_decimal(level.get("price"))
        size = to_decimal(level.get("size"))
        if price is None or size is None:
            continue
        parsed.append(ParsedLevel(side, price, size, index))
    return parsed


def parse_ks_orderbook(payload: dict[str, Any]) -> ParsedBook:
    book = payload.get("orderbook_fp") or {}
    yes_bids = parse_ks_levels(book.get("yes_dollars"), "yes_bid")
    no_bids = parse_ks_levels(book.get("no_dollars"), "no_bid")
    best_yes_bid = max(yes_bids, key=lambda level: level.price) if yes_bids else None
    best_no_bid = max(no_bids, key=lambda level: level.price) if no_bids else None
    best_ask = (ONE - best_no_bid.price) if best_no_bid else None
    return ParsedBook(
        levels=[*yes_bids, *no_bids],
        best_bid=best_yes_bid.price if best_yes_bid else None,
        best_bid_size=best_yes_bid.size if best_yes_bid else None,
        best_ask=best_ask,
        best_ask_size=best_no_bid.size if best_no_bid else None,
    )


def parse_ks_levels(levels: Any, side: str) -> list[ParsedLevel]:
    if not isinstance(levels, list):
        return []
    parsed: list[ParsedLevel] = []
    for index, level in enumerate(levels):
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = to_decimal(level[0])
        size = to_decimal(level[1])
        if price is None or size is None:
            continue
        order_count = None
        if len(level) > 2:
            try:
                order_count = int(level[2])
            except (TypeError, ValueError):
                order_count = None
        parsed.append(ParsedLevel(side, price, size, index, order_count))
    return parsed


def source_ts_to_utc(value: str) -> str:
    if not value:
        return ""
    try:
        numeric = Decimal(value)
    except InvalidOperation:
        return ""
    if numeric > Decimal("1000000000000"):
        seconds = float(numeric / Decimal("1000"))
    else:
        seconds = float(numeric)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def record_orderbook_success(
    conn: sqlite3.Connection,
    venue: str,
    instrument_id: str,
    payload: dict[str, Any],
    collected_ts_utc: str,
    request_path: str,
    depth: int | None,
    latency_ms: int | None,
) -> int:
    parsed = parse_pm_orderbook(payload) if venue == "pm" else parse_ks_orderbook(payload)
    payload_hash, raw_json = json_hash(payload)
    now = utc_now()
    conn.execute(
        """
        INSERT INTO orderbook_payloads (payload_hash, raw_json, first_seen_ts, last_seen_ts)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(payload_hash) DO UPDATE SET last_seen_ts=excluded.last_seen_ts
        """,
        (payload_hash, raw_json, now, now),
    )
    cursor = conn.execute(
        """
        INSERT INTO orderbook_observations (
            venue, instrument_id, collected_ts_utc, source_ts_utc, source_ts_raw,
            status, error_message, request_path, depth, latency_ms, payload_hash,
            best_bid_scaled, best_bid_size_scaled, best_ask_scaled, best_ask_size_scaled
        ) VALUES (?, ?, ?, ?, ?, 'ok', NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            venue,
            instrument_id,
            collected_ts_utc,
            parsed.source_ts_utc,
            parsed.source_ts_raw,
            request_path,
            depth,
            latency_ms,
            payload_hash,
            to_scaled(parsed.best_bid),
            to_scaled(parsed.best_bid_size),
            to_scaled(parsed.best_ask),
            to_scaled(parsed.best_ask_size),
        ),
    )
    observation_id = int(cursor.lastrowid)
    for level in parsed.levels:
        conn.execute(
            """
            INSERT INTO orderbook_levels (
                observation_id, side, level_index, price_scaled, size_scaled, order_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                level.side,
                level.level_index,
                to_scaled(level.price),
                to_scaled(level.size),
                level.order_count,
            ),
        )
    return observation_id


def record_orderbook_error(
    conn: sqlite3.Connection,
    venue: str,
    instrument_id: str,
    collected_ts_utc: str,
    request_path: str,
    depth: int | None,
    latency_ms: int | None,
    error_message: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO orderbook_observations (
            venue, instrument_id, collected_ts_utc, status, error_message,
            request_path, depth, latency_ms
        ) VALUES (?, ?, ?, 'error', ?, ?, ?, ?)
        """,
        (venue, instrument_id, collected_ts_utc, error_message, request_path, depth, latency_ms),
    )
    return int(cursor.lastrowid)


def record_warning(
    conn: sqlite3.Connection,
    stage: str,
    message: str,
    *,
    category_key: str = "",
    severity: str = "warning",
    context: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO data_quality_warnings (
            ts_utc, stage, category_key, severity, message, context_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (utc_now(), stage, category_key, severity, message, json_text(context or {})),
    )


def active_pm_token_ids(conn: sqlite3.Connection, limit: int = 0) -> list[str]:
    sql = """
        WITH latest AS (
            SELECT instrument_id, MAX(collected_ts_utc) AS last_collected_ts
            FROM orderbook_observations
            WHERE venue = 'pm'
            GROUP BY instrument_id
        ),
        candidates AS (
            SELECT
                token.token_id,
                COALESCE(event.tag_slug, '') AS category_key,
                latest.last_collected_ts,
                CASE WHEN pair.paired_contract_id IS NULL THEN 1 ELSE 0 END AS pair_priority,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(event.tag_slug, '')
                    ORDER BY
                        CASE WHEN latest.last_collected_ts IS NULL THEN 0 ELSE 1 END,
                        latest.last_collected_ts,
                        token.token_id
                ) AS category_rank
            FROM pm_tokens token
            JOIN pm_markets market ON market.market_id = token.market_id
            JOIN pm_events event ON event.event_slug = market.event_slug
            LEFT JOIN latest ON latest.instrument_id = token.token_id
            LEFT JOIN paired_contracts pair ON pair.pm_token_id = token.token_id AND pair.safe_paired = 1
            WHERE token.active = 1
              AND token.closed = 0
              AND market.enable_order_book = 1
              AND market.active = 1
              AND market.closed = 0
        )
        SELECT token_id
        FROM candidates
        ORDER BY
            pair_priority,
            category_rank,
            CASE WHEN last_collected_ts IS NULL THEN 0 ELSE 1 END,
            last_collected_ts,
            category_key,
            token_id
    """
    if limit > 0:
        sql = f"{sql} LIMIT ?"
        return [str(row["token_id"]) for row in conn.execute(sql, (limit,))]
    return [str(row["token_id"]) for row in conn.execute(sql)]


def active_ks_market_tickers(conn: sqlite3.Connection, limit: int = 0) -> list[str]:
    sql = """
        WITH latest AS (
            SELECT instrument_id, MAX(collected_ts_utc) AS last_collected_ts
            FROM orderbook_observations
            WHERE venue = 'ks'
            GROUP BY instrument_id
        ),
        candidates AS (
            SELECT
                market.market_ticker,
                market.series_ticker,
                latest.last_collected_ts,
                CASE WHEN pair.paired_contract_id IS NULL THEN 1 ELSE 0 END AS pair_priority,
                ROW_NUMBER() OVER (
                    PARTITION BY market.series_ticker
                    ORDER BY
                        CASE WHEN latest.last_collected_ts IS NULL THEN 0 ELSE 1 END,
                        latest.last_collected_ts,
                        market.market_ticker
                ) AS series_rank
            FROM ks_markets market
            LEFT JOIN latest ON latest.instrument_id = market.market_ticker
            LEFT JOIN paired_contracts pair ON pair.ks_market_ticker = market.market_ticker AND pair.safe_paired = 1
            WHERE LOWER(COALESCE(market.status, 'open')) IN ('', 'open', 'active')
        )
        SELECT market_ticker
        FROM candidates
        ORDER BY
            pair_priority,
            series_rank,
            CASE WHEN last_collected_ts IS NULL THEN 0 ELSE 1 END,
            last_collected_ts,
            series_ticker,
            market_ticker
    """
    if limit > 0:
        sql = f"{sql} LIMIT ?"
        return [str(row["market_ticker"]) for row in conn.execute(sql, (limit,))]
    return [str(row["market_ticker"]) for row in conn.execute(sql)]


def pm_events_for_tag_slugs(conn: sqlite3.Connection, tag_slugs: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
    if not tag_slugs:
        return []
    placeholders = ",".join("?" for _ in tag_slugs)
    rows = conn.execute(
        f"""
        SELECT event_slug
        FROM pm_events
        WHERE tag_slug IN ({placeholders})
          AND closed = 0
        ORDER BY start_date, event_slug
        LIMIT ?
        """,
        (*tag_slugs, limit),
    ).fetchall()
    return [pm_event_payload(conn, str(row["event_slug"])) for row in rows]


def pm_events_for_tag_ids(conn: sqlite3.Connection, tag_ids: tuple[int, ...], limit: int) -> list[dict[str, Any]]:
    if not tag_ids:
        return []
    normalized_ids = tuple(str(tag_id) for tag_id in tag_ids)
    placeholders = ",".join("?" for _ in normalized_ids)
    rows = conn.execute(
        f"""
        SELECT event_slug
        FROM pm_events
        WHERE tag_id IN ({placeholders})
          AND closed = 0
        ORDER BY start_date, event_slug
        LIMIT ?
        """,
        (*normalized_ids, limit),
    ).fetchall()
    return [pm_event_payload(conn, str(row["event_slug"])) for row in rows]


def pm_event_payload(conn: sqlite3.Connection, event_slug: str) -> dict[str, Any]:
    event_row = conn.execute("SELECT raw_json FROM pm_events WHERE event_slug = ?", (event_slug,)).fetchone()
    if event_row is None:
        return {}
    event = json.loads(str(event_row["raw_json"]))
    markets = [
        json.loads(str(row["raw_json"]))
        for row in conn.execute(
            """
            SELECT raw_json
            FROM pm_markets
            WHERE event_slug = ?
            ORDER BY market_id
            """,
            (event_slug,),
        )
    ]
    event["markets"] = markets
    return event


def ks_markets_for_series(conn: sqlite3.Connection, series_ticker: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT raw_json
        FROM ks_markets
        WHERE series_ticker = ?
          AND LOWER(COALESCE(status, 'open')) IN ('', 'open', 'active')
        ORDER BY event_ticker, market_ticker
        LIMIT ?
        """,
        (series_ticker, limit),
    ).fetchall()
    return [json.loads(str(row["raw_json"])) for row in rows]


@contextlib.contextmanager
def db_backed_core_fetches(conn: sqlite3.Connection) -> Iterator[None]:
    original_fetch_pm = core.fetch_polymarket_events
    original_fetch_pm_by_tag_ids = core.fetch_polymarket_events_by_tag_ids
    original_fetch_ks = core.fetch_kalshi_series_markets
    original_get_json = core.get_json

    def fetch_pm(tag_slugs: tuple[str, ...], limit: int, ascending_values: tuple[str, ...] = ("true", "false")) -> list[dict[str, Any]]:
        return pm_events_for_tag_slugs(conn, tag_slugs, limit)

    def fetch_pm_by_tag_ids(tag_ids: tuple[int, ...], limit: int, ascending_values: tuple[str, ...] = ("true", "false")) -> list[dict[str, Any]]:
        return pm_events_for_tag_ids(conn, tag_ids, limit)

    def fetch_ks(series_ticker: str, limit: int) -> list[dict[str, Any]]:
        return ks_markets_for_series(conn, series_ticker, limit)

    def get_json(base_url: str, path: str, params: dict[str, Any], timeout: int = 20) -> Any:
        if base_url == core.KALSHI_API and path == "/markets":
            series_ticker = str(params.get("series_ticker") or "")
            limit = int(params.get("limit") or 200)
            return {"markets": ks_markets_for_series(conn, series_ticker, limit), "cursor": ""}
        return original_get_json(base_url, path, params, timeout)

    core.fetch_polymarket_events = fetch_pm
    core.fetch_polymarket_events_by_tag_ids = fetch_pm_by_tag_ids
    core.fetch_kalshi_series_markets = fetch_ks
    core.get_json = get_json
    try:
        yield
    finally:
        core.fetch_polymarket_events = original_fetch_pm
        core.fetch_polymarket_events_by_tag_ids = original_fetch_pm_by_tag_ids
        core.fetch_kalshi_series_markets = original_fetch_ks
        core.get_json = original_get_json


def reset_normalized_contracts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM normalized_contracts")


def reset_paired_contracts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM edge_snapshots")
    conn.execute("DELETE FROM paired_contracts")


def upsert_normalized_candidate(conn: sqlite3.Connection, candidate: Any, universe: str = "") -> None:
    venue = str(candidate.source)
    instrument_id = candidate.pm_token_id if venue == "pm" else candidate.ks_market_ticker
    if not instrument_id:
        return
    now = utc_now()
    competition_gender = str(getattr(candidate, "competition_gender", "unknown") or "unknown")
    if competition_gender not in {"men", "women", "mixed", "open", "unknown"}:
        competition_gender = "unknown"
    gender_source = str(getattr(candidate, "gender_source", "") or "")
    gender_confidence = str(getattr(candidate, "gender_confidence", "low") or "low")
    conn.execute(
        """
        INSERT INTO normalized_contracts (
            venue, category_key, universe, event_date, canonical_event_id, market_type,
            competition_gender, gender_source, gender_confidence,
            match_name, outcome_name, entity_key, outcome_key, instrument_id,
            pm_event_slug, pm_market_id, pm_token_id, pm_question,
            ks_series_ticker, ks_event_ticker, ks_market_ticker, ks_title,
            first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(venue, category_key, instrument_id, outcome_key, canonical_event_id)
        DO UPDATE SET
            universe=excluded.universe,
            event_date=excluded.event_date,
            market_type=excluded.market_type,
            competition_gender=excluded.competition_gender,
            gender_source=excluded.gender_source,
            gender_confidence=excluded.gender_confidence,
            match_name=excluded.match_name,
            outcome_name=excluded.outcome_name,
            entity_key=excluded.entity_key,
            pm_event_slug=excluded.pm_event_slug,
            pm_market_id=excluded.pm_market_id,
            pm_token_id=excluded.pm_token_id,
            pm_question=excluded.pm_question,
            ks_series_ticker=excluded.ks_series_ticker,
            ks_event_ticker=excluded.ks_event_ticker,
            ks_market_ticker=excluded.ks_market_ticker,
            ks_title=excluded.ks_title,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            venue,
            str(candidate.category_key),
            universe,
            str(candidate.event_date),
            str(candidate.canonical_event_id),
            str(candidate.market_type),
            competition_gender,
            gender_source,
            gender_confidence,
            str(candidate.match_name),
            str(candidate.outcome),
            str(candidate.entity_key),
            str(candidate.outcome_key),
            str(instrument_id),
            str(candidate.pm_event_slug),
            str(candidate.pm_market_id),
            str(candidate.pm_token_id),
            str(candidate.pm_question),
            str(candidate.ks_series_ticker),
            str(candidate.ks_event_ticker),
            str(candidate.ks_market_ticker),
            str(candidate.ks_title),
            now,
            now,
        ),
    )


def pair_key(pair: core.PairedContract) -> str:
    raw = "|".join((pair.canonical_event_id, pair.market_type, pair.pm_token_id, pair.ks_market_ticker))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upsert_paired_contract(conn: sqlite3.Connection, pair: core.PairedContract) -> int:
    now = utc_now()
    key = pair_key(pair)
    conn.execute(
        """
        INSERT INTO paired_contracts (
            pair_key, universe, category, match_name, event_date, canonical_event_id,
            market_type, pm_yes_outcome, ks_yes_outcome, pm_event_slug, pm_market_id,
            pm_token_id, ks_event_ticker, ks_market_ticker, match_format, schedule_source,
            safe_paired, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(pair_key) DO UPDATE SET
            universe=excluded.universe,
            category=excluded.category,
            match_name=excluded.match_name,
            event_date=excluded.event_date,
            canonical_event_id=excluded.canonical_event_id,
            market_type=excluded.market_type,
            pm_yes_outcome=excluded.pm_yes_outcome,
            ks_yes_outcome=excluded.ks_yes_outcome,
            pm_event_slug=excluded.pm_event_slug,
            pm_market_id=excluded.pm_market_id,
            pm_token_id=excluded.pm_token_id,
            ks_event_ticker=excluded.ks_event_ticker,
            ks_market_ticker=excluded.ks_market_ticker,
            match_format=excluded.match_format,
            schedule_source=excluded.schedule_source,
            safe_paired=1,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            key,
            pair.universe,
            pair.category,
            pair.match_name,
            pair.event_date,
            pair.canonical_event_id,
            pair.market_type,
            pair.pm_yes_outcome,
            pair.ks_yes_outcome,
            pair.pm_event_slug,
            pair.pm_market_id,
            pair.pm_token_id,
            pair.ks_event_ticker,
            pair.ks_market_ticker,
            pair.match_format,
            pair.schedule_source,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT paired_contract_id FROM paired_contracts WHERE pair_key = ?", (key,)).fetchone()
    assert row is not None
    return int(row["paired_contract_id"])


def paired_contract_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                universe, category, match_name, event_date, canonical_event_id,
                market_type, pm_yes_outcome, ks_yes_outcome, pm_event_slug, pm_market_id,
                pm_token_id, ks_event_ticker, ks_market_ticker, match_format,
                schedule_source, safe_paired
            FROM paired_contracts
            ORDER BY universe, event_date, match_name, pm_yes_outcome
            """
        )
    ]


def latest_ok_observation(conn: sqlite3.Connection, venue: str, instrument_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM orderbook_observations
        WHERE venue = ?
          AND instrument_id = ?
          AND status = 'ok'
          AND best_bid_scaled IS NOT NULL
          AND best_ask_scaled IS NOT NULL
          AND best_bid_size_scaled IS NOT NULL
          AND best_ask_size_scaled IS NOT NULL
        ORDER BY collected_ts_utc DESC, observation_id DESC
        LIMIT 1
        """,
        (venue, instrument_id),
    ).fetchone()


def parse_iso_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def seconds_between(left: str, right: str) -> float:
    return abs((parse_iso_ts(left) - parse_iso_ts(right)).total_seconds())


def age_seconds(value: str, now_ts: str) -> float:
    return max(0.0, (parse_iso_ts(now_ts) - parse_iso_ts(value)).total_seconds())


def fee(rate: Decimal, price: Decimal) -> Decimal:
    return rate * price * (ONE - price)


def best_net_decimal(pm_bid: Decimal, pm_ask: Decimal, ks_bid: Decimal, ks_ask: Decimal) -> tuple[str, Decimal, Decimal]:
    cost_a = pm_ask + (ONE - ks_bid)
    net_a = ONE - cost_a - fee(PM_FEE_RATE, pm_ask) - fee(KS_FEE_RATE, ONE - ks_bid)
    cost_b = (ONE - pm_bid) + ks_ask
    net_b = ONE - cost_b - fee(PM_FEE_RATE, ONE - pm_bid) - fee(KS_FEE_RATE, ks_ask)
    if net_a >= net_b:
        return "PM_YES_KS_NO", net_a, cost_a
    return "PM_NO_KS_YES", net_b, cost_b


def compute_edge(
    pm_obs: sqlite3.Row,
    ks_obs: sqlite3.Row,
) -> EdgeResult:
    pm_bid = scaled_to_decimal(pm_obs["best_bid_scaled"])
    pm_ask = scaled_to_decimal(pm_obs["best_ask_scaled"])
    ks_bid = scaled_to_decimal(ks_obs["best_bid_scaled"])
    ks_ask = scaled_to_decimal(ks_obs["best_ask_scaled"])
    if pm_bid is None or pm_ask is None or ks_bid is None or ks_ask is None:
        return EdgeResult(None, None, None, None, None)
    best_leg, net_edge, gross_cost = best_net_decimal(pm_bid, pm_ask, ks_bid, ks_ask)
    pm_bid_size = scaled_to_decimal(pm_obs["best_bid_size_scaled"])
    pm_ask_size = scaled_to_decimal(pm_obs["best_ask_size_scaled"])
    ks_bid_size = scaled_to_decimal(ks_obs["best_bid_size_scaled"])
    ks_ask_size = scaled_to_decimal(ks_obs["best_ask_size_scaled"])
    if best_leg == "PM_YES_KS_NO":
        sizes = [pm_ask_size, ks_bid_size]
    else:
        sizes = [pm_bid_size, ks_ask_size]
    bbo_size = min(size for size in sizes if size is not None) if all(size is not None for size in sizes) else None
    net_profit = net_edge * bbo_size if bbo_size is not None else None
    return EdgeResult(best_leg, net_edge, gross_cost, bbo_size, net_profit)


def compute_edge_snapshots(
    conn: sqlite3.Connection,
    *,
    max_age_seconds: float = 10.0,
    max_skew_seconds: float = 5.0,
    ignore_age: bool = False,
    paired_contract_ids: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    ts_utc = utc_now()
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    params: tuple[Any, ...] = ()
    pair_filter = ""
    if paired_contract_ids:
        placeholders = ",".join("?" for _ in paired_contract_ids)
        pair_filter = f"AND paired_contract_id IN ({placeholders})"
        params = tuple(sorted(paired_contract_ids))
    pairs = conn.execute(
        f"""
        SELECT *
        FROM paired_contracts
        WHERE safe_paired = 1
          {pair_filter}
        ORDER BY universe, event_date, match_name, pm_yes_outcome
        """,
        params,
    ).fetchall()
    for pair in pairs:
        pm_obs = latest_ok_observation(conn, "pm", str(pair["pm_token_id"]))
        ks_obs = latest_ok_observation(conn, "ks", str(pair["ks_market_ticker"]))
        if pm_obs is None or ks_obs is None:
            message = f"missing complete DB orderbook for pair {pair['pair_key']}"
            warnings.append(message)
            record_warning(conn, "compute_edges", message, category_key=str(pair["universe"]))
            continue
        book_age = max(
            age_seconds(str(pm_obs["collected_ts_utc"]), ts_utc),
            age_seconds(str(ks_obs["collected_ts_utc"]), ts_utc),
        )
        skew = seconds_between(str(pm_obs["collected_ts_utc"]), str(ks_obs["collected_ts_utc"]))
        if not ignore_age and (book_age > max_age_seconds or skew > max_skew_seconds):
            message = (
                f"stale/skewed DB orderbook skipped: pair={pair['pair_key']} "
                f"age={book_age:.3f}s skew={skew:.3f}s"
            )
            warnings.append(message)
            record_warning(
                conn,
                "compute_edges",
                message,
                category_key=str(pair["universe"]),
                context={"book_age_seconds": book_age, "snapshot_skew_seconds": skew},
            )
            continue
        edge = compute_edge(pm_obs, ks_obs)
        if edge.net_edge is None:
            message = f"incomplete edge calculation skipped: pair={pair['pair_key']}"
            warnings.append(message)
            record_warning(conn, "compute_edges", message, category_key=str(pair["universe"]))
            continue
        if edge.best_leg_bbo_size is None or edge.best_leg_bbo_size <= Decimal("0"):
            message = (
                f"non-executable BBO depth skipped: pair={pair['pair_key']} "
                f"best_leg={edge.best_leg} bbo_size={edge.best_leg_bbo_size}"
            )
            warnings.append(message)
            record_warning(conn, "compute_edges", message, category_key=str(pair["universe"]))
            continue
        alert = edge.net_edge > Decimal("0")
        conn.execute(
            """
            INSERT INTO edge_snapshots (
                paired_contract_id, ts_utc, pm_observation_id, ks_observation_id,
                pm_bid_scaled, pm_ask_scaled, pm_bid_size_scaled, pm_ask_size_scaled,
                ks_bid_scaled, ks_ask_scaled, ks_bid_size_scaled, ks_ask_size_scaled,
                best_leg, gross_cost_scaled, net_edge_scaled, best_leg_bbo_size_scaled,
                net_profit_at_bbo_scaled, alert, alert_reason, book_age_seconds,
                snapshot_skew_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pair["paired_contract_id"],
                ts_utc,
                pm_obs["observation_id"],
                ks_obs["observation_id"],
                pm_obs["best_bid_scaled"],
                pm_obs["best_ask_scaled"],
                pm_obs["best_bid_size_scaled"],
                pm_obs["best_ask_size_scaled"],
                ks_obs["best_bid_scaled"],
                ks_obs["best_ask_scaled"],
                ks_obs["best_bid_size_scaled"],
                ks_obs["best_ask_size_scaled"],
                edge.best_leg,
                to_scaled(edge.gross_cost),
                to_scaled(edge.net_edge),
                to_scaled(edge.best_leg_bbo_size),
                to_scaled(edge.net_profit_at_bbo),
                "ALERT" if alert else "",
                "net_edge_positive" if alert else "",
                book_age,
                skew,
            ),
        )
        rows.append(edge_csv_row(pair, pm_obs, ks_obs, edge, ts_utc, alert))
    conn.commit()
    return rows, warnings


def edge_csv_row(
    pair: sqlite3.Row,
    pm_obs: sqlite3.Row,
    ks_obs: sqlite3.Row,
    edge: EdgeResult,
    ts_utc: str,
    alert: bool,
) -> dict[str, Any]:
    return {
        "ts_utc": ts_utc,
        "universe": pair["universe"],
        "category": pair["category"],
        "match_name": pair["match_name"],
        "event_date": pair["event_date"],
        "canonical_event_id": pair["canonical_event_id"],
        "market_type": pair["market_type"],
        "pm_yes_outcome": pair["pm_yes_outcome"],
        "ks_yes_outcome": pair["ks_yes_outcome"],
        "pm_bid": scaled_to_text(pm_obs["best_bid_scaled"]),
        "pm_ask": scaled_to_text(pm_obs["best_ask_scaled"]),
        "pm_bid_sz": scaled_to_text(pm_obs["best_bid_size_scaled"]),
        "pm_ask_sz": scaled_to_text(pm_obs["best_ask_size_scaled"]),
        "ks_bid": scaled_to_text(ks_obs["best_bid_scaled"]),
        "ks_ask": scaled_to_text(ks_obs["best_ask_scaled"]),
        "ks_bid_sz": scaled_to_text(ks_obs["best_bid_size_scaled"]),
        "ks_ask_sz": scaled_to_text(ks_obs["best_ask_size_scaled"]),
        "alert": "ALERT" if alert else "",
        "alert_threshold": core.ALERT_THRESHOLD,
        "alert_reason": "net_edge_positive" if alert else "",
        "net_edge": format_decimal(edge.net_edge),
        "best_leg": edge.best_leg or "",
        "gross_cost": format_decimal(edge.gross_cost),
        "best_leg_bbo_size": format_decimal(edge.best_leg_bbo_size),
        "net_profit_at_bbo": format_decimal(edge.net_profit_at_bbo),
        "pm_event_slug": pair["pm_event_slug"],
        "pm_market_id": pair["pm_market_id"],
        "pm_token_id": pair["pm_token_id"],
        "ks_event_ticker": pair["ks_event_ticker"],
        "ks_market_ticker": pair["ks_market_ticker"],
        "match_format": pair["match_format"] or "",
        "schedule_source": pair["schedule_source"] or "",
    }


def fetch_and_record_orderbook(
    conn: sqlite3.Connection,
    *,
    venue: str,
    instrument_id: str,
    fetcher: Callable[[], dict[str, Any]],
    request_path: str,
    depth: int | None,
) -> int:
    collected_ts = utc_now()
    started = time.monotonic()
    try:
        payload = fetcher()
    except Exception as exc:  # noqa: BLE001 - collector records per-instrument errors.
        latency_ms = int((time.monotonic() - started) * 1000)
        observation_id = record_orderbook_error(
            conn,
            venue,
            instrument_id,
            collected_ts,
            request_path,
            depth,
            latency_ms,
            str(exc),
        )
        record_warning(
            conn,
            "collect_orderbooks",
            f"{venue.upper()} orderbook fetch failed: {instrument_id}: {exc}",
            severity="error",
            context={"venue": venue, "instrument_id": instrument_id},
        )
        return observation_id
    latency_ms = int((time.monotonic() - started) * 1000)
    return record_orderbook_success(conn, venue, instrument_id, payload, collected_ts, request_path, depth, latency_ms)


def clear_adapter_caches() -> None:
    for module_name in ("sports_adapters.ufc",):
        try:
            module = __import__(module_name, fromlist=["dummy"])
        except Exception:  # noqa: BLE001 - cache clearing is best-effort.
            continue
        for name in ("discover_pm_ufc", "discover_ks_ufc"):
            function = getattr(module, name, None)
            cache_clear = getattr(function, "cache_clear", None)
            if cache_clear:
                cache_clear()


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "pm_events",
        "pm_event_sources",
        "pm_markets",
        "pm_tokens",
        "ks_events",
        "ks_markets",
        "orderbook_payloads",
        "orderbook_observations",
        "orderbook_levels",
        "normalized_contracts",
        "paired_contracts",
        "edge_snapshots",
        "data_quality_warnings",
    )
    return {
        table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        for table in tables
    }


def pair_to_dict(pair: core.PairedContract) -> dict[str, Any]:
    return asdict(pair)
