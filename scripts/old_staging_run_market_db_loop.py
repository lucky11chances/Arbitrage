#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import old_market_db
import old_staging_collect_market_db
import old_staging_normalize_market_db


STOP = False


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def log(message: str) -> None:
    print(f"{old_market_db.utc_now()} {message}", flush=True)


def run_iteration(conn, adapters, args: argparse.Namespace, iteration: int, metadata_due: bool) -> None:
    started = time.monotonic()
    log(f"db-loop iteration {iteration} start; metadata_due={str(metadata_due).lower()}")
    if metadata_due:
        pm_events = old_staging_collect_market_db.collect_pm_metadata(conn, adapters, args)
        ks_markets = old_staging_collect_market_db.collect_ks_metadata(conn, adapters, args)
        log(f"metadata refreshed; pm_events_seen={pm_events}; ks_markets_seen={ks_markets}")
        if not args.skip_normalize:
            normalized = old_staging_normalize_market_db.normalize_db(conn, args.sports)
            log(
                "metadata normalized; "
                f"pm_normalized={normalized['pm_normalized']}; ks_normalized={normalized['ks_normalized']}"
            )
    if args.metadata_only:
        elapsed = time.monotonic() - started
        log(f"metadata-only iteration complete; elapsed_sec={elapsed:.3f}")
        if elapsed > args.interval:
            log(f"interval warning: iteration exceeded target interval {args.interval:.3f}s")
            return
        sleep_until_next_iteration(args.interval - elapsed)
        return
    pm_books, ks_books = old_staging_collect_market_db.collect_orderbooks(conn, args)
    elapsed = time.monotonic() - started
    log(
        f"orderbooks appended; pm_books={pm_books}; ks_books={ks_books}; "
        f"elapsed_sec={elapsed:.3f}"
    )
    if elapsed > args.interval:
        log(f"interval warning: iteration exceeded target interval {args.interval:.3f}s")
        return
    sleep_until_next_iteration(args.interval - elapsed)


def sleep_until_next_iteration(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not STOP:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuously append PM/KS sports orderbooks into the local SQLite DB.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="all")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--metadata-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--pm-page-limit", type=int, default=200)
    parser.add_argument("--ks-page-limit", type=int, default=200)
    parser.add_argument("--max-pm-pages", type=int, default=0)
    parser.add_argument("--max-ks-pages", type=int, default=0)
    parser.add_argument("--max-orderbooks", type=int, default=0)
    parser.add_argument("--max-pm-orderbooks", type=int, default=0)
    parser.add_argument("--max-ks-orderbooks", type=int, default=0)
    parser.add_argument("--ks-depth", type=int, default=0)
    parser.add_argument("--pm-sleep", type=float, default=0.0)
    parser.add_argument("--ks-sleep", type=float, default=0.0)
    parser.add_argument("--orderbook-commit-every", type=int, default=100)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--skip-initial-metadata", action="store_true")
    parser.add_argument("--skip-normalize", action="store_true")
    parser.add_argument("--metadata-only", action="store_true", help="Refresh PM/KS metadata on schedule without pulling broad raw orderbooks.")
    parser.add_argument("--show-warnings", action="store_true")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        db_path = old_market_db.ensure_legacy_db_path(args.db)
    except ValueError as exc:
        parser.error(str(exc))
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.metadata_refresh_seconds <= 0:
        parser.error("--metadata-refresh-seconds must be positive")
    if args.pm_page_limit <= 0 or args.ks_page_limit <= 0:
        parser.error("page limits must be positive")
    if (
        args.max_orderbooks < 0
        or args.max_pm_orderbooks < 0
        or args.max_ks_orderbooks < 0
        or args.max_pm_pages < 0
        or args.max_ks_pages < 0
        or args.orderbook_commit_every < 0
    ):
        parser.error("max values must be >= 0")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        adapters = old_staging_collect_market_db.parse_adapters(args.sports)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    with old_market_db.connect(db_path) as conn:
        old_market_db.init_db(conn)
        iteration = 1
        next_metadata_refresh = 0.0 if not args.skip_initial_metadata else time.monotonic() + args.metadata_refresh_seconds
        while not STOP:
            now = time.monotonic()
            metadata_due = now >= next_metadata_refresh
            run_iteration(conn, adapters, args, iteration, metadata_due)
            if metadata_due:
                next_metadata_refresh = time.monotonic() + args.metadata_refresh_seconds
            if args.max_iterations and iteration >= args.max_iterations:
                break
            iteration += 1
    log("db-loop stopped")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
