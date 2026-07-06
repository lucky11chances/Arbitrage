#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import index_event_index_db


def readonly_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def coverage_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH
        events AS (
            SELECT sport_key, competition_key, COUNT(*) AS event_count,
                   MIN(event_date) AS event_min_date, MAX(event_date) AS event_max_date
            FROM canonical_events
            GROUP BY sport_key, competition_key
        ),
        sources AS (
            SELECT sport_key, competition_key,
                   GROUP_CONCAT(source_key || ':' || source_status, ',') AS source_keys
            FROM index_competition_sources
            GROUP BY sport_key, competition_key
        )
        SELECT
            comp.sport_key,
            comp.competition_key,
            comp.display_name,
            COALESCE(events.event_count, 0) AS event_count,
            COALESCE(events.event_min_date, '') AS event_min_date,
            COALESCE(events.event_max_date, '') AS event_max_date,
            COALESCE(wanted.wanted_from_date, '') AS wanted_from_date,
            COALESCE(wanted.wanted_to_date, '') AS wanted_to_date,
            COALESCE(wanted.pm_event_count, 0) AS pm_event_count,
            COALESCE(wanted.ks_event_count, 0) AS ks_event_count,
            COALESCE(sources.source_keys, '') AS source_keys
        FROM index_competitions AS comp
        LEFT JOIN events
          ON events.sport_key = comp.sport_key
         AND events.competition_key = comp.competition_key
        LEFT JOIN index_wanted_competition_ranges AS wanted
          ON wanted.sport_key = comp.sport_key
         AND wanted.competition_key = comp.competition_key
        LEFT JOIN sources
          ON sources.sport_key = comp.sport_key
         AND sources.competition_key = comp.competition_key
        ORDER BY event_count ASC, (pm_event_count + ks_event_count) DESC, comp.sport_key, comp.competition_key
        """
    ).fetchall()


def print_report(conn: sqlite3.Connection, *, limit: int) -> None:
    print("index source coverage")
    rows = coverage_rows(conn)
    for row in rows[:limit if limit > 0 else None]:
        wanted = f"{row['wanted_from_date']}..{row['wanted_to_date']}" if row["wanted_from_date"] else ".."
        events = f"{row['event_min_date']}..{row['event_max_date']}" if row["event_min_date"] else ".."
        print(
            f"{row['sport_key']}/{row['competition_key']}: "
            f"events={row['event_count']} event_dates={events} "
            f"wanted_dates={wanted} pm={row['pm_event_count']} ks={row['ks_event_count']} "
            f"sources={row['source_keys']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Report source and wanted-range coverage for the standalone index DB.")
    parser.add_argument("--db", default=str(index_event_index_db.DEFAULT_INDEX_DB_PATH))
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args()

    db_path = index_event_index_db.ensure_index_db_path(Path(args.db))
    with readonly_connect(db_path) as conn:
        print_report(conn, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
