#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import market_db
import pipeline_core as core


def write_per_universe_outputs(rows: list[dict[str, object]], output_dir: Path) -> None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("universe") or "unknown")].append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    for universe, universe_rows in grouped.items():
        core.write_latest_csv(
            universe_rows,
            core.BINARY_CSV_FIELDS,
            output_dir / f"{universe}_db_arb_snapshot_latest.csv",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute PM/KS net edge from local SQLite orderbook snapshots.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--output", default="data/staging/latest_edges.csv")
    parser.add_argument("--alert-output", default="data/staging/latest_alerts.csv")
    parser.add_argument("--alert-dir", default="data/staging/alerts")
    parser.add_argument("--per-universe-dir", default="data/staging")
    parser.add_argument("--max-age-seconds", type=float, default=10.0)
    parser.add_argument("--max-skew-seconds", type=float, default=5.0)
    parser.add_argument("--ignore-age", action="store_true")
    parser.add_argument("--no-per-universe", action="store_true")
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()
    if args.max_age_seconds <= 0 or args.max_skew_seconds <= 0:
        parser.error("age/skew windows must be positive")

    with market_db.connect(Path(args.db)) as conn:
        market_db.init_db(conn)
        rows, warnings = market_db.compute_edge_snapshots(
            conn,
            max_age_seconds=args.max_age_seconds,
            max_skew_seconds=args.max_skew_seconds,
            ignore_age=args.ignore_age,
        )
        alerts = core.alert_rows(rows)
        core.write_latest_csv(rows, core.BINARY_CSV_FIELDS, Path(args.output))
        core.write_latest_csv(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_output))
        core.write_alert_folder(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_dir))
        if not args.no_per_universe:
            write_per_universe_outputs(rows, Path(args.per_universe_dir))
        counts = market_db.row_counts(conn)

    if args.show_warnings:
        for warning in warnings:
            print(f"DB edge warning: {warning}")
    print(
        "staging DB edge complete: "
        f"rows={len(rows)}; alerts={len(alerts)}; output={args.output}; alert_output={args.alert_output}"
    )
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
