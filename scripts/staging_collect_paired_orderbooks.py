#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import market_db
import pipeline_core as core
import staging_edge_snapshot_builder as edge_builder


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


def fetch_payload(fetcher) -> tuple[dict[str, Any] | None, str, int, str]:
    started = time.monotonic()
    try:
        payload = fetcher()
        collected_ts = market_db.utc_now()
        latency_ms = int((time.monotonic() - started) * 1000)
        return payload, collected_ts, latency_ms, ""
    except Exception as exc:  # noqa: BLE001 - per-instrument fetch errors are recorded in DB.
        collected_ts = market_db.utc_now()
        latency_ms = int((time.monotonic() - started) * 1000)
        return None, collected_ts, latency_ms, str(exc)


def record_payload_or_error(
    conn,
    *,
    venue: str,
    instrument_id: str,
    payload: dict[str, Any] | None,
    collected_ts: str,
    request_path: str,
    depth: int | None,
    latency_ms: int,
    error_message: str,
) -> int:
    if payload is not None:
        return market_db.record_orderbook_success(
            conn,
            venue,
            instrument_id,
            payload,
            collected_ts,
            request_path,
            depth,
            latency_ms,
        )
    observation_id = market_db.record_orderbook_error(
        conn,
        venue,
        instrument_id,
        collected_ts,
        request_path,
        depth,
        latency_ms,
        error_message,
    )
    market_db.record_warning(
        conn,
        "collect_paired_orderbooks",
        f"{venue.upper()} paired orderbook fetch failed: {instrument_id}: {error_message}",
        severity="error",
        context={"venue": venue, "instrument_id": instrument_id},
    )
    return observation_id


def collect_pair(conn, pair, ks_depth: int, sleep_seconds: float = 0.0) -> tuple[int, int]:
    pm_token = str(pair["pm_token_id"])
    ks_ticker = str(pair["ks_market_ticker"])
    pm_payload, pm_collected_ts, pm_latency_ms, pm_error = fetch_payload(
        lambda: core.get_json(core.PM_CLOB, "/book", {"token_id": pm_token})
    )
    if sleep_seconds:
        time.sleep(sleep_seconds)
    ks_payload, ks_collected_ts, ks_latency_ms, ks_error = fetch_payload(
        lambda: core.get_json(core.KALSHI_API, f"/markets/{ks_ticker}/orderbook", {"depth": ks_depth})
    )
    pm_observation_id = record_payload_or_error(
        conn,
        venue="pm",
        instrument_id=pm_token,
        payload=pm_payload,
        collected_ts=pm_collected_ts,
        request_path="/book",
        depth=None,
        latency_ms=pm_latency_ms,
        error_message=pm_error,
    )
    ks_observation_id = record_payload_or_error(
        conn,
        venue="ks",
        instrument_id=ks_ticker,
        payload=ks_payload,
        collected_ts=ks_collected_ts,
        request_path=f"/markets/{ks_ticker}/orderbook",
        depth=ks_depth,
        latency_ms=ks_latency_ms,
        error_message=ks_error,
    )
    return pm_observation_id, ks_observation_id


def observation_by_id(conn, observation_id: int):
    return conn.execute(
        "SELECT * FROM orderbook_observations WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()


def collect_pairs(
    conn,
    pairs: list[Any],
    ks_depth: int,
    sleep_seconds: float,
    commit_every: int,
    *,
    compute_edges: bool,
    max_age_seconds: float,
    max_skew_seconds: float,
    dedupe_edges: bool,
) -> tuple[int, int, edge_builder.InsertStats]:
    pm_count = 0
    ks_count = 0
    edge_stats = edge_builder.InsertStats()
    for index, pair in enumerate(pairs, start=1):
        pm_observation_id, ks_observation_id = collect_pair(conn, pair, ks_depth, sleep_seconds)
        pm_count += 1
        ks_count += 1
        if compute_edges:
            edge_stats = edge_stats.plus(
                edge_builder.insert_edge_snapshot(
                    conn,
                    pair,
                    observation_by_id(conn, pm_observation_id),
                    observation_by_id(conn, ks_observation_id),
                    max_age_seconds=max_age_seconds,
                    max_skew_seconds=max_skew_seconds,
                    dedupe=dedupe_edges,
                )
            )
        if commit_every > 0 and index % commit_every == 0:
            conn.commit()
    conn.commit()
    return pm_count, ks_count, edge_stats


def run_once(conn, args: argparse.Namespace) -> tuple[int, int, int, edge_builder.InsertStats]:
    pairs = selected_pairs(conn, requested_universes(args.universes), args.max_pairs)
    pm_count, ks_count, edge_stats = collect_pairs(
        conn,
        pairs,
        args.ks_depth,
        args.sleep_between_legs,
        args.commit_every,
        compute_edges=args.compute_edges,
        max_age_seconds=args.max_age_seconds,
        max_skew_seconds=args.max_skew_seconds,
        dedupe_edges=not args.no_edge_dedupe,
    )
    return len(pairs), pm_count, ks_count, edge_stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect fresh orderbooks pair-by-pair for DB safe paired contracts.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--universes", default="all")
    parser.add_argument("--max-pairs", type=int, default=20)
    parser.add_argument("--ks-depth", type=int, default=10)
    parser.add_argument("--sleep-between-legs", type=float, default=0.0)
    parser.add_argument("--commit-every", type=int, default=5)
    parser.add_argument(
        "--compute-edges",
        action="store_true",
        help="Insert edge_snapshots from the exact PM/KS observations collected for each pair.",
    )
    parser.add_argument("--no-edge-dedupe", action="store_true")
    parser.add_argument("--output", default="data/staging/latest_edges.csv")
    parser.add_argument("--alert-output", default="data/staging/latest_alerts.csv")
    parser.add_argument("--alert-dir", default="data/staging/alerts")
    parser.add_argument("--max-age-seconds", type=float, default=10.0)
    parser.add_argument("--max-skew-seconds", type=float, default=5.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--busy-timeout-ms", type=int, default=60000)
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    if args.max_pairs <= 0:
        parser.error("--max-pairs must be positive")
    if args.ks_depth < 0:
        parser.error("--ks-depth must be >= 0")
    if args.sleep_between_legs < 0 or args.interval <= 0:
        parser.error("sleep/interval values must be non-negative and interval must be positive")
    if args.max_age_seconds <= 0 or args.max_skew_seconds <= 0:
        parser.error("age/skew windows must be positive")
    if args.busy_timeout_ms <= 0:
        parser.error("--busy-timeout-ms must be positive")

    iteration = 1
    with market_db.connect(Path(args.db)) as conn:
        market_db.init_db(conn)
        conn.execute(f"PRAGMA busy_timeout = {args.busy_timeout_ms}")
        edge_builder.ensure_builder_indexes(conn)
        while True:
            started = time.monotonic()
            selected, pm_count, ks_count, edge_stats = run_once(conn, args)
            print(
                "paired orderbook collect complete: "
                f"iteration={iteration}; selected_pairs={selected}; pm_books={pm_count}; ks_books={ks_count}; "
                f"{edge_builder.format_stats(edge_stats)}",
                flush=True,
            )
            if not args.loop or (args.max_iterations and iteration >= args.max_iterations):
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.interval - elapsed))
            iteration += 1


if __name__ == "__main__":
    main()
