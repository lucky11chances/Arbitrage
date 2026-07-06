#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import index_event_index_db
import old_index_event_matching as matching
import old_market_db


def readonly_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def print_audit(audit: matching.SourceAudit) -> None:
    print(f"source purity forbidden_events={audit.forbidden_count}")
    for row in audit.distribution:
        print(
            "source_distribution "
            f"source_key={row['source_key']} "
            f"source_type={row['source_type']} "
            f"confidence={row['source_confidence']} "
            f"events={row['event_count']}"
        )


def copy_canonical_events(index_conn: sqlite3.Connection, old_conn: sqlite3.Connection) -> int:
    rows = index_conn.execute(
        """
        SELECT canonical_event_id, category_key, universe, sport_key, competition_key,
               season, event_date, source_local_date, start_time_utc, market_type,
               competition_gender, event_name, venue_id, venue_name, venue_city,
               venue_region, venue_country, source_key, source_event_id, source_type,
               source_url, source_confidence, first_seen_ts, last_seen_ts
        FROM canonical_events
        ORDER BY canonical_event_id
        """
    ).fetchall()
    participant_keys = {
        row["canonical_event_id"]: "|".join(
            sorted(
                part["participant_key"]
                for part in index_conn.execute(
                    "SELECT participant_key FROM canonical_participants WHERE canonical_event_id = ?",
                    (row["canonical_event_id"],),
                ).fetchall()
            )
        )
        for row in rows
    }
    for row in rows:
        match_name = row["event_name"] or row["canonical_event_id"]
        old_conn.execute(
            """
            INSERT INTO canonical_events (
                canonical_event_id, category_key, universe, sport_key, competition_key,
                season, event_date, source_local_date, start_time_utc, market_type,
                competition_gender, entity_key, match_name, event_name, venue_id,
                venue_name, venue_city, venue_region, venue_country, mapping_source,
                source_event_id, source_type, source_url, source_confidence,
                first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_event_id) DO UPDATE SET
                category_key=excluded.category_key,
                universe=excluded.universe,
                sport_key=excluded.sport_key,
                competition_key=excluded.competition_key,
                season=excluded.season,
                event_date=excluded.event_date,
                source_local_date=excluded.source_local_date,
                start_time_utc=excluded.start_time_utc,
                market_type=excluded.market_type,
                competition_gender=excluded.competition_gender,
                entity_key=excluded.entity_key,
                match_name=excluded.match_name,
                event_name=excluded.event_name,
                venue_id=excluded.venue_id,
                venue_name=excluded.venue_name,
                venue_city=excluded.venue_city,
                venue_region=excluded.venue_region,
                venue_country=excluded.venue_country,
                mapping_source=excluded.mapping_source,
                source_event_id=excluded.source_event_id,
                source_type=excluded.source_type,
                source_url=excluded.source_url,
                source_confidence=excluded.source_confidence,
                last_seen_ts=excluded.last_seen_ts
            """,
            (
                row["canonical_event_id"],
                row["category_key"],
                row["universe"],
                row["sport_key"],
                row["competition_key"],
                row["season"],
                row["event_date"],
                row["source_local_date"],
                row["start_time_utc"],
                row["market_type"],
                row["competition_gender"],
                participant_keys.get(row["canonical_event_id"], ""),
                match_name,
                row["event_name"],
                row["venue_id"],
                row["venue_name"],
                row["venue_city"],
                row["venue_region"],
                row["venue_country"],
                row["source_key"],
                row["source_event_id"],
                row["source_type"],
                row["source_url"],
                row["source_confidence"],
                row["first_seen_ts"],
                row["last_seen_ts"],
            ),
        )
        old_market_db.upsert_canonical_event_name(old_conn, row["canonical_event_id"], "source", match_name)
    return len(rows)


def replace_snapshot_tables(index_conn: sqlite3.Connection, old_conn: sqlite3.Connection) -> dict[str, int]:
    counts = {
        "participants_seen": 0,
        "participants_inserted": 0,
        "event_sources_seen": 0,
        "event_sources_inserted": 0,
        "platform_maps_seen": 0,
        "platform_maps_inserted": 0,
    }
    old_conn.execute("DELETE FROM old_index_canonical_participants")
    old_conn.execute("DELETE FROM old_index_event_sources")
    old_conn.execute("DELETE FROM old_index_platform_category_map")

    for row in index_conn.execute("SELECT * FROM canonical_participants ORDER BY canonical_event_id, participant_order").fetchall():
        counts["participants_seen"] += 1
        old_conn.execute(
            """
            INSERT INTO old_index_canonical_participants (
                canonical_event_id, participant_key, source_participant_id, display_name,
                participant_order, role, aliases_json, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["canonical_event_id"],
                row["participant_key"],
                row["source_participant_id"],
                row["display_name"],
                row["participant_order"],
                row["role"],
                row["aliases_json"],
                row["first_seen_ts"],
                row["last_seen_ts"],
            ),
        )
        aliases = matching.parse_json_list(row["aliases_json"])
        old_market_db.upsert_canonical_event_name(old_conn, row["canonical_event_id"], "source", row["display_name"])
        for alias in aliases:
            old_market_db.upsert_canonical_event_name(old_conn, row["canonical_event_id"], "source", str(alias))
        counts["participants_inserted"] += 1

    for row in index_conn.execute("SELECT * FROM canonical_event_sources ORDER BY canonical_event_id, source_key").fetchall():
        counts["event_sources_seen"] += 1
        old_conn.execute(
            """
            INSERT INTO old_index_event_sources (
                canonical_event_id, source_key, source_event_id, source_type, source_url,
                source_confidence, raw_payload_hash, raw_payload_json, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["canonical_event_id"],
                row["source_key"],
                row["source_event_id"],
                row["source_type"],
                row["source_url"],
                row["source_confidence"],
                row["raw_payload_hash"],
                row["raw_payload_json"],
                row["first_seen_ts"],
                row["last_seen_ts"],
            ),
        )
        counts["event_sources_inserted"] += 1

    for row in index_conn.execute("SELECT * FROM index_platform_category_map ORDER BY platform, platform_key").fetchall():
        counts["platform_maps_seen"] += 1
        old_conn.execute(
            """
            INSERT INTO old_index_platform_category_map (
                platform, platform_key_type, platform_key, sport_key, competition_key,
                mapping_status, confidence, notes, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["platform"],
                row["platform_key_type"],
                row["platform_key"],
                row["sport_key"],
                row["competition_key"],
                row["mapping_status"],
                row["confidence"],
                row["notes"],
                row["first_seen_ts"],
                row["last_seen_ts"],
            ),
        )
        counts["platform_maps_inserted"] += 1
    return counts


def sync_snapshot(index_db: Path, old_db: Path, *, print_audit_report: bool = False) -> dict[str, int]:
    with readonly_connect(index_db) as index_conn, old_market_db.connect(old_db) as old_conn:
        old_market_db.init_db(old_conn)
        matching.ensure_old_index_tables(old_conn)
        audit = matching.audit_index_sources(index_conn)
        if print_audit_report:
            print_audit(audit)
        if audit.forbidden_count:
            raise RuntimeError("index canonical_events contains PM/KS-derived sources")
        sync_run_id = matching.begin_sync_run(old_conn, index_db)
        counts = {"canonical_events_seen": 0, "canonical_events_upserted": 0}
        try:
            copied = copy_canonical_events(index_conn, old_conn)
            counts["canonical_events_seen"] = copied
            counts["canonical_events_upserted"] = copied
            counts.update(replace_snapshot_tables(index_conn, old_conn))
            matching.finish_sync_run(old_conn, sync_run_id, counts, status="ok", message="snapshot copied")
            old_conn.commit()
            return counts
        except Exception as exc:
            matching.finish_sync_run(old_conn, sync_run_id, counts, status="error", message=str(exc))
            old_conn.commit()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy the standalone index DB canonical event snapshot into the old DB.")
    parser.add_argument("--index-db", default=str(matching.INDEX_DB_PATH))
    parser.add_argument("--old-db", default=str(matching.OLD_DB_PATH))
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()

    index_db = index_event_index_db.ensure_index_db_path(Path(args.index_db))
    old_db = old_market_db.ensure_legacy_db_path(Path(args.old_db))
    if args.audit_only:
        with readonly_connect(index_db) as index_conn:
            audit = matching.audit_index_sources(index_conn)
            print_audit(audit)
            if audit.forbidden_count:
                return 2
        return 0

    counts = sync_snapshot(index_db, old_db, print_audit_report=True)
    print(
        "old index snapshot sync complete: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        + f", old_db={old_db}, index_db={index_db}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
