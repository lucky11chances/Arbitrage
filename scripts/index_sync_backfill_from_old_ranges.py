#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import index_event_index_db
import index_event_index_sources
import old_market_db


@dataclass(frozen=True)
class CandidateWindow:
    sport_key: str
    competition_key: str
    start_date: str
    end_date: str
    eligible_events: int
    platforms: str
    source_key: str = ""


def readonly_old_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def enabled_source_lookup() -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for source in index_event_index_sources.ALL_SOURCES:
        if source.status != "enabled" or source.fetcher is None:
            continue
        lookup[(source.category_key, source.universe)] = source.source_key
    return lookup


def parse_competition_filter(value: str) -> set[tuple[str, str]]:
    selected: set[tuple[str, str]] = set()
    for part in value.split(","):
        text = part.strip()
        if not text:
            continue
        if "/" not in text:
            raise ValueError("--competitions must use sport/competition entries, for example baseball/mlb")
        sport_key, competition_key = (piece.strip() for piece in text.split("/", 1))
        if not sport_key or not competition_key:
            raise ValueError("--competitions must use sport/competition entries, for example baseball/mlb")
        selected.add((sport_key, competition_key))
    return selected


def padded_date_range(start_date: str, end_date: str, padding_days: int) -> tuple[str, str]:
    start = date.fromisoformat(start_date) - timedelta(days=max(padding_days, 0))
    end = date.fromisoformat(end_date) + timedelta(days=max(padding_days, 0))
    return start.isoformat(), end.isoformat()


def candidate_windows_from_old(
    conn: sqlite3.Connection,
    *,
    platform: str = "all",
    min_events: int = 1,
    competition_filter: set[tuple[str, str]] | None = None,
    padding_days: int = 1,
) -> list[CandidateWindow]:
    rows = conn.execute(
        """
        SELECT
            sport_key,
            competition_key,
            MIN(event_date) AS start_date,
            MAX(event_date) AS end_date,
            COUNT(DISTINCT platform_event_id) AS eligible_events,
            GROUP_CONCAT(DISTINCT platform) AS platforms
        FROM old_index_event_mapping_diagnostics
        WHERE eligible = 1
          AND sport_key != ''
          AND competition_key != ''
          AND event_date GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]'
          AND (? = 'all' OR platform = ?)
        GROUP BY sport_key, competition_key
        HAVING eligible_events >= ?
        ORDER BY eligible_events DESC, sport_key, competition_key
        """,
        (platform, platform, min_events),
    ).fetchall()
    source_by_competition = enabled_source_lookup()
    windows: list[CandidateWindow] = []
    for row in rows:
        key = (row["sport_key"], row["competition_key"])
        if competition_filter and key not in competition_filter:
            continue
        start, end = padded_date_range(row["start_date"], row["end_date"], padding_days)
        windows.append(
            CandidateWindow(
                sport_key=row["sport_key"],
                competition_key=row["competition_key"],
                start_date=start,
                end_date=end,
                eligible_events=int(row["eligible_events"]),
                platforms=str(row["platforms"] or ""),
                source_key=source_by_competition.get(key, ""),
            )
        )
    return windows


def sync_windows(
    index_db: Path,
    windows: list[CandidateWindow],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    totals = {
        "windows": len(windows),
        "synced_windows": 0,
        "skipped_no_enabled_source": 0,
        "events_seen": 0,
        "events_upserted": 0,
        "warnings": 0,
    }
    if dry_run:
        for window in windows:
            print_window(window, dry_run=True)
            if not window.source_key:
                totals["skipped_no_enabled_source"] += 1
        return totals

    with index_event_index_db.connect(index_db) as conn:
        index_event_index_db.init_db(conn)
        for window in windows:
            print_window(window, dry_run=False)
            if not window.source_key:
                totals["skipped_no_enabled_source"] += 1
                continue
            counts = index_event_index_sources.sync_index_event_index_sources(
                conn,
                sports=window.competition_key,
                from_date=window.start_date,
                thru_date=window.end_date,
                source_keys={window.source_key},
            )
            totals["synced_windows"] += 1
            totals["events_seen"] += counts.get("events_seen", 0)
            totals["events_upserted"] += counts.get("events_upserted", 0)
            totals["warnings"] += counts.get("warnings", 0)
    return totals


def print_window(window: CandidateWindow, *, dry_run: bool) -> None:
    status = "dry_run" if dry_run else "sync"
    source = window.source_key or "no_enabled_source"
    print(
        f"{status} {window.sport_key}/{window.competition_key} "
        f"dates={window.start_date}..{window.end_date} "
        f"eligible_events={window.eligible_events} platforms={window.platforms} source={source}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill the standalone index DB over old-DB candidate date ranges."
    )
    parser.add_argument("--old-db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--index-db", default=str(index_event_index_db.DEFAULT_INDEX_DB_PATH))
    parser.add_argument("--platform", choices=("pm", "ks", "all"), default="all")
    parser.add_argument("--competitions", default="", help="Comma-separated sport/competition filters, e.g. baseball/mlb,basketball/wnba.")
    parser.add_argument("--min-events", type=int, default=1)
    parser.add_argument("--padding-days", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    old_db = old_market_db.ensure_legacy_db_path(Path(args.old_db))
    index_db = index_event_index_db.ensure_index_db_path(Path(args.index_db))
    competition_filter = parse_competition_filter(args.competitions) if args.competitions else None
    with readonly_old_connect(old_db) as old_conn:
        windows = candidate_windows_from_old(
            old_conn,
            platform=args.platform,
            min_events=args.min_events,
            competition_filter=competition_filter,
            padding_days=args.padding_days,
        )
    totals = sync_windows(index_db, windows, dry_run=args.dry_run)
    print(
        "index backfill from old ranges complete: "
        + ", ".join(f"{key}={value}" for key, value in sorted(totals.items()))
        + f", old_db={old_db}, index_db={index_db}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
