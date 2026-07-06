#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import new_ws_orderbook_core as core


METADATA_TABLES = (
    "pm_events",
    "pm_event_sources",
    "pm_markets",
    "pm_tokens",
    "ks_events",
    "ks_markets",
)


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row[1]) for row in rows]


def copy_table(source: sqlite3.Connection, dest: sqlite3.Connection, table: str) -> int:
    columns = table_columns(source, table)
    quoted = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    rows = source.execute(f"SELECT {quoted} FROM {table}").fetchall()
    if not rows:
        return 0
    dest.executemany(
        f"INSERT OR REPLACE INTO {table} ({quoted}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy WS metadata tables into a service-local SQLite DB.")
    parser.add_argument("--source", default="data/new/new_arb_research.sqlite")
    parser.add_argument("--dest", required=True)
    args = parser.parse_args()

    source_path = Path(args.source)
    dest_path = Path(args.dest)
    if not source_path.exists():
        raise SystemExit(f"missing source DB: {source_path}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    dest = core.connect_db(dest_path)
    try:
        dest.execute("PRAGMA foreign_keys=OFF")
        for table in reversed(METADATA_TABLES):
            dest.execute(f"DELETE FROM {table}")
        counts = {table: copy_table(source, dest, table) for table in METADATA_TABLES}
        dest.commit()
    finally:
        source.close()
        dest.close()

    print("metadata export complete: " + ", ".join(f"{table}={count}" for table, count in counts.items()))


if __name__ == "__main__":
    main()
