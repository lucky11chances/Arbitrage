#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import old_index_event_matching as matching
import old_market_db


def readonly_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def classified_diagnostics_cte() -> str:
    return """
        WITH
        index_competitions AS (
            SELECT
                sport_key,
                competition_key,
                COUNT(*) AS index_events,
                MIN(event_date) AS index_min_date,
                MAX(event_date) AS index_max_date
            FROM canonical_events
            GROUP BY sport_key, competition_key
        ),
        index_dates AS (
            SELECT DISTINCT sport_key, competition_key, event_date
            FROM canonical_events
        ),
        classified AS (
            SELECT
                diag.*,
                COALESCE(index_competitions.index_events, 0) AS index_events,
                COALESCE(index_competitions.index_min_date, '') AS index_min_date,
                COALESCE(index_competitions.index_max_date, '') AS index_max_date,
                CASE
                    WHEN diag.status = 'mapped' THEN 'matched'
                    WHEN diag.reason != 'no_index_event' THEN diag.reason
                    WHEN COALESCE(index_competitions.index_events, 0) = 0 THEN 'no_index_competition'
                    WHEN index_dates.event_date IS NULL THEN 'no_index_date'
                    ELSE 'participant_mismatch_same_date'
                END AS diagnosis_reason
            FROM old_index_event_mapping_diagnostics AS diag
            LEFT JOIN index_competitions
              ON index_competitions.sport_key = diag.sport_key
             AND index_competitions.competition_key = diag.competition_key
            LEFT JOIN index_dates
              ON index_dates.sport_key = diag.sport_key
             AND index_dates.competition_key = diag.competition_key
             AND index_dates.event_date = diag.event_date
        )
    """


def rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        classified_diagnostics_cte()
        + """
        ,
        by_competition AS (
            SELECT
                platform,
                sport_key,
                competition_key,
                COUNT(DISTINCT CASE WHEN eligible = 1 THEN platform_event_id END) AS eligible_events,
                COUNT(DISTINCT CASE WHEN status = 'mapped' THEN platform_event_id END) AS mapped_events,
                COUNT(DISTINCT platform_event_id) AS observed_events,
                MAX(index_events) AS index_events,
                MIN(NULLIF(event_date, '')) AS candidate_min_date,
                MAX(NULLIF(event_date, '')) AS candidate_max_date,
                MAX(index_min_date) AS index_min_date,
                MAX(index_max_date) AS index_max_date
            FROM classified
            GROUP BY platform, sport_key, competition_key
        )
        SELECT
            platform,
            sport_key,
            competition_key,
            observed_events,
            eligible_events,
            mapped_events,
            index_events,
            COALESCE(candidate_min_date, '') AS candidate_min_date,
            COALESCE(candidate_max_date, '') AS candidate_max_date,
            COALESCE(index_min_date, '') AS index_min_date,
            COALESCE(index_max_date, '') AS index_max_date,
            CASE
                WHEN eligible_events = 0 THEN 0.0
                ELSE ROUND(100.0 * mapped_events / eligible_events, 2)
            END AS mapped_pct
        FROM by_competition
        ORDER BY platform, mapped_pct ASC, eligible_events DESC, sport_key, competition_key
        """
    ).fetchall()


def reason_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        classified_diagnostics_cte()
        + """
        SELECT
            platform,
            sport_key,
            competition_key,
            diagnosis_reason AS reason,
            COUNT(DISTINCT platform_event_id) AS events
        FROM classified
        WHERE status != 'mapped'
        GROUP BY platform, sport_key, competition_key, diagnosis_reason
        ORDER BY platform, events DESC, reason
        """
    ).fetchall()


def summary_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            platform,
            COUNT(DISTINCT CASE WHEN eligible = 1 THEN platform_event_id END) AS eligible_events,
            COUNT(DISTINCT CASE WHEN status = 'mapped' THEN platform_event_id END) AS mapped_events,
            COUNT(DISTINCT platform_event_id) AS observed_events,
            CASE
                WHEN COUNT(DISTINCT CASE WHEN eligible = 1 THEN platform_event_id END) = 0 THEN 0.0
                ELSE ROUND(
                    100.0 * COUNT(DISTINCT CASE WHEN status = 'mapped' THEN platform_event_id END)
                    / COUNT(DISTINCT CASE WHEN eligible = 1 THEN platform_event_id END),
                    2
                )
            END AS mapped_pct
        FROM old_index_event_mapping_diagnostics
        GROUP BY platform
        ORDER BY platform
        """
    ).fetchall()


def sample_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    if limit <= 0:
        return []
    return conn.execute(
        classified_diagnostics_cte()
        + """
        SELECT
            platform,
            sport_key,
            competition_key,
            diagnosis_reason AS reason,
            event_date,
            platform_event_id,
            source_name,
            index_events,
            index_min_date,
            index_max_date
        FROM classified
        WHERE status != 'mapped'
        ORDER BY
            CASE diagnosis_reason
                WHEN 'no_index_competition' THEN 0
                WHEN 'no_index_date' THEN 1
                WHEN 'participant_mismatch_same_date' THEN 2
                WHEN 'time_mismatch' THEN 3
                ELSE 4
            END,
            platform,
            sport_key,
            competition_key,
            event_date,
            platform_event_id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def print_report(conn: sqlite3.Connection, *, sample_limit: int = 25) -> None:
    print("index event mapping coverage summary")
    for row in summary_rows(conn):
        print(
            f"{row['platform']}: observed={row['observed_events']} "
            f"eligible={row['eligible_events']} mapped={row['mapped_events']} "
            f"mapped_pct={row['mapped_pct']}%"
        )
    print("")
    print("coverage by sport/competition")
    for row in rows(conn):
        print(
            f"{row['platform']} {row['sport_key']}/{row['competition_key']}: "
            f"observed={row['observed_events']} eligible={row['eligible_events']} "
            f"mapped={row['mapped_events']} mapped_pct={row['mapped_pct']}%"
            f" index_events={row['index_events']}"
            f" candidate_dates={row['candidate_min_date']}..{row['candidate_max_date']}"
            f" index_dates={row['index_min_date']}..{row['index_max_date']}"
        )
    print("")
    print("unmapped diagnosis reasons")
    for row in reason_rows(conn):
        print(
            f"{row['platform']} {row['sport_key']}/{row['competition_key']} "
            f"{row['reason']}: {row['events']}"
        )
    samples = sample_rows(conn, sample_limit)
    if samples:
        print("")
        print("unmapped diagnosis samples")
        for row in samples:
            print(
                f"{row['platform']} {row['sport_key']}/{row['competition_key']} "
                f"{row['reason']} event_date={row['event_date']} "
                f"platform_event_id={row['platform_event_id']} "
                f"index_events={row['index_events']} index_dates={row['index_min_date']}..{row['index_max_date']} "
                f"name={row['source_name']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Report old DB PM/KS to index-event mapping coverage.")
    parser.add_argument("--old-db", default=str(matching.OLD_DB_PATH))
    parser.add_argument("--samples", type=int, default=25)
    args = parser.parse_args()
    old_db = old_market_db.ensure_legacy_db_path(Path(args.old_db))
    with readonly_connect(old_db) as conn:
        print_report(conn, sample_limit=args.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
