#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import old_market_db
import old_refresh_index_event_maps


DEFAULT_OUTPUT_PATH = Path("data/index/index_wanted_ranges.jsonl")


@dataclass
class WantedRange:
    sport_key: str
    competition_key: str
    wanted_from_date: str
    wanted_to_date: str
    pm_event_count: int
    ks_event_count: int
    total_event_count: int
    sample_events: list[dict[str, Any]]
    source_export_path: str = ""


def readonly_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def aggregate_wanted_ranges(conn: sqlite3.Connection, *, sample_limit: int = 5) -> list[WantedRange]:
    pm_candidates, _pm_counts = old_refresh_index_event_maps.pm_candidates(conn)
    ks_candidates, _ks_counts = old_refresh_index_event_maps.ks_candidates(conn)
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "dates": [],
            "platform_events": {"pm": set(), "ks": set()},
            "samples": [],
        }
    )
    for candidate in (*pm_candidates, *ks_candidates):
        key = (candidate.sport_key, candidate.competition_key)
        bucket = grouped[key]
        if candidate.event_date:
            bucket["dates"].append(candidate.event_date)
        bucket["platform_events"][candidate.platform].add(candidate.platform_event_id)
        if len(bucket["samples"]) < sample_limit:
            bucket["samples"].append(
                {
                    "platform": candidate.platform,
                    "platform_event_id": candidate.platform_event_id,
                    "event_date": candidate.event_date,
                    "participant_names": list(candidate.participant_names),
                    "source_name": candidate.source_name,
                }
            )
    wanted = []
    for (sport_key, competition_key), bucket in sorted(grouped.items()):
        dates = sorted(set(bucket["dates"]))
        pm_count = len(bucket["platform_events"]["pm"])
        ks_count = len(bucket["platform_events"]["ks"])
        wanted.append(
            WantedRange(
                sport_key=sport_key,
                competition_key=competition_key,
                wanted_from_date=dates[0] if dates else "",
                wanted_to_date=dates[-1] if dates else "",
                pm_event_count=pm_count,
                ks_event_count=ks_count,
                total_event_count=pm_count + ks_count,
                sample_events=bucket["samples"],
            )
        )
    return wanted


def write_jsonl(path: Path, wanted_ranges: list[WantedRange]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for wanted in wanted_ranges:
            payload = asdict(wanted)
            payload["source_export_path"] = path.as_posix()
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export PM/KS old DB union coverage gaps as index wanted ranges.")
    parser.add_argument("--old-db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--sample-limit", type=int, default=5)
    args = parser.parse_args()

    old_db = old_market_db.ensure_legacy_db_path(Path(args.old_db))
    output = Path(args.output)
    with readonly_connect(old_db) as conn:
        wanted = aggregate_wanted_ranges(conn, sample_limit=max(args.sample_limit, 0))
    write_jsonl(output, wanted)
    print(
        "index wanted ranges exported: "
        f"ranges={len(wanted)} output={output} old_db={old_db}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
