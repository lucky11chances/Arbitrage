#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import index_event_index_db
import index_taxonomy


DEFAULT_INPUT_PATH = Path("data/index/index_wanted_ranges.jsonl")


@dataclass(frozen=True)
class WantedRangeRecord:
    sport_key: str
    competition_key: str
    wanted_from_date: str = ""
    wanted_to_date: str = ""
    pm_event_count: int = 0
    ks_event_count: int = 0
    total_event_count: int = 0
    sample_events: list[dict[str, Any]] | None = None
    source_export_path: str = ""


def load_jsonl(path: Path) -> list[WantedRangeRecord]:
    records: list[WantedRangeRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not payload.get("sport_key") or not payload.get("competition_key"):
                raise ValueError(f"{path}:{line_number} missing sport_key/competition_key")
            records.append(
                WantedRangeRecord(
                    sport_key=str(payload.get("sport_key") or ""),
                    competition_key=str(payload.get("competition_key") or ""),
                    wanted_from_date=str(payload.get("wanted_from_date") or ""),
                    wanted_to_date=str(payload.get("wanted_to_date") or ""),
                    pm_event_count=int(payload.get("pm_event_count") or 0),
                    ks_event_count=int(payload.get("ks_event_count") or 0),
                    total_event_count=int(payload.get("total_event_count") or 0),
                    sample_events=list(payload.get("sample_events") or []),
                    source_export_path=str(payload.get("source_export_path") or path.as_posix()),
                )
            )
    return records


def sync_wanted_ranges(db_path: Path, input_path: Path) -> int:
    records = load_jsonl(input_path)
    with index_event_index_db.connect(db_path) as conn:
        index_event_index_db.init_db(conn)
        index_taxonomy.seed_index_taxonomy(conn)
        for record in records:
            index_event_index_db.upsert_index_wanted_competition_range(conn, record)
        conn.commit()
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import old DB PM/KS union wanted ranges into the standalone index DB.")
    parser.add_argument("--db", default=str(index_event_index_db.DEFAULT_INDEX_DB_PATH))
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH))
    args = parser.parse_args()

    db_path = index_event_index_db.ensure_index_db_path(Path(args.db))
    input_path = Path(args.input)
    count = sync_wanted_ranges(db_path, input_path)
    print(f"index wanted ranges synced: ranges={count} input={input_path} db={db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
