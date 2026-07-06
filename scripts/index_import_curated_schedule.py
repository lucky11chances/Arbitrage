#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import index_event_index_core as index_core
import index_event_index_db
import index_taxonomy


REQUIRED_FIELDS = {
    "sport_key",
    "competition_key",
    "event_date",
    "participant_a",
    "participant_b",
    "source_url",
    "source_name",
    "source_type",
    "source_accessed_date",
}
ALLOWED_SOURCE_TYPES = {
    "official_api",
    "official_site",
    "league_site",
    "team_site",
    "tournament_site",
    "authoritative_sports_media",
    "curated_from_authoritative_source",
    "paid_provider_api",
    "vetted_public_api",
    "vetted_provider_api",
    "static_official_fixture",
}
FORBIDDEN_SOURCE_RE = re.compile(r"\b(pm|polymarket|ks|kalshi)\b", re.IGNORECASE)


@dataclass(frozen=True)
class EventEvidence:
    canonical_event_id: str
    source_key: str
    source_name: str
    source_type: str
    source_url: str
    source_accessed_date: str
    evidence_status: str = "verified"
    confidence: str = "medium"
    raw_payload: dict[str, Any] | None = None


def alias_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def split_aliases(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split("|") if part.strip())


def market_type_for(sport_key: str, competition_key: str) -> str:
    if sport_key in {"boxing", "mma"} or competition_key in {"ufc", "boxing"}:
        return "fighter_winner"
    if sport_key in {"tennis", "esports"}:
        return "match_winner"
    return "game_winner"


def confidence_for_source_type(source_type: str) -> str:
    if source_type in {"official_api", "official_site", "league_site", "team_site", "tournament_site", "static_official_fixture"}:
        return "high"
    return "medium"


def expand_input_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = [Path(match) for match in glob.glob(pattern)]
        paths.extend(matches or [Path(pattern)])
    return sorted(dict.fromkeys(paths))


def load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(row) for row in payload]
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            return [dict(row) for row in payload["events"]]
        if isinstance(payload, dict):
            return [payload]
    raise ValueError(f"unsupported curated schedule format: {path}")


def validate_row(row: dict[str, Any], *, path: Path, row_number: int) -> None:
    missing = sorted(field for field in REQUIRED_FIELDS if not str(row.get(field) or "").strip())
    if missing:
        raise ValueError(f"{path}:{row_number} missing required fields: {', '.join(missing)}")
    source_type = str(row.get("source_type") or "").strip()
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError(f"{path}:{row_number} unsupported source_type={source_type}")
    source_text = " ".join(str(row.get(key) or "") for key in ("source_key", "source_name", "source_type", "source_url"))
    if FORBIDDEN_SOURCE_RE.search(source_text):
        raise ValueError(f"{path}:{row_number} PM/KS cannot be a canonical event source")


def indexed_event_from_row(row: dict[str, Any]) -> tuple[index_core.IndexedEvent, EventEvidence]:
    sport_key = str(row.get("sport_key") or "").strip()
    competition_key = str(row.get("competition_key") or "").strip()
    gender = str(row.get("competition_gender") or row.get("gender") or "open").strip()
    event_date = str(row.get("event_date") or "").strip()
    market_type = str(row.get("market_type") or market_type_for(sport_key, competition_key)).strip()
    source_name = str(row.get("source_name") or "").strip()
    source_type = str(row.get("source_type") or "").strip()
    source_url = str(row.get("source_url") or "").strip()
    source_key = str(row.get("source_key") or f"curated_{competition_key}_{alias_key(source_name)}").strip()
    source_accessed_date = str(row.get("source_accessed_date") or "").strip()
    source_confidence = str(row.get("source_confidence") or confidence_for_source_type(source_type)).strip()
    participant_a = str(row.get("participant_a") or "").strip()
    participant_b = str(row.get("participant_b") or "").strip()
    participant_a_id = str(row.get("participant_a_id") or alias_key(participant_a)).strip()
    participant_b_id = str(row.get("participant_b_id") or alias_key(participant_b)).strip()
    participants = (
        index_core.IndexedParticipant(
            participant_key=index_core.participant_key(sport_key, competition_key, gender, participant_a_id),
            source_participant_id=participant_a_id,
            display_name=participant_a,
            aliases=index_core.team_aliases(participant_a, *split_aliases(str(row.get("participant_a_aliases") or ""))),
            role=str(row.get("participant_a_role") or "away"),
            order=0,
        ),
        index_core.IndexedParticipant(
            participant_key=index_core.participant_key(sport_key, competition_key, gender, participant_b_id),
            source_participant_id=participant_b_id,
            display_name=participant_b,
            aliases=index_core.team_aliases(participant_b, *split_aliases(str(row.get("participant_b_aliases") or ""))),
            role=str(row.get("participant_b_role") or "home"),
            order=1,
        ),
    )
    raw_payload = dict(row)
    raw_hash, _raw_json = index_event_index_db.json_hash(raw_payload)
    source_event_id = str(row.get("source_event_id") or "").strip()
    if source_event_id:
        canonical_event_id = index_core.canonical_event_id_from_source(competition_key, source_key, source_event_id)
    else:
        participant_keys = "|".join(sorted(participant.participant_key for participant in participants))
        canonical_event_id = f"{competition_key}:{event_date}:{market_type}:{participant_keys}:{raw_hash[:8]}"
        source_event_id = f"{event_date}:{market_type}:{participant_keys}:{raw_hash[:8]}"
    event_name = str(row.get("event_name") or row.get("match_name") or f"{participant_a} vs {participant_b}").strip()
    event = index_core.IndexedEvent(
        canonical_event_id=canonical_event_id,
        category_key=sport_key,
        universe=competition_key,
        sport_key=sport_key,
        competition_key=competition_key,
        season=str(row.get("season") or (event_date[:4] if len(event_date) >= 4 else "")),
        event_date=event_date,
        source_local_date=str(row.get("source_local_date") or event_date),
        start_time_utc=str(row.get("start_time_utc") or ""),
        market_type=market_type,
        competition_gender=gender,
        match_name=event_name,
        event_name=event_name,
        venue_id=str(row.get("venue_id") or ""),
        venue_name=str(row.get("venue_name") or ""),
        venue_city=str(row.get("venue_city") or ""),
        venue_region=str(row.get("venue_region") or ""),
        venue_country=str(row.get("venue_country") or ""),
        source_key=source_key,
        source_event_id=source_event_id,
        source_type=source_type,
        source_confidence=source_confidence,
        source_url=source_url,
        participants=participants,
        raw_payload=raw_payload,
    )
    evidence = EventEvidence(
        canonical_event_id=canonical_event_id,
        source_key=source_key,
        source_name=source_name,
        source_type=source_type,
        source_url=source_url,
        source_accessed_date=source_accessed_date,
        confidence=source_confidence,
        raw_payload=raw_payload,
    )
    return event, evidence


def import_file(conn, path: Path) -> tuple[int, int]:
    rows = load_rows(path)
    if not rows:
        import_id = index_event_index_db.begin_manual_schedule_import(
            conn,
            input_path=path.as_posix(),
            source_name="",
            source_url="",
            source_type="",
            source_accessed_date="",
            raw_payload=[],
        )
        index_event_index_db.finish_manual_schedule_import(conn, import_id, status="empty", message="no rows")
        return 0, 0
    first = rows[0]
    import_id = index_event_index_db.begin_manual_schedule_import(
        conn,
        input_path=path.as_posix(),
        source_name=str(first.get("source_name") or ""),
        source_url=str(first.get("source_url") or ""),
        source_type=str(first.get("source_type") or ""),
        source_accessed_date=str(first.get("source_accessed_date") or ""),
        raw_payload={"rows": rows},
    )
    upserted = 0
    try:
        for row_number, row in enumerate(rows, start=1):
            validate_row(row, path=path, row_number=row_number)
            event, evidence = indexed_event_from_row(row)
            index_event_index_db.upsert_indexed_event(conn, event)
            index_event_index_db.upsert_index_event_evidence(conn, evidence)
            upserted += 1
        index_event_index_db.finish_manual_schedule_import(
            conn,
            import_id,
            status="ok",
            rows_seen=len(rows),
            events_upserted=upserted,
            message=f"imported {upserted} curated events",
        )
        return len(rows), upserted
    except Exception as exc:
        index_event_index_db.finish_manual_schedule_import(
            conn,
            import_id,
            status="error",
            rows_seen=len(rows),
            events_upserted=upserted,
            warnings_count=1,
            message=str(exc),
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Import curated authoritative schedules into the standalone index DB.")
    parser.add_argument("--db", default=str(index_event_index_db.DEFAULT_INDEX_DB_PATH))
    parser.add_argument("--input", nargs="+", required=True, help="CSV/JSON/JSONL files or glob patterns.")
    args = parser.parse_args()

    db_path = index_event_index_db.ensure_index_db_path(Path(args.db))
    paths = expand_input_paths(args.input)
    with index_event_index_db.connect(db_path) as conn:
        index_event_index_db.init_db(conn)
        index_taxonomy.seed_index_taxonomy(conn)
        rows_seen = 0
        events_upserted = 0
        for path in paths:
            seen, upserted = import_file(conn, path)
            rows_seen += seen
            events_upserted += upserted
        conn.commit()
    print(
        "curated schedule import complete: "
        f"files={len(paths)} rows_seen={rows_seen} events_upserted={events_upserted} db={db_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
