#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import index_event_index_db
import index_event_index_sources
import index_taxonomy


def parse_source_keys(value: str) -> set[str] | None:
    keys = {part.strip() for part in value.split(",") if part.strip()}
    return keys or None


def parse_source_modes(value: str) -> set[str] | None:
    modes = {part.strip().lower() for part in value.split(",") if part.strip()}
    allowed = {"api", "web", "curated", "all"}
    unknown = modes - allowed
    if unknown:
        raise ValueError(f"unknown source mode(s): {', '.join(sorted(unknown))}")
    return modes or None


def wanted_ranges(conn) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT sport_key, competition_key, wanted_from_date, wanted_to_date
        FROM index_wanted_competition_ranges
        WHERE competition_key != ''
          AND wanted_from_date != ''
          AND wanted_to_date != ''
        ORDER BY sport_key, competition_key
        """
    ).fetchall()
    return [dict(row) for row in rows]


def add_counts(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    for key, value in right.items():
        merged[key] = int(merged.get(key, 0) or 0) + int(value or 0)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync official/vetted schedules into the standalone event index DB."
    )
    parser.add_argument("--db", default=str(index_event_index_db.DEFAULT_INDEX_DB_PATH))
    parser.add_argument("--sports", default="all")
    parser.add_argument("--competition", default="", help="Comma-separated competition keys such as mlb,nba,wnba,kbo.")
    parser.add_argument("--from-date", default=date.today().isoformat())
    parser.add_argument("--thru-date", default="")
    parser.add_argument("--to-date", default="", help="Alias for --thru-date.")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--sources", default="", help="Comma-separated event index source keys to sync.")
    parser.add_argument("--source-mode", default="all", help="Comma-separated source modes: api,web,curated,all.")
    parser.add_argument("--from-wanted-ranges", action="store_true", help="Sync competitions/date ranges currently recorded in index_wanted_competition_ranges.")
    parser.add_argument("--skip-taxonomy-sync", action="store_true")
    parser.add_argument("--skip-source-sync", action="store_true")
    parser.add_argument("--discover-platform-categories", action="store_true", help="Best-effort live PM/KS category discovery.")
    args = parser.parse_args()

    with index_event_index_db.connect(Path(args.db)) as conn:
        index_event_index_db.init_db(conn)
        taxonomy_counts = {
            "sports": 0,
            "competitions": 0,
            "platform_categories": 0,
            "platform_maps": 0,
            "competition_sources": 0,
            "discovered_pm_categories": 0,
            "discovered_ks_categories": 0,
        }
        if not args.skip_taxonomy_sync:
            taxonomy_counts = index_taxonomy.sync_index_taxonomy(
                conn,
                discover_pm=args.discover_platform_categories,
                discover_ks=args.discover_platform_categories,
            )
        source_counts = {
            "sources": 0,
            "events_seen": 0,
            "events_upserted": 0,
            "source_gaps": 0,
            "missing_api_keys": 0,
            "warnings": 0,
        }
        if not args.skip_source_sync:
            source_keys = parse_source_keys(args.sources)
            source_modes = parse_source_modes(args.source_mode)
            if args.from_wanted_ranges:
                for wanted in wanted_ranges(conn):
                    source_counts = add_counts(
                        source_counts,
                        index_event_index_sources.sync_index_event_index_sources(
                            conn,
                            sports=wanted["competition_key"],
                            from_date=wanted["wanted_from_date"],
                            thru_date=wanted["wanted_to_date"],
                            days=args.days,
                            source_keys=source_keys,
                            source_modes=source_modes,
                        ),
                    )
            else:
                source_counts = index_event_index_sources.sync_index_event_index_sources(
                    conn,
                    sports=args.competition or args.sports,
                    from_date=args.from_date,
                    thru_date=args.to_date or args.thru_date or None,
                    days=args.days,
                    source_keys=source_keys,
                    source_modes=source_modes,
                )

        row_counts = index_event_index_db.row_counts(conn)

    print(
        "event index sync complete: "
        f"taxonomy_sports={taxonomy_counts['sports']}; "
        f"taxonomy_competitions={taxonomy_counts['competitions']}; "
        f"platform_categories={taxonomy_counts['platform_categories']}; "
        f"platform_maps={taxonomy_counts['platform_maps']}; "
        f"competition_sources={taxonomy_counts['competition_sources']}; "
        f"discovered_pm_categories={taxonomy_counts['discovered_pm_categories']}; "
        f"discovered_ks_categories={taxonomy_counts['discovered_ks_categories']}; "
        f"sources={source_counts['sources']}; "
        f"events_seen={source_counts['events_seen']}; "
        f"events_upserted={source_counts['events_upserted']}; "
        f"source_gaps={source_counts['source_gaps']}; "
        f"missing_api_keys={source_counts['missing_api_keys']}; "
        f"source_warnings={source_counts['warnings']}; "
        f"db={args.db}"
    )
    print(", ".join(f"{key}={value}" for key, value in row_counts.items()))


if __name__ == "__main__":
    main()
