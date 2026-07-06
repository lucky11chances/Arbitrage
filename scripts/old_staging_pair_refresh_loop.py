#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import old_market_db
import old_staging_pair_from_db


STOP = False
LOCK_HANDLE = None


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def sleep_until_next_iteration(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not STOP:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.5))


def acquire_single_instance_lock(lock_file: Path) -> None:
    global LOCK_HANDLE
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    LOCK_HANDLE = lock_file.open("w")
    try:
        fcntl.flock(LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"another paired refresh loop is already running; lock_file={lock_file}", file=sys.stderr)
        raise SystemExit(2)
    LOCK_HANDLE.write(f"pid={os.getpid()} started_ts={old_market_db.utc_now()}\n")
    LOCK_HANDLE.flush()


def run_once(conn, args: argparse.Namespace) -> tuple[int, int, float]:
    started = time.monotonic()
    pairs, diagnostics, warnings, counts = old_staging_pair_from_db.refresh_pairs_incremental(
        conn,
        sports=args.sports,
        from_date=args.from_date,
    )
    old_staging_pair_from_db.write_pairing_outputs(
        pairs,
        diagnostics,
        pairs_output=args.pairs_output,
        diagnostics_output=args.diagnostics_output,
    )
    if args.show_warnings:
        for warning in warnings:
            print(f"DB pair refresh warning: {warning}", file=sys.stderr)
    elapsed = time.monotonic() - started
    candidate_count = sum(1 for row in diagnostics if row.get("status") == "candidate_match")
    print(
        f"{old_market_db.utc_now()} incremental pair refresh complete: "
        f"safe_pairs={counts['safe_pairs']}; disabled_pairs={counts.get('disabled_pairs', 0)}; "
        f"candidate_matches={candidate_count}; "
        f"elapsed_sec={elapsed:.3f}; pairs_output={args.pairs_output}; "
        f"diagnostics_output={args.diagnostics_output}",
        flush=True,
    )
    return counts["safe_pairs"], counts.get("disabled_pairs", 0), elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely refresh DB safe pairs incrementally without deleting edge history.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="all")
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--all-dates", action="store_true")
    parser.add_argument("--interval", type=float, default=300.0)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--busy-timeout-ms", type=int, default=60000)
    parser.add_argument("--lock-file", default="data/old/old_locks/old_paired_refresh_loop.lock")
    parser.add_argument("--pairs-output", default="data/old/old_latest_paired_contracts.csv")
    parser.add_argument("--diagnostics-output", default="data/old/old_latest_pairing_diagnostics.csv")
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    if args.all_dates:
        args.from_date = None
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.max_iterations < 0:
        parser.error("--max-iterations must be >= 0")
    if args.busy_timeout_ms <= 0:
        parser.error("--busy-timeout-ms must be positive")

    acquire_single_instance_lock(Path(args.lock_file))

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        conn.execute(f"PRAGMA busy_timeout = {args.busy_timeout_ms}")
        iteration = 1
        while not STOP:
            run_once(conn, args)
            if args.max_iterations and iteration >= args.max_iterations:
                break
            sleep_until_next_iteration(args.interval)
            iteration += 1


if __name__ == "__main__":
    main()
