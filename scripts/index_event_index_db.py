from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INDEX_DB_PATH = Path("data/index/index_event_index.sqlite")
FORBIDDEN_DB_PATHS = (
    Path("data/old/old_arb_research.sqlite"),
    Path("data/new/new_arb_research.sqlite"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_path_text(path: Path | str) -> str:
    return Path(path).expanduser().as_posix().rstrip("/")


def ensure_index_db_path(db_path: Path | str) -> Path:
    path = Path(db_path)
    text = normalized_path_text(path)
    for forbidden in FORBIDDEN_DB_PATHS:
        forbidden_text = forbidden.as_posix()
        if text == forbidden_text or text.endswith(f"/{forbidden_text}"):
            raise ValueError(
                "event index sync must write to the standalone index DB, not legacy or websocket DB"
            )
    return path


def connect(db_path: Path | str = DEFAULT_INDEX_DB_PATH) -> sqlite3.Connection:
    path = ensure_index_db_path(db_path)
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
        CREATE TABLE IF NOT EXISTS event_index_source_catalog (
            source_key TEXT PRIMARY KEY,
            sport_key TEXT NOT NULL DEFAULT '',
            competition_key TEXT NOT NULL DEFAULT '',
            registry_category_key TEXT NOT NULL DEFAULT '',
            category_key TEXT NOT NULL DEFAULT '',
            universe TEXT NOT NULL DEFAULT '',
            source_status TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            provider_name TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT '',
            requires_api_key INTEGER NOT NULL DEFAULT 0,
            pricing_summary TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_event_index_source_catalog_competition
            ON event_index_source_catalog(sport_key, competition_key, source_status);

        CREATE TABLE IF NOT EXISTS event_index_source_runs (
            source_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            sport_key TEXT NOT NULL DEFAULT '',
            competition_key TEXT NOT NULL DEFAULT '',
            category_key TEXT NOT NULL DEFAULT '',
            universe TEXT NOT NULL DEFAULT '',
            source_status TEXT NOT NULL DEFAULT '',
            provider_name TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            started_ts_utc TEXT NOT NULL,
            finished_ts_utc TEXT,
            events_seen INTEGER NOT NULL DEFAULT 0,
            events_upserted INTEGER NOT NULL DEFAULT 0,
            warnings_count INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_event_index_source_runs_lookup
            ON event_index_source_runs(source_key, sport_key, competition_key, started_ts_utc);

        CREATE TABLE IF NOT EXISTS canonical_events (
            canonical_event_id TEXT PRIMARY KEY,
            sport_key TEXT NOT NULL,
            competition_key TEXT NOT NULL,
            category_key TEXT NOT NULL DEFAULT '',
            universe TEXT NOT NULL DEFAULT '',
            season TEXT NOT NULL DEFAULT '',
            event_date TEXT NOT NULL,
            source_local_date TEXT NOT NULL DEFAULT '',
            start_time_utc TEXT NOT NULL DEFAULT '',
            market_type TEXT NOT NULL DEFAULT '',
            competition_gender TEXT NOT NULL DEFAULT 'unknown',
            event_name TEXT NOT NULL DEFAULT '',
            venue_id TEXT NOT NULL DEFAULT '',
            venue_name TEXT NOT NULL DEFAULT '',
            venue_city TEXT NOT NULL DEFAULT '',
            venue_region TEXT NOT NULL DEFAULT '',
            venue_country TEXT NOT NULL DEFAULT '',
            source_key TEXT NOT NULL DEFAULT '',
            source_event_id TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_confidence TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_canonical_events_competition_date
            ON canonical_events(sport_key, competition_key, event_date);

        CREATE TABLE IF NOT EXISTS canonical_event_sources (
            canonical_event_id TEXT NOT NULL REFERENCES canonical_events(canonical_event_id) ON DELETE CASCADE,
            source_key TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_confidence TEXT NOT NULL DEFAULT '',
            raw_payload_hash TEXT NOT NULL DEFAULT '',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(source_key, source_event_id)
        );

        CREATE TABLE IF NOT EXISTS canonical_participants (
            canonical_event_id TEXT NOT NULL REFERENCES canonical_events(canonical_event_id) ON DELETE CASCADE,
            participant_key TEXT NOT NULL,
            source_participant_id TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL,
            participant_order INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL DEFAULT '',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(canonical_event_id, participant_key)
        );

        CREATE TABLE IF NOT EXISTS source_warnings (
            warning_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            source_key TEXT NOT NULL DEFAULT '',
            sport_key TEXT NOT NULL DEFAULT '',
            competition_key TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'warning',
            message TEXT NOT NULL,
            context_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS index_sports (
            sport_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS index_competitions (
            sport_key TEXT NOT NULL,
            competition_key TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            country TEXT NOT NULL DEFAULT '',
            gender TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(sport_key, competition_key)
        );

        CREATE INDEX IF NOT EXISTS idx_index_competitions_sport_status
            ON index_competitions(sport_key, status);

        CREATE TABLE IF NOT EXISTS index_platform_categories (
            platform TEXT NOT NULL,
            platform_key_type TEXT NOT NULL,
            platform_key TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            parent_key TEXT NOT NULL DEFAULT '',
            observed_event_count INTEGER NOT NULL DEFAULT 0,
            active_event_count INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(platform, platform_key_type, platform_key)
        );

        CREATE INDEX IF NOT EXISTS idx_index_platform_categories_platform
            ON index_platform_categories(platform, platform_key);

        CREATE TABLE IF NOT EXISTS index_platform_category_map (
            platform TEXT NOT NULL,
            platform_key_type TEXT NOT NULL,
            platform_key TEXT NOT NULL,
            sport_key TEXT NOT NULL DEFAULT '',
            competition_key TEXT NOT NULL DEFAULT '',
            mapping_status TEXT NOT NULL DEFAULT 'needs_review'
                CHECK (mapping_status IN ('mapped', 'broad', 'ambiguous', 'ignored', 'needs_review')),
            confidence TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(platform, platform_key_type, platform_key, sport_key, competition_key)
        );

        CREATE INDEX IF NOT EXISTS idx_index_platform_category_map_competition
            ON index_platform_category_map(sport_key, competition_key, platform, mapping_status);

        CREATE TABLE IF NOT EXISTS index_competition_sources (
            sport_key TEXT NOT NULL,
            competition_key TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            requires_api_key INTEGER NOT NULL DEFAULT 0,
            confidence TEXT NOT NULL DEFAULT '',
            source_status TEXT NOT NULL DEFAULT '',
            pricing_summary TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(sport_key, competition_key, source_key)
        );

        CREATE INDEX IF NOT EXISTS idx_index_competition_sources_status
            ON index_competition_sources(source_status, sport_key, competition_key);

        CREATE TABLE IF NOT EXISTS index_wanted_competition_ranges (
            sport_key TEXT NOT NULL,
            competition_key TEXT NOT NULL,
            wanted_from_date TEXT NOT NULL DEFAULT '',
            wanted_to_date TEXT NOT NULL DEFAULT '',
            pm_event_count INTEGER NOT NULL DEFAULT 0,
            ks_event_count INTEGER NOT NULL DEFAULT 0,
            total_event_count INTEGER NOT NULL DEFAULT 0,
            sample_events_json TEXT NOT NULL DEFAULT '[]',
            source_export_path TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(sport_key, competition_key)
        );

        CREATE INDEX IF NOT EXISTS idx_index_wanted_ranges_dates
            ON index_wanted_competition_ranges(wanted_from_date, wanted_to_date, sport_key, competition_key);

        CREATE TABLE IF NOT EXISTS index_manual_schedule_imports (
            import_id INTEGER PRIMARY KEY AUTOINCREMENT,
            input_path TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_accessed_date TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            rows_seen INTEGER NOT NULL DEFAULT 0,
            events_upserted INTEGER NOT NULL DEFAULT 0,
            warnings_count INTEGER NOT NULL DEFAULT 0,
            raw_payload_hash TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            started_ts_utc TEXT NOT NULL,
            finished_ts_utc TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_index_manual_imports_lookup
            ON index_manual_schedule_imports(input_path, source_type, started_ts_utc);

        CREATE TABLE IF NOT EXISTS index_event_evidence (
            canonical_event_id TEXT NOT NULL REFERENCES canonical_events(canonical_event_id) ON DELETE CASCADE,
            source_key TEXT NOT NULL,
            source_name TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_accessed_date TEXT NOT NULL DEFAULT '',
            evidence_status TEXT NOT NULL DEFAULT 'verified',
            confidence TEXT NOT NULL DEFAULT '',
            raw_payload_hash TEXT NOT NULL DEFAULT '',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(canonical_event_id, source_key, source_url)
        );

        CREATE INDEX IF NOT EXISTS idx_index_event_evidence_source
            ON index_event_evidence(source_key, source_type, evidence_status);
        """
    )
    conn.execute("DROP VIEW IF EXISTS index_taxonomy_full_join")
    conn.execute(
        """
        CREATE VIEW index_taxonomy_full_join AS
        WITH
        pm AS (
            SELECT
                sport_key,
                competition_key,
                GROUP_CONCAT(platform_key_type || ':' || platform_key || ':' || mapping_status, ',') AS pm_categories
            FROM index_platform_category_map
            WHERE platform = 'pm'
              AND mapping_status != 'ignored'
              AND sport_key != ''
              AND competition_key != ''
            GROUP BY sport_key, competition_key
        ),
        ks AS (
            SELECT
                sport_key,
                competition_key,
                GROUP_CONCAT(platform_key_type || ':' || platform_key || ':' || mapping_status, ',') AS ks_categories
            FROM index_platform_category_map
            WHERE platform = 'ks'
              AND mapping_status != 'ignored'
              AND sport_key != ''
              AND competition_key != ''
            GROUP BY sport_key, competition_key
        ),
        sources AS (
            SELECT
                sport_key,
                competition_key,
                GROUP_CONCAT(source_key || ':' || source_status, ',') AS source_keys,
                GROUP_CONCAT(source_status, ',') AS source_statuses
            FROM index_competition_sources
            GROUP BY sport_key, competition_key
        )
        SELECT
            'competition' AS row_kind,
            comp.sport_key,
            comp.competition_key,
            comp.display_name AS competition_name,
            comp.status AS competition_status,
            COALESCE(pm.pm_categories, '') AS pm_categories,
            COALESCE(ks.ks_categories, '') AS ks_categories,
            COALESCE(sources.source_keys, '') AS source_keys,
            COALESCE(sources.source_statuses, '') AS source_statuses,
            CASE
                WHEN COALESCE(pm.pm_categories, '') != ''
                 AND COALESCE(ks.ks_categories, '') != ''
                 AND COALESCE(sources.source_statuses, '') LIKE '%source_gap%' THEN 'both_platforms_source_gap'
                WHEN COALESCE(pm.pm_categories, '') != ''
                 AND COALESCE(ks.ks_categories, '') = '' THEN 'pm_only'
                WHEN COALESCE(pm.pm_categories, '') = ''
                 AND COALESCE(ks.ks_categories, '') != '' THEN 'ks_only'
                WHEN COALESCE(pm.pm_categories, '') != ''
                 AND COALESCE(ks.ks_categories, '') != '' THEN 'both_platforms'
                WHEN COALESCE(sources.source_keys, '') != '' THEN 'source_only'
                ELSE 'local_only'
            END AS coverage_status
        FROM index_competitions AS comp
        LEFT JOIN pm
            ON pm.sport_key = comp.sport_key
           AND pm.competition_key = comp.competition_key
        LEFT JOIN ks
            ON ks.sport_key = comp.sport_key
           AND ks.competition_key = comp.competition_key
        LEFT JOIN sources
            ON sources.sport_key = comp.sport_key
           AND sources.competition_key = comp.competition_key

        UNION ALL

        SELECT
            'unmapped_platform_category' AS row_kind,
            '' AS sport_key,
            '' AS competition_key,
            category.display_name AS competition_name,
            '' AS competition_status,
            CASE WHEN category.platform = 'pm' THEN category.platform_key_type || ':' || category.platform_key ELSE '' END AS pm_categories,
            CASE WHEN category.platform = 'ks' THEN category.platform_key_type || ':' || category.platform_key ELSE '' END AS ks_categories,
            '' AS source_keys,
            '' AS source_statuses,
            'needs_review' AS coverage_status
        FROM index_platform_categories AS category
        WHERE NOT EXISTS (
            SELECT 1
            FROM index_platform_category_map AS map
            WHERE map.platform = category.platform
              AND map.platform_key_type = category.platform_key_type
              AND map.platform_key = category.platform_key
        )
        """
    )
    conn.commit()


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def json_hash(value: Any) -> tuple[str, str]:
    text = json_text(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


def normalize_utc_iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parseable = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def source_status(source: Any) -> str:
    return str(getattr(source, "source_status", "") or getattr(source, "status", "") or "")


def source_sport_key(source: Any) -> str:
    return str(getattr(source, "sport_key", "") or getattr(source, "category_key", "") or "")


def source_competition_key(source: Any) -> str:
    return str(getattr(source, "competition_key", "") or getattr(source, "universe", "") or "")


def upsert_event_index_source_catalog(conn: sqlite3.Connection, source: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO event_index_source_catalog (
            source_key, sport_key, competition_key, registry_category_key, category_key,
            universe, source_status, source_type, provider_name, source_url, confidence,
            requires_api_key, pricing_summary, note, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            sport_key=excluded.sport_key,
            competition_key=excluded.competition_key,
            registry_category_key=excluded.registry_category_key,
            category_key=excluded.category_key,
            universe=excluded.universe,
            source_status=excluded.source_status,
            source_type=excluded.source_type,
            provider_name=excluded.provider_name,
            source_url=excluded.source_url,
            confidence=excluded.confidence,
            requires_api_key=excluded.requires_api_key,
            pricing_summary=excluded.pricing_summary,
            note=excluded.note,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(source.source_key),
            source_sport_key(source),
            source_competition_key(source),
            str(getattr(source, "registry_category_key", "") or ""),
            str(getattr(source, "category_key", "") or ""),
            str(getattr(source, "universe", "") or ""),
            source_status(source),
            str(getattr(source, "source_type", "") or ""),
            str(getattr(source, "provider_name", "") or ""),
            str(getattr(source, "source_url", "") or ""),
            str(getattr(source, "confidence", "") or ""),
            bool_int(getattr(source, "requires_api_key", False)),
            str(getattr(source, "pricing_summary", "") or ""),
            str(getattr(source, "note", "") or ""),
            now,
            now,
        ),
    )


def begin_event_index_source_run(conn: sqlite3.Connection, source: Any, *, status: str = "running") -> int:
    cursor = conn.execute(
        """
        INSERT INTO event_index_source_runs (
            source_key, sport_key, competition_key, category_key, universe, source_status,
            provider_name, source_type, source_url, confidence, status, started_ts_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(source.source_key),
            source_sport_key(source),
            source_competition_key(source),
            str(getattr(source, "category_key", "") or ""),
            str(getattr(source, "universe", "") or ""),
            source_status(source),
            str(getattr(source, "provider_name", "") or ""),
            str(getattr(source, "source_type", "") or ""),
            str(getattr(source, "source_url", "") or ""),
            str(getattr(source, "confidence", "") or ""),
            status,
            utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def finish_event_index_source_run(
    conn: sqlite3.Connection,
    source_run_id: int,
    *,
    status: str,
    events_seen: int = 0,
    events_upserted: int = 0,
    warnings_count: int = 0,
    message: str = "",
) -> None:
    conn.execute(
        """
        UPDATE event_index_source_runs
        SET status = ?,
            finished_ts_utc = ?,
            events_seen = ?,
            events_upserted = ?,
            warnings_count = ?,
            message = ?
        WHERE source_run_id = ?
        """,
        (status, utc_now(), events_seen, events_upserted, warnings_count, message, source_run_id),
    )


def record_warning(
    conn: sqlite3.Connection,
    source: Any,
    message: str,
    *,
    severity: str = "warning",
    context: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO source_warnings (
            ts_utc, source_key, sport_key, competition_key, severity, message, context_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            str(getattr(source, "source_key", "") or ""),
            source_sport_key(source),
            source_competition_key(source),
            severity,
            message,
            json_text(context or {}),
        ),
    )


def upsert_index_sport(conn: sqlite3.Connection, sport: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO index_sports (
            sport_key, display_name, status, notes, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(sport_key) DO UPDATE SET
            display_name=excluded.display_name,
            status=excluded.status,
            notes=excluded.notes,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(sport, "sport_key", "") or getattr(sport, "key", "") or ""),
            str(getattr(sport, "display_name", "") or getattr(sport, "label", "") or ""),
            str(getattr(sport, "status", "") or ""),
            str(getattr(sport, "notes", "") or ""),
            now,
            now,
        ),
    )


def upsert_index_competition(conn: sqlite3.Connection, competition: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO index_competitions (
            sport_key, competition_key, display_name, country, gender, level,
            status, notes, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sport_key, competition_key) DO UPDATE SET
            display_name=excluded.display_name,
            country=excluded.country,
            gender=excluded.gender,
            level=excluded.level,
            status=excluded.status,
            notes=excluded.notes,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(competition, "sport_key", "") or ""),
            str(getattr(competition, "competition_key", "") or ""),
            str(getattr(competition, "display_name", "") or ""),
            str(getattr(competition, "country", "") or ""),
            str(getattr(competition, "gender", "") or ""),
            str(getattr(competition, "level", "") or ""),
            str(getattr(competition, "status", "") or ""),
            str(getattr(competition, "notes", "") or ""),
            now,
            now,
        ),
    )


def upsert_index_platform_category(conn: sqlite3.Connection, category: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO index_platform_categories (
            platform, platform_key_type, platform_key, display_name, parent_key,
            observed_event_count, active_event_count, raw_json, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, platform_key_type, platform_key) DO UPDATE SET
            display_name=excluded.display_name,
            parent_key=excluded.parent_key,
            observed_event_count=excluded.observed_event_count,
            active_event_count=excluded.active_event_count,
            raw_json=excluded.raw_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(category, "platform", "") or ""),
            str(getattr(category, "platform_key_type", "") or ""),
            str(getattr(category, "platform_key", "") or ""),
            str(getattr(category, "display_name", "") or ""),
            str(getattr(category, "parent_key", "") or ""),
            int(getattr(category, "observed_event_count", 0) or 0),
            int(getattr(category, "active_event_count", 0) or 0),
            json_text(getattr(category, "raw_payload", None) or getattr(category, "raw_json", None) or {}),
            now,
            now,
        ),
    )


def upsert_index_platform_category_map(conn: sqlite3.Connection, mapping: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO index_platform_category_map (
            platform, platform_key_type, platform_key, sport_key, competition_key,
            mapping_status, confidence, notes, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, platform_key_type, platform_key, sport_key, competition_key) DO UPDATE SET
            mapping_status=excluded.mapping_status,
            confidence=excluded.confidence,
            notes=excluded.notes,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(mapping, "platform", "") or ""),
            str(getattr(mapping, "platform_key_type", "") or ""),
            str(getattr(mapping, "platform_key", "") or ""),
            str(getattr(mapping, "sport_key", "") or ""),
            str(getattr(mapping, "competition_key", "") or ""),
            str(getattr(mapping, "mapping_status", "") or "needs_review"),
            str(getattr(mapping, "confidence", "") or ""),
            str(getattr(mapping, "notes", "") or ""),
            now,
            now,
        ),
    )


def upsert_index_competition_source(conn: sqlite3.Connection, source: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO index_competition_sources (
            sport_key, competition_key, source_key, source_type, source_url,
            requires_api_key, confidence, source_status, pricing_summary, notes,
            first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sport_key, competition_key, source_key) DO UPDATE SET
            source_type=excluded.source_type,
            source_url=excluded.source_url,
            requires_api_key=excluded.requires_api_key,
            confidence=excluded.confidence,
            source_status=excluded.source_status,
            pricing_summary=excluded.pricing_summary,
            notes=excluded.notes,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(source, "sport_key", "") or ""),
            str(getattr(source, "competition_key", "") or ""),
            str(getattr(source, "source_key", "") or ""),
            str(getattr(source, "source_type", "") or ""),
            str(getattr(source, "source_url", "") or ""),
            bool_int(getattr(source, "requires_api_key", False)),
            str(getattr(source, "confidence", "") or ""),
            str(getattr(source, "source_status", "") or getattr(source, "status", "") or ""),
            str(getattr(source, "pricing_summary", "") or ""),
            str(getattr(source, "notes", "") or getattr(source, "note", "") or ""),
            now,
            now,
        ),
    )


def upsert_index_wanted_competition_range(conn: sqlite3.Connection, wanted: Any) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO index_wanted_competition_ranges (
            sport_key, competition_key, wanted_from_date, wanted_to_date,
            pm_event_count, ks_event_count, total_event_count, sample_events_json,
            source_export_path, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sport_key, competition_key) DO UPDATE SET
            wanted_from_date=excluded.wanted_from_date,
            wanted_to_date=excluded.wanted_to_date,
            pm_event_count=excluded.pm_event_count,
            ks_event_count=excluded.ks_event_count,
            total_event_count=excluded.total_event_count,
            sample_events_json=excluded.sample_events_json,
            source_export_path=excluded.source_export_path,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(wanted, "sport_key", "") or ""),
            str(getattr(wanted, "competition_key", "") or ""),
            str(getattr(wanted, "wanted_from_date", "") or ""),
            str(getattr(wanted, "wanted_to_date", "") or ""),
            int(getattr(wanted, "pm_event_count", 0) or 0),
            int(getattr(wanted, "ks_event_count", 0) or 0),
            int(getattr(wanted, "total_event_count", 0) or 0),
            json_text(getattr(wanted, "sample_events", None) or getattr(wanted, "sample_events_json", None) or []),
            str(getattr(wanted, "source_export_path", "") or ""),
            now,
            now,
        ),
    )


def begin_manual_schedule_import(conn: sqlite3.Connection, *, input_path: str, source_name: str, source_url: str, source_type: str, source_accessed_date: str, raw_payload: Any = None) -> int:
    raw_payload_hash, _raw_payload_json = json_hash(raw_payload or {})
    cursor = conn.execute(
        """
        INSERT INTO index_manual_schedule_imports (
            input_path, source_name, source_url, source_type, source_accessed_date,
            status, raw_payload_hash, started_ts_utc
        ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
        """,
        (
            input_path,
            source_name,
            source_url,
            source_type,
            source_accessed_date,
            raw_payload_hash,
            utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def finish_manual_schedule_import(
    conn: sqlite3.Connection,
    import_id: int,
    *,
    status: str,
    rows_seen: int = 0,
    events_upserted: int = 0,
    warnings_count: int = 0,
    message: str = "",
) -> None:
    conn.execute(
        """
        UPDATE index_manual_schedule_imports
        SET status = ?,
            rows_seen = ?,
            events_upserted = ?,
            warnings_count = ?,
            message = ?,
            finished_ts_utc = ?
        WHERE import_id = ?
        """,
        (status, rows_seen, events_upserted, warnings_count, message, utc_now(), import_id),
    )


def upsert_index_event_evidence(conn: sqlite3.Connection, evidence: Any) -> None:
    now = utc_now()
    raw_payload_hash, raw_payload_json = json_hash(getattr(evidence, "raw_payload", None) or {})
    conn.execute(
        """
        INSERT INTO index_event_evidence (
            canonical_event_id, source_key, source_name, source_type, source_url,
            source_accessed_date, evidence_status, confidence, raw_payload_hash,
            raw_payload_json, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_event_id, source_key, source_url) DO UPDATE SET
            source_name=excluded.source_name,
            source_type=excluded.source_type,
            source_accessed_date=excluded.source_accessed_date,
            evidence_status=excluded.evidence_status,
            confidence=excluded.confidence,
            raw_payload_hash=excluded.raw_payload_hash,
            raw_payload_json=excluded.raw_payload_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(getattr(evidence, "canonical_event_id", "") or ""),
            str(getattr(evidence, "source_key", "") or ""),
            str(getattr(evidence, "source_name", "") or ""),
            str(getattr(evidence, "source_type", "") or ""),
            str(getattr(evidence, "source_url", "") or ""),
            str(getattr(evidence, "source_accessed_date", "") or ""),
            str(getattr(evidence, "evidence_status", "") or "verified"),
            str(getattr(evidence, "confidence", "") or ""),
            raw_payload_hash,
            raw_payload_json,
            now,
            now,
        ),
    )


def upsert_indexed_event(conn: sqlite3.Connection, event: Any) -> None:
    now = utc_now()
    raw_payload_hash, raw_payload_json = json_hash(getattr(event, "raw_payload", None) or {})
    event_date = str(getattr(event, "event_date", "") or "")
    start_time_utc = normalize_utc_iso(getattr(event, "start_time_utc", ""))
    sport_key = str(getattr(event, "sport_key", "") or getattr(event, "category_key", "") or "")
    competition_key = str(getattr(event, "competition_key", "") or getattr(event, "universe", "") or "")
    event_name = str(getattr(event, "event_name", "") or getattr(event, "match_name", "") or "")
    season = str(getattr(event, "season", "") or (event_date[:4] if len(event_date) >= 4 else ""))
    source_local_date = str(getattr(event, "source_local_date", "") or event_date)
    conn.execute(
        """
        INSERT INTO canonical_events (
            canonical_event_id, sport_key, competition_key, category_key, universe, season,
            event_date, source_local_date, start_time_utc, market_type, competition_gender,
            event_name, venue_id, venue_name, venue_city, venue_region, venue_country,
            source_key, source_event_id, source_type, source_url, source_confidence,
            first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_event_id) DO UPDATE SET
            sport_key=excluded.sport_key,
            competition_key=excluded.competition_key,
            category_key=excluded.category_key,
            universe=excluded.universe,
            season=excluded.season,
            event_date=excluded.event_date,
            source_local_date=excluded.source_local_date,
            start_time_utc=excluded.start_time_utc,
            market_type=excluded.market_type,
            competition_gender=excluded.competition_gender,
            event_name=excluded.event_name,
            venue_id=excluded.venue_id,
            venue_name=excluded.venue_name,
            venue_city=excluded.venue_city,
            venue_region=excluded.venue_region,
            venue_country=excluded.venue_country,
            source_key=excluded.source_key,
            source_event_id=excluded.source_event_id,
            source_type=excluded.source_type,
            source_url=excluded.source_url,
            source_confidence=excluded.source_confidence,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(event.canonical_event_id),
            sport_key,
            competition_key,
            str(getattr(event, "category_key", "") or ""),
            str(getattr(event, "universe", "") or ""),
            season,
            event_date,
            source_local_date,
            start_time_utc,
            str(getattr(event, "market_type", "") or ""),
            str(getattr(event, "competition_gender", "") or "unknown"),
            event_name,
            str(getattr(event, "venue_id", "") or ""),
            str(getattr(event, "venue_name", "") or ""),
            str(getattr(event, "venue_city", "") or ""),
            str(getattr(event, "venue_region", "") or ""),
            str(getattr(event, "venue_country", "") or ""),
            str(event.source_key),
            str(event.source_event_id),
            str(event.source_type),
            str(getattr(event, "source_url", "") or ""),
            str(event.source_confidence),
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO canonical_event_sources (
            canonical_event_id, source_key, source_event_id, source_type, source_url,
            source_confidence, raw_payload_hash, raw_payload_json, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key, source_event_id) DO UPDATE SET
            canonical_event_id=excluded.canonical_event_id,
            source_type=excluded.source_type,
            source_url=excluded.source_url,
            source_confidence=excluded.source_confidence,
            raw_payload_hash=excluded.raw_payload_hash,
            raw_payload_json=excluded.raw_payload_json,
            last_seen_ts=excluded.last_seen_ts
        """,
        (
            str(event.canonical_event_id),
            str(event.source_key),
            str(event.source_event_id),
            str(event.source_type),
            str(getattr(event, "source_url", "") or ""),
            str(event.source_confidence),
            raw_payload_hash,
            raw_payload_json,
            now,
            now,
        ),
    )
    for index, participant in enumerate(tuple(getattr(event, "participants", ()) or ())):
        participant_key = str(getattr(participant, "participant_key", "") or "")
        if not participant_key:
            continue
        aliases = sorted({str(alias) for alias in (getattr(participant, "aliases", ()) or ()) if str(alias)})
        display_name = str(getattr(participant, "display_name", "") or "")
        if display_name and display_name not in aliases:
            aliases.append(display_name)
        conn.execute(
            """
            INSERT INTO canonical_participants (
                canonical_event_id, participant_key, source_participant_id, display_name,
                participant_order, role, aliases_json, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_event_id, participant_key) DO UPDATE SET
                source_participant_id=excluded.source_participant_id,
                display_name=excluded.display_name,
                participant_order=excluded.participant_order,
                role=excluded.role,
                aliases_json=excluded.aliases_json,
                last_seen_ts=excluded.last_seen_ts
            """,
            (
                str(event.canonical_event_id),
                participant_key,
                str(getattr(participant, "source_participant_id", "") or ""),
                display_name,
                int(getattr(participant, "order", index) or index),
                str(getattr(participant, "role", "") or ""),
                json_text(aliases),
                now,
                now,
            ),
        )


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = (
        "index_sports",
        "index_competitions",
        "index_platform_categories",
        "index_platform_category_map",
        "index_competition_sources",
        "event_index_source_catalog",
        "event_index_source_runs",
        "canonical_events",
        "canonical_event_sources",
        "canonical_participants",
        "source_warnings",
        "index_wanted_competition_ranges",
        "index_manual_schedule_imports",
        "index_event_evidence",
    )
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }
