from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import old_market_db


INDEX_DB_PATH = Path("data/index/index_event_index.sqlite")
OLD_DB_PATH = Path("data/old/old_arb_research.sqlite")

STRICT_SOURCE_TYPES = {"official_api", "official_site", "static_official_fixture"}
VETTED_SOURCE_TYPES = {"vetted_public_api", "vetted_provider_api", "paid_provider_api", "enterprise_provider_api"}
FORBIDDEN_SOURCE_RE = re.compile(r"\b(pm|polymarket|ks|kalshi)\b", re.IGNORECASE)
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
EXCLUDED_MARKET_RE = re.compile(
    r"\b("
    r"future|futures|champion|championship|tournament|series winner|"
    r"playoff|postseason|super bowl|stanley cup|world series|"
    r"mvp|award|draft|free agency|trade|transfer|roster|"
    r"home runs?|rbis?|strikeouts?|stolen bases?|statistical leader|"
    r"golden boot|golden ball|spread|handicap|total|over/under|"
    r"map \d+|set \d+|inning|period|quarter"
    r")\b",
    re.IGNORECASE,
)
PLACEHOLDER_OUTCOMES = {
    "yes",
    "no",
    "draw",
    "tie",
    "other",
    "over",
    "under",
    "above",
    "below",
}


@dataclass(frozen=True)
class SourceAudit:
    forbidden_count: int
    distribution: list[dict[str, Any]]


@dataclass(frozen=True)
class IndexParticipant:
    participant_key: str
    display_name: str
    alias_keys: frozenset[str]


@dataclass(frozen=True)
class IndexEvent:
    canonical_event_id: str
    sport_key: str
    competition_key: str
    event_date: str
    start_time_utc: str
    market_type: str
    source_type: str
    source_confidence: str
    participants: tuple[IndexParticipant, ...]


@dataclass(frozen=True)
class PlatformCandidate:
    platform: str
    platform_event_id: str
    platform_market_id: str
    sport_key: str
    competition_key: str
    event_date: str
    start_time_utc: str
    market_type: str
    participant_names: tuple[str, str]
    source_name: str
    context: dict[str, Any]


@dataclass(frozen=True)
class MatchResult:
    status: str
    reason: str
    canonical_event_id: str = ""
    confidence: str = ""
    participant_map: dict[str, str] | None = None
    message: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def alias_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def words(value: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value or ""))
    folded = unicodedata.normalize("NFKD", spaced).encode("ascii", "ignore").decode("ascii")
    return [token for token in re.findall(r"[A-Za-z0-9]+", folded) if token]


def expanded_alias_keys(value: str) -> set[str]:
    values = {str(value or "").strip()}
    tokens = words(value)
    if tokens:
        values.add(" ".join(tokens))
        values.update(tokens)
    if len(tokens) >= 2:
        city = " ".join(tokens[:-1])
        nickname = tokens[-1]
        values.add(city)
        values.add(nickname)
        values.add(f"{city} {nickname[:1]}")
        values.add("".join(token[:1] for token in tokens))
        if len(tokens) >= 3:
            last_two = tokens[-2:]
            city_before_last_two = " ".join(tokens[:-2])
            values.add(" ".join(last_two))
            if city_before_last_two:
                values.add(f"{city_before_last_two} {''.join(token[:1] for token in last_two)}")
    return {alias_key(item) for item in values if alias_key(item)}


def source_is_forbidden(*values: str) -> bool:
    return any(FORBIDDEN_SOURCE_RE.search(str(value or "")) for value in values)


def audit_index_sources(index_conn: sqlite3.Connection) -> SourceAudit:
    forbidden = index_conn.execute(
        """
        SELECT COUNT(*)
        FROM canonical_events
        WHERE lower(source_key) LIKE '%pm%'
           OR lower(source_key) LIKE '%polymarket%'
           OR lower(source_key) LIKE '%ks%'
           OR lower(source_key) LIKE '%kalshi%'
           OR lower(source_type) LIKE '%pm%'
           OR lower(source_type) LIKE '%polymarket%'
           OR lower(source_type) LIKE '%ks%'
           OR lower(source_type) LIKE '%kalshi%'
        """
    ).fetchone()[0]
    distribution = [
        dict(row)
        for row in index_conn.execute(
            """
            SELECT source_key, source_type, source_confidence, COUNT(*) AS event_count
            FROM canonical_events
            GROUP BY source_key, source_type, source_confidence
            ORDER BY source_type, source_key
            """
        ).fetchall()
    ]
    return SourceAudit(forbidden_count=int(forbidden), distribution=distribution)


def ensure_old_index_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS old_index_sync_runs (
            sync_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_ts_utc TEXT NOT NULL,
            finished_ts_utc TEXT,
            index_db_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            canonical_events_seen INTEGER NOT NULL DEFAULT 0,
            canonical_events_upserted INTEGER NOT NULL DEFAULT 0,
            participants_seen INTEGER NOT NULL DEFAULT 0,
            participants_inserted INTEGER NOT NULL DEFAULT 0,
            event_sources_seen INTEGER NOT NULL DEFAULT 0,
            event_sources_inserted INTEGER NOT NULL DEFAULT 0,
            platform_maps_seen INTEGER NOT NULL DEFAULT 0,
            platform_maps_inserted INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS old_index_canonical_participants (
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

        CREATE TABLE IF NOT EXISTS old_index_event_sources (
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

        CREATE TABLE IF NOT EXISTS old_index_platform_category_map (
            platform TEXT NOT NULL,
            platform_key_type TEXT NOT NULL,
            platform_key TEXT NOT NULL,
            sport_key TEXT NOT NULL DEFAULT '',
            competition_key TEXT NOT NULL DEFAULT '',
            mapping_status TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            first_seen_ts TEXT NOT NULL,
            last_seen_ts TEXT NOT NULL,
            PRIMARY KEY(platform, platform_key_type, platform_key, sport_key, competition_key)
        );

        CREATE TABLE IF NOT EXISTS old_index_event_mapping_diagnostics (
            diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            platform TEXT NOT NULL,
            sport_key TEXT NOT NULL DEFAULT '',
            competition_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            eligible INTEGER NOT NULL DEFAULT 0,
            event_date TEXT NOT NULL DEFAULT '',
            market_type TEXT NOT NULL DEFAULT '',
            canonical_event_id TEXT NOT NULL DEFAULT '',
            platform_event_id TEXT NOT NULL DEFAULT '',
            platform_market_id TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_old_index_diag_platform
            ON old_index_event_mapping_diagnostics(platform, status, reason);

        CREATE INDEX IF NOT EXISTS idx_old_index_participants_lookup
            ON old_index_canonical_participants(canonical_event_id, participant_order);
        """
    )


def begin_sync_run(conn: sqlite3.Connection, index_db_path: Path | str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO old_index_sync_runs (started_ts_utc, index_db_path, status)
        VALUES (?, ?, 'running')
        """,
        (utc_now(), str(index_db_path)),
    )
    return int(cursor.lastrowid)


def finish_sync_run(conn: sqlite3.Connection, sync_run_id: int, counts: dict[str, int], *, status: str, message: str = "") -> None:
    conn.execute(
        """
        UPDATE old_index_sync_runs
        SET finished_ts_utc = ?,
            status = ?,
            canonical_events_seen = ?,
            canonical_events_upserted = ?,
            participants_seen = ?,
            participants_inserted = ?,
            event_sources_seen = ?,
            event_sources_inserted = ?,
            platform_maps_seen = ?,
            platform_maps_inserted = ?,
            message = ?
        WHERE sync_run_id = ?
        """,
        (
            utc_now(),
            status,
            counts.get("canonical_events_seen", 0),
            counts.get("canonical_events_upserted", 0),
            counts.get("participants_seen", 0),
            counts.get("participants_inserted", 0),
            counts.get("event_sources_seen", 0),
            counts.get("event_sources_inserted", 0),
            counts.get("platform_maps_seen", 0),
            counts.get("platform_maps_inserted", 0),
            message,
            sync_run_id,
        ),
    )


def record_diagnostic(conn: sqlite3.Connection, candidate: PlatformCandidate | None, result: MatchResult, *, eligible: bool) -> None:
    conn.execute(
        """
        INSERT INTO old_index_event_mapping_diagnostics (
            ts_utc, platform, sport_key, competition_key, status, reason, eligible,
            event_date, market_type, canonical_event_id, platform_event_id,
            platform_market_id, source_name, message, context_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            candidate.platform if candidate else "",
            candidate.sport_key if candidate else "",
            candidate.competition_key if candidate else "",
            result.status,
            result.reason,
            1 if eligible else 0,
            candidate.event_date if candidate else "",
            candidate.market_type if candidate else "",
            result.canonical_event_id,
            candidate.platform_event_id if candidate else "",
            candidate.platform_market_id if candidate else "",
            candidate.source_name if candidate else "",
            result.message,
            json_text({**(candidate.context if candidate else {}), "participant_map": result.participant_map or {}}),
        ),
    )


def mapped_category(conn: sqlite3.Connection, platform: str, key_type: str, key: str) -> tuple[str, str, str] | None:
    row = conn.execute(
        """
        SELECT sport_key, competition_key, mapping_status
        FROM old_index_platform_category_map
        WHERE platform = ?
          AND platform_key_type = ?
          AND platform_key = ?
          AND mapping_status IN ('mapped', 'broad', 'ambiguous')
          AND sport_key != ''
          AND competition_key != ''
        ORDER BY CASE mapping_status WHEN 'mapped' THEN 0 WHEN 'broad' THEN 1 ELSE 2 END
        LIMIT 1
        """,
        (platform, key_type, key),
    ).fetchone()
    return (row["sport_key"], row["competition_key"], row["mapping_status"]) if row else None


def load_index_events_from_old(conn: sqlite3.Connection) -> list[IndexEvent]:
    participant_rows = conn.execute(
        """
        SELECT canonical_event_id, participant_key, display_name, aliases_json
        FROM old_index_canonical_participants
        ORDER BY canonical_event_id, participant_order, participant_key
        """
    ).fetchall()
    by_event: dict[str, list[IndexParticipant]] = {}
    for row in participant_rows:
        aliases = {str(row["display_name"] or "")}
        aliases.update(str(value) for value in parse_json_list(row["aliases_json"]) if str(value or "").strip())
        alias_keys = set()
        for alias in aliases:
            alias_keys.update(expanded_alias_keys(alias))
        by_event.setdefault(row["canonical_event_id"], []).append(
            IndexParticipant(
                participant_key=row["participant_key"],
                display_name=row["display_name"],
                alias_keys=frozenset(alias_keys),
            )
        )
    events = []
    for row in conn.execute(
        """
        SELECT canonical_event_id, sport_key, competition_key, event_date, start_time_utc,
               market_type, source_type, source_confidence
        FROM canonical_events
        WHERE sport_key != ''
          AND competition_key != ''
          AND event_date != ''
          AND source_type NOT LIKE '%polymarket%'
          AND source_type NOT LIKE '%kalshi%'
        ORDER BY event_date, sport_key, competition_key
        """
    ).fetchall():
        participants = tuple(by_event.get(row["canonical_event_id"], ()))
        if len(participants) != 2:
            continue
        events.append(
            IndexEvent(
                canonical_event_id=row["canonical_event_id"],
                sport_key=row["sport_key"],
                competition_key=row["competition_key"],
                event_date=row["event_date"],
                start_time_utc=row["start_time_utc"],
                market_type=row["market_type"],
                source_type=row["source_type"],
                source_confidence=row["source_confidence"],
                participants=participants,
            )
        )
    return events


def source_quality_compatible(event: IndexEvent, *, strict: bool = False) -> bool:
    if source_is_forbidden(event.source_type):
        return False
    if strict:
        return event.source_type in STRICT_SOURCE_TYPES
    return event.source_type in STRICT_SOURCE_TYPES | VETTED_SOURCE_TYPES


def times_compatible(candidate_time: str, index_time: str, tolerance_minutes: int = 15) -> bool:
    if not candidate_time or not index_time:
        return True
    try:
        left = datetime.fromisoformat(candidate_time.replace("Z", "+00:00")).astimezone(timezone.utc)
        right = datetime.fromisoformat(index_time.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return candidate_time == index_time
    return abs((left - right).total_seconds()) <= tolerance_minutes * 60


def participant_set_match(candidate_names: tuple[str, str], event: IndexEvent) -> tuple[bool, str, dict[str, str]]:
    candidate_to_participant: dict[str, str] = {}
    used_participants: set[str] = set()
    for name in candidate_names:
        name_keys = expanded_alias_keys(name)
        matches = [
            participant
            for participant in event.participants
            if participant.alias_keys.intersection(name_keys)
        ]
        if not matches:
            return False, "participant_mismatch", {}
        if len(matches) > 1:
            return False, "ambiguous_participant_alias", {}
        participant = matches[0]
        if participant.participant_key in used_participants:
            return False, "participant_mismatch", {}
        candidate_to_participant[name] = participant.participant_key
        used_participants.add(participant.participant_key)
    if len(used_participants) != 2:
        return False, "participant_mismatch", {}
    return True, "matched", candidate_to_participant


def match_candidate(
    candidate: PlatformCandidate,
    index_events: Iterable[IndexEvent],
    *,
    strict_sources: bool = False,
) -> MatchResult:
    competition_events: list[IndexEvent] = []
    date_events: list[IndexEvent] = []
    time_events: list[IndexEvent] = []
    candidates: list[tuple[IndexEvent, dict[str, str]]] = []
    for event in index_events:
        if event.sport_key != candidate.sport_key:
            continue
        if event.competition_key != candidate.competition_key:
            continue
        if candidate.market_type and event.market_type and event.market_type != candidate.market_type:
            continue
        if not source_quality_compatible(event, strict=strict_sources):
            continue
        competition_events.append(event)
        if event.event_date != candidate.event_date:
            continue
        date_events.append(event)
        if not times_compatible(candidate.start_time_utc, event.start_time_utc):
            continue
        time_events.append(event)
        matched, reason, participant_map = participant_set_match(candidate.participant_names, event)
        if matched:
            candidates.append((event, participant_map))
        elif reason == "ambiguous_participant_alias":
            return MatchResult("unmapped", reason, message="participant alias matched multiple indexed participants")
    if len(candidates) == 1:
        event, participant_map = candidates[0]
        confidence = "high" if event.source_type in STRICT_SOURCE_TYPES else "provisional"
        return MatchResult("mapped", "matched", event.canonical_event_id, confidence, participant_map)
    if not competition_events:
        return MatchResult("unmapped", "no_index_competition")
    if not date_events:
        return MatchResult("unmapped", "no_index_date")
    if not time_events:
        return MatchResult("unmapped", "time_mismatch")
    if not candidates:
        return MatchResult("unmapped", "participant_mismatch_same_date")
    return MatchResult("unmapped", "ambiguous_index_event", message=f"{len(candidates)} index events matched")


def date_from_slug_or_text(*values: str) -> str:
    for value in values:
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(value or ""))
        if match:
            return match.group(1)
    for value in values:
        text = str(value or "").strip()
        if len(text) >= 10 and re.match(r"20\d{2}-\d{2}-\d{2}", text):
            return text[:10]
    return ""


def date_from_ks_ticker(*values: str) -> str:
    for value in values:
        match = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", str(value or "").upper())
        if not match:
            continue
        month = MONTHS.get(match.group(2))
        if month:
            return f"{2000 + int(match.group(1)):04d}-{month:02d}-{int(match.group(3)):02d}"
    return date_from_slug_or_text(*values)


def market_type_for_sport(sport_key: str, competition_key: str) -> str:
    if sport_key in {"baseball", "basketball", "football", "hockey", "lacrosse"}:
        return "game_winner"
    if sport_key in {"tennis", "esports", "cricket", "rugby", "pickleball", "table_tennis"}:
        return "match_winner"
    if sport_key in {"boxing", "mma"}:
        return "fighter_winner"
    if sport_key == "soccer" and competition_key == "world_cup":
        return "3_way_moneyline"
    return "unknown"


def is_excluded_market(text: str) -> bool:
    return bool(EXCLUDED_MARKET_RE.search(text or ""))


def valid_two_participants(values: list[Any]) -> tuple[str, str] | None:
    participants = [str(value or "").strip() for value in values if str(value or "").strip()]
    if len(participants) != 2:
        return None
    normalized = [alias_key(value) for value in participants]
    if len(set(normalized)) != 2:
        return None
    if any(value in PLACEHOLDER_OUTCOMES for value in normalized):
        return None
    return (participants[0], participants[1])


def upsert_pm_map(conn: sqlite3.Connection, candidate: PlatformCandidate, result: MatchResult) -> None:
    old_market_db.upsert_pm_canonical_event_map_direct(
        conn,
        pm_event_slug=candidate.platform_event_id,
        canonical_event_id=result.canonical_event_id,
        pm_market_id=candidate.platform_market_id,
        mapping_source="index_snapshot_match",
        confidence=result.confidence,
    )
    old_market_db.upsert_canonical_event_name(conn, result.canonical_event_id, "pm", candidate.source_name)


def upsert_ks_map(conn: sqlite3.Connection, candidate: PlatformCandidate, result: MatchResult) -> None:
    old_market_db.upsert_ks_canonical_event_map_direct(
        conn,
        ks_event_ticker=candidate.platform_event_id,
        canonical_event_id=result.canonical_event_id,
        ks_series_ticker=candidate.context.get("series_ticker", ""),
        mapping_source="index_snapshot_match",
        confidence=result.confidence,
    )
    old_market_db.upsert_canonical_event_name(conn, result.canonical_event_id, "ks", candidate.source_name)
