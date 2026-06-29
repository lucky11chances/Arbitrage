#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import market_db


COMPLETE_OBS_FILTER = """
    status = 'ok'
    AND best_bid_scaled IS NOT NULL
    AND best_ask_scaled IS NOT NULL
    AND best_bid_size_scaled IS NOT NULL
    AND best_ask_size_scaled IS NOT NULL
"""


@dataclass(frozen=True)
class InsertStats:
    inserted: int = 0
    duplicate: int = 0
    missing: int = 0
    stale_or_skewed: int = 0
    incomplete_edge: int = 0
    non_executable: int = 0

    def plus(self, other: "InsertStats") -> "InsertStats":
        return InsertStats(
            inserted=self.inserted + other.inserted,
            duplicate=self.duplicate + other.duplicate,
            missing=self.missing + other.missing,
            stale_or_skewed=self.stale_or_skewed + other.stale_or_skewed,
            incomplete_edge=self.incomplete_edge + other.incomplete_edge,
            non_executable=self.non_executable + other.non_executable,
        )


def parse_universes(value: str) -> set[str]:
    return {part.strip().lower().replace("-", "_") for part in value.split(",") if part.strip()}


def pair_rows(conn, universes: set[str], limit: int = 0) -> list[Any]:
    params: list[Any] = []
    universe_filter = ""
    if universes and "all" not in universes:
        placeholders = ",".join("?" for _ in universes)
        universe_filter = f"AND universe IN ({placeholders})"
        params.extend(sorted(universes))
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
    return conn.execute(
        f"""
        SELECT *
        FROM paired_contracts
        WHERE safe_paired = 1
          {universe_filter}
        ORDER BY universe, event_date, match_name, pm_yes_outcome
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def ensure_builder_indexes(conn) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_edge_snapshots_pair_observations
            ON edge_snapshots(paired_contract_id, pm_observation_id, ks_observation_id)
        """
    )
    conn.commit()


def latest_complete_observation(conn, venue: str, instrument_id: str):
    return conn.execute(
        f"""
        SELECT *
        FROM orderbook_observations
        WHERE venue = ?
          AND instrument_id = ?
          AND {COMPLETE_OBS_FILTER}
        ORDER BY collected_ts_utc DESC, observation_id DESC
        LIMIT 1
        """,
        (venue, instrument_id),
    ).fetchone()


def nearest_complete_observation(conn, venue: str, instrument_id: str, target_ts: str, max_skew_seconds: float):
    target = market_db.parse_iso_ts(target_ts)
    start_ts = (target - timedelta(seconds=max_skew_seconds)).isoformat()
    end_ts = (target + timedelta(seconds=max_skew_seconds)).isoformat()
    return conn.execute(
        f"""
        SELECT *
        FROM orderbook_observations
        WHERE venue = ?
          AND instrument_id = ?
          AND collected_ts_utc BETWEEN ? AND ?
          AND {COMPLETE_OBS_FILTER}
        ORDER BY ABS((julianday(collected_ts_utc) - julianday(?)) * 86400.0), observation_id
        LIMIT 1
        """,
        (venue, instrument_id, start_ts, end_ts, target_ts),
    ).fetchone()


def anchor_observations(conn, venue: str, instrument_id: str, start_ts: str, end_ts: str, limit: int = 0):
    params: list[Any] = [venue, instrument_id, start_ts, end_ts]
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
    return conn.execute(
        f"""
        SELECT *
        FROM orderbook_observations
        WHERE venue = ?
          AND instrument_id = ?
          AND collected_ts_utc >= ?
          AND collected_ts_utc <= ?
          AND {COMPLETE_OBS_FILTER}
        ORDER BY collected_ts_utc, observation_id
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def snapshot_exists(conn, pair_id: int, pm_observation_id: int, ks_observation_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM edge_snapshots
        WHERE paired_contract_id = ?
          AND pm_observation_id = ?
          AND ks_observation_id = ?
        LIMIT 1
        """,
        (pair_id, pm_observation_id, ks_observation_id),
    ).fetchone()
    return row is not None


def insert_edge_snapshot(
    conn,
    pair,
    pm_obs,
    ks_obs,
    *,
    max_age_seconds: float,
    max_skew_seconds: float,
    dedupe: bool,
) -> InsertStats:
    if pm_obs is None or ks_obs is None:
        return InsertStats(missing=1)

    pair_id = int(pair["paired_contract_id"])
    pm_observation_id = int(pm_obs["observation_id"])
    ks_observation_id = int(ks_obs["observation_id"])
    if dedupe and snapshot_exists(conn, pair_id, pm_observation_id, ks_observation_id):
        return InsertStats(duplicate=1)

    pm_ts = str(pm_obs["collected_ts_utc"])
    ks_ts = str(ks_obs["collected_ts_utc"])
    snapshot_ts = max(market_db.parse_iso_ts(pm_ts), market_db.parse_iso_ts(ks_ts)).isoformat()
    book_age = max(
        (market_db.parse_iso_ts(snapshot_ts) - market_db.parse_iso_ts(pm_ts)).total_seconds(),
        (market_db.parse_iso_ts(snapshot_ts) - market_db.parse_iso_ts(ks_ts)).total_seconds(),
    )
    skew = market_db.seconds_between(pm_ts, ks_ts)
    if book_age > max_age_seconds or skew > max_skew_seconds:
        return InsertStats(stale_or_skewed=1)

    edge = market_db.compute_edge(pm_obs, ks_obs)
    if edge.net_edge is None:
        return InsertStats(incomplete_edge=1)
    if edge.best_leg_bbo_size is None or edge.best_leg_bbo_size <= Decimal("0"):
        return InsertStats(non_executable=1)

    alert = edge.net_edge > Decimal("0")
    conn.execute(
        """
        INSERT INTO edge_snapshots (
            paired_contract_id, ts_utc, pm_observation_id, ks_observation_id,
            pm_bid_scaled, pm_ask_scaled, pm_bid_size_scaled, pm_ask_size_scaled,
            ks_bid_scaled, ks_ask_scaled, ks_bid_size_scaled, ks_ask_size_scaled,
            best_leg, gross_cost_scaled, net_edge_scaled, best_leg_bbo_size_scaled,
            net_profit_at_bbo_scaled, alert, alert_reason, book_age_seconds,
            snapshot_skew_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pair_id,
            snapshot_ts,
            pm_observation_id,
            ks_observation_id,
            pm_obs["best_bid_scaled"],
            pm_obs["best_ask_scaled"],
            pm_obs["best_bid_size_scaled"],
            pm_obs["best_ask_size_scaled"],
            ks_obs["best_bid_scaled"],
            ks_obs["best_ask_scaled"],
            ks_obs["best_bid_size_scaled"],
            ks_obs["best_ask_size_scaled"],
            edge.best_leg,
            market_db.to_scaled(edge.gross_cost),
            market_db.to_scaled(edge.net_edge),
            market_db.to_scaled(edge.best_leg_bbo_size),
            market_db.to_scaled(edge.net_profit_at_bbo),
            "ALERT" if alert else "",
            "net_edge_positive" if alert else "",
            book_age,
            skew,
        ),
    )
    return InsertStats(inserted=1)


def build_latest_once(conn, pairs, args) -> InsertStats:
    total = InsertStats()
    now_ts = market_db.utc_now()
    for pair in pairs:
        pm_obs = latest_complete_observation(conn, "pm", str(pair["pm_token_id"]))
        ks_obs = latest_complete_observation(conn, "ks", str(pair["ks_market_ticker"]))
        if pm_obs is None or ks_obs is None:
            total = total.plus(InsertStats(missing=1))
            continue
        current_age = max(
            market_db.age_seconds(str(pm_obs["collected_ts_utc"]), now_ts),
            market_db.age_seconds(str(ks_obs["collected_ts_utc"]), now_ts),
        )
        if current_age > args.max_age_seconds:
            total = total.plus(InsertStats(stale_or_skewed=1))
            continue
        total = total.plus(
            insert_edge_snapshot(
                conn,
                pair,
                pm_obs,
                ks_obs,
                max_age_seconds=args.max_age_seconds,
                max_skew_seconds=args.max_skew_seconds,
                dedupe=not args.no_dedupe,
            )
        )
    conn.commit()
    return total


def build_historical(conn, pairs, args) -> InsertStats:
    total = InsertStats()
    anchor_venue = args.anchor_venue
    for index, pair in enumerate(pairs, start=1):
        if anchor_venue == "ks":
            anchors = anchor_observations(
                conn,
                "ks",
                str(pair["ks_market_ticker"]),
                args.start_ts,
                args.end_ts,
                args.max_anchors_per_pair,
            )
            for ks_obs in anchors:
                pm_obs = nearest_complete_observation(
                    conn,
                    "pm",
                    str(pair["pm_token_id"]),
                    str(ks_obs["collected_ts_utc"]),
                    args.max_skew_seconds,
                )
                total = total.plus(
                    insert_edge_snapshot(
                        conn,
                        pair,
                        pm_obs,
                        ks_obs,
                        max_age_seconds=args.max_age_seconds,
                        max_skew_seconds=args.max_skew_seconds,
                        dedupe=not args.no_dedupe,
                    )
                )
        else:
            anchors = anchor_observations(
                conn,
                "pm",
                str(pair["pm_token_id"]),
                args.start_ts,
                args.end_ts,
                args.max_anchors_per_pair,
            )
            for pm_obs in anchors:
                ks_obs = nearest_complete_observation(
                    conn,
                    "ks",
                    str(pair["ks_market_ticker"]),
                    str(pm_obs["collected_ts_utc"]),
                    args.max_skew_seconds,
                )
                total = total.plus(
                    insert_edge_snapshot(
                        conn,
                        pair,
                        pm_obs,
                        ks_obs,
                        max_age_seconds=args.max_age_seconds,
                        max_skew_seconds=args.max_skew_seconds,
                        dedupe=not args.no_dedupe,
                    )
                )
        if args.commit_every > 0 and index % args.commit_every == 0:
            conn.commit()
            print(f"historical progress: pairs={index}; {format_stats(total)}", flush=True)
    conn.commit()
    return total


def format_stats(stats: InsertStats) -> str:
    return (
        f"inserted={stats.inserted}; duplicate={stats.duplicate}; missing={stats.missing}; "
        f"stale_or_skewed={stats.stale_or_skewed}; incomplete_edge={stats.incomplete_edge}; "
        f"non_executable={stats.non_executable}"
    )


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--universes", default="all")
    parser.add_argument("--max-pairs", type=int, default=0)
    parser.add_argument("--max-age-seconds", type=float, default=10.0)
    parser.add_argument("--max-skew-seconds", type=float, default=5.0)
    parser.add_argument("--no-dedupe", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PM/KS edge snapshots from DB orderbook observations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    latest = subparsers.add_parser("latest-loop", help="Insert latest safe-pair edge snapshots on an interval.")
    add_common_args(latest)
    latest.add_argument("--interval", type=float, default=5.0)
    latest.add_argument("--max-iterations", type=int, default=0)

    historical = subparsers.add_parser("historical-backfill", help="Backfill edge snapshots from historical raw books.")
    add_common_args(historical)
    historical.add_argument("--start-ts", required=True)
    historical.add_argument("--end-ts", required=True)
    historical.add_argument("--anchor-venue", choices=("ks", "pm"), default="ks")
    historical.add_argument("--max-anchors-per-pair", type=int, default=0)
    historical.add_argument("--commit-every", type=int, default=10)

    args = parser.parse_args()
    if args.max_pairs < 0:
        parser.error("--max-pairs must be >= 0")
    if args.max_age_seconds <= 0 or args.max_skew_seconds <= 0:
        parser.error("age/skew windows must be positive")

    with market_db.connect(Path(args.db)) as conn:
        market_db.init_db(conn)
        ensure_builder_indexes(conn)
        if args.command == "historical-backfill":
            pairs = pair_rows(conn, parse_universes(args.universes), args.max_pairs)
            stats = build_historical(conn, pairs, args)
            print(f"historical edge snapshot backfill complete: pairs={len(pairs)}; {format_stats(stats)}")
            return

        iteration = 1
        while True:
            started = time.monotonic()
            pairs = pair_rows(conn, parse_universes(args.universes), args.max_pairs)
            stats = build_latest_once(conn, pairs, args)
            print(
                f"latest edge snapshot loop: iteration={iteration}; pairs={len(pairs)}; {format_stats(stats)}",
                flush=True,
            )
            if args.max_iterations and iteration >= args.max_iterations:
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.interval - elapsed))
            iteration += 1


if __name__ == "__main__":
    main()
