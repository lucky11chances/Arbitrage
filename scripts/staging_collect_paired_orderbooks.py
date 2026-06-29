#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import market_db
import pipeline_core as core


def requested_universes(value: str) -> set[str]:
    return {part.strip().lower().replace("-", "_") for part in value.split(",") if part.strip()}


def selected_pairs(conn, universes: set[str], limit: int) -> list[Any]:
    params: list[Any] = []
    universe_filter = ""
    if universes and "all" not in universes:
        placeholders = ",".join("?" for _ in universes)
        universe_filter = f"AND pair.universe IN ({placeholders})"
        params.extend(sorted(universes))
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
    return conn.execute(
        f"""
        WITH latest AS (
            SELECT venue, instrument_id, MAX(collected_ts_utc) AS last_collected_ts
            FROM orderbook_observations
            WHERE status = 'ok'
              AND best_bid_scaled IS NOT NULL
              AND best_bid_size_scaled IS NOT NULL
              AND best_ask_scaled IS NOT NULL
              AND best_ask_size_scaled IS NOT NULL
            GROUP BY venue, instrument_id
        )
        SELECT
            pair.*,
            pm.last_collected_ts AS pm_last_collected_ts,
            ks.last_collected_ts AS ks_last_collected_ts
        FROM paired_contracts pair
        LEFT JOIN latest pm ON pm.venue = 'pm' AND pm.instrument_id = pair.pm_token_id
        LEFT JOIN latest ks ON ks.venue = 'ks' AND ks.instrument_id = pair.ks_market_ticker
        WHERE pair.safe_paired = 1
          {universe_filter}
        ORDER BY
            CASE WHEN pm.last_collected_ts IS NULL OR ks.last_collected_ts IS NULL THEN 0 ELSE 1 END,
            MIN(COALESCE(pm.last_collected_ts, ''), COALESCE(ks.last_collected_ts, '')),
            pair.universe,
            pair.event_date,
            pair.match_name,
            pair.pm_yes_outcome
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def collect_pair(conn, pair, ks_depth: int, sleep_seconds: float = 0.0) -> tuple[int, int]:
    pm_observation_id = market_db.fetch_and_record_orderbook(
        conn,
        venue="pm",
        instrument_id=str(pair["pm_token_id"]),
        request_path="/book",
        depth=None,
        fetcher=lambda: core.get_json(core.PM_CLOB, "/book", {"token_id": str(pair["pm_token_id"])}),
    )
    if sleep_seconds:
        time.sleep(sleep_seconds)
    ks_ticker = str(pair["ks_market_ticker"])
    ks_observation_id = market_db.fetch_and_record_orderbook(
        conn,
        venue="ks",
        instrument_id=ks_ticker,
        request_path=f"/markets/{ks_ticker}/orderbook",
        depth=ks_depth,
        fetcher=lambda: core.get_json(core.KALSHI_API, f"/markets/{ks_ticker}/orderbook", {"depth": ks_depth}),
    )
    return pm_observation_id, ks_observation_id


def collect_pairs(conn, pairs: list[Any], ks_depth: int, sleep_seconds: float, commit_every: int) -> tuple[int, int]:
    pm_count = 0
    ks_count = 0
    for index, pair in enumerate(pairs, start=1):
        collect_pair(conn, pair, ks_depth, sleep_seconds)
        pm_count += 1
        ks_count += 1
        if commit_every > 0 and index % commit_every == 0:
            conn.commit()
    conn.commit()
    return pm_count, ks_count


def write_edges(conn, args: argparse.Namespace, pairs: list[Any]) -> tuple[int, int, list[str]]:
    paired_contract_ids = {int(pair["paired_contract_id"]) for pair in pairs}
    rows, warnings = market_db.compute_edge_snapshots(
        conn,
        max_age_seconds=args.max_age_seconds,
        max_skew_seconds=args.max_skew_seconds,
        ignore_age=args.ignore_age,
        paired_contract_ids=paired_contract_ids,
    )
    alerts = core.alert_rows(rows)
    core.write_latest_csv(rows, core.BINARY_CSV_FIELDS, Path(args.output))
    core.write_latest_csv(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_output))
    core.write_alert_folder(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_dir))
    return len(rows), len(alerts), warnings


def run_once(conn, args: argparse.Namespace) -> tuple[int, int, int, int, list[str]]:
    pairs = selected_pairs(conn, requested_universes(args.universes), args.max_pairs)
    pm_count, ks_count = collect_pairs(conn, pairs, args.ks_depth, args.sleep_between_legs, args.commit_every)
    edge_rows = 0
    alert_rows = 0
    warnings: list[str] = []
    if args.compute_edges:
        edge_rows, alert_rows, warnings = write_edges(conn, args, pairs)
    return len(pairs), pm_count, ks_count, edge_rows, alert_rows, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect fresh orderbooks pair-by-pair for DB safe paired contracts.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--universes", default="all")
    parser.add_argument("--max-pairs", type=int, default=20)
    parser.add_argument("--ks-depth", type=int, default=10)
    parser.add_argument("--sleep-between-legs", type=float, default=0.0)
    parser.add_argument("--commit-every", type=int, default=5)
    parser.add_argument("--compute-edges", action="store_true")
    parser.add_argument("--output", default="data/staging/latest_edges.csv")
    parser.add_argument("--alert-output", default="data/staging/latest_alerts.csv")
    parser.add_argument("--alert-dir", default="data/staging/alerts")
    parser.add_argument("--max-age-seconds", type=float, default=10.0)
    parser.add_argument("--max-skew-seconds", type=float, default=5.0)
    parser.add_argument("--ignore-age", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    if args.max_pairs <= 0:
        parser.error("--max-pairs must be positive")
    if args.ks_depth < 0:
        parser.error("--ks-depth must be >= 0")
    if args.sleep_between_legs < 0 or args.interval <= 0:
        parser.error("sleep/interval values must be non-negative and interval must be positive")

    iteration = 1
    with market_db.connect(Path(args.db)) as conn:
        market_db.init_db(conn)
        while True:
            started = time.monotonic()
            selected, pm_count, ks_count, edge_count, alert_count, warnings = run_once(conn, args)
            counts = market_db.row_counts(conn)
            print(
                "paired orderbook collect complete: "
                f"iteration={iteration}; selected_pairs={selected}; pm_books={pm_count}; ks_books={ks_count}; "
                f"edge_rows={edge_count}; alerts={alert_count}; observations={counts['orderbook_observations']}",
                flush=True,
            )
            if args.show_warnings:
                for warning in warnings:
                    print(f"DB edge warning: {warning}", file=sys.stderr, flush=True)
            if not args.loop or (args.max_iterations and iteration >= args.max_iterations):
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.interval - elapsed))
            iteration += 1


if __name__ == "__main__":
    main()
