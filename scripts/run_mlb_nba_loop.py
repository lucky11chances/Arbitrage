#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import build_all_snapshots
import pipeline_core as core
import universe_adapters


DEFAULT_SPORTS = ("mlb", "nba")
STOP = False
PAIR_CACHE: dict[str, tuple[float, list[core.PairedContract]]] = {}


def parse_sports(value: str) -> list[str]:
    sports = [sport.strip().lower() for sport in value.split(",") if sport.strip()]
    unknown = [sport for sport in sports if sport not in DEFAULT_SPORTS]
    if unknown:
        raise argparse.ArgumentTypeError(f"this loop only supports mlb,nba; unsupported: {', '.join(unknown)}")
    return sports


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


def run_universe(universe: str, args: argparse.Namespace) -> int:
    pairs, warnings = get_pairs(universe, args)
    rows, quote_warnings = core.build_binary_rows(pairs, args.bbo_workers)
    warnings.extend(quote_warnings)

    output = Path(build_all_snapshots.DEFAULT_OUTPUTS[universe])
    core.write_latest_csv(rows, core.BINARY_CSV_FIELDS, output)
    if not args.no_history:
        core.append_history_csv(
            rows,
            core.BINARY_CSV_FIELDS,
            build_all_snapshots.history_path(universe, args.history_dir),
        )
    if args.show_warnings:
        for warning in warnings:
            log(f"{universe} warning: {warning}")
    log(f"{universe}: wrote {len(rows)} rows to {output}; history={'off' if args.no_history else 'on'}")
    return len(rows)


def get_pairs(universe: str, args: argparse.Namespace) -> tuple[list[core.PairedContract], list[str]]:
    now = time.monotonic()
    cached = PAIR_CACHE.get(universe)
    if cached is not None and now < cached[0]:
        return cached[1], []

    pairs, warnings = universe_adapters.pair_binary_universe(universe, args.pm_limit, args.ks_limit, args.from_date or None)
    PAIR_CACHE[universe] = (now + args.pair_refresh_seconds, pairs)
    warnings.append(f"pair cache refreshed; pairs={len(pairs)}; ttl_sec={args.pair_refresh_seconds:.1f}")
    return pairs, warnings


def run_iteration(args: argparse.Namespace, iteration: int) -> None:
    started = time.monotonic()
    log(f"iteration {iteration} start; sports={','.join(args.sports)}")
    for universe in args.sports:
        try:
            run_universe(universe, args)
        except Exception as exc:  # noqa: BLE001 - keep overnight loop alive after transient API failures.
            log(f"{universe} error: {exc}")
            traceback.print_exc()
    elapsed = time.monotonic() - started
    log(f"iteration {iteration} done; elapsed_sec={elapsed:.3f}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuously build MLB/NBA PM/Kalshi snapshots.")
    parser.add_argument("--sports", type=parse_sports, default=list(DEFAULT_SPORTS))
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--pm-limit", type=int, default=200)
    parser.add_argument("--ks-limit", type=int, default=200)
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--history-dir", default="data/history")
    parser.add_argument("--bbo-workers", type=int, default=12)
    parser.add_argument("--pair-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    if args.bbo_workers <= 0:
        raise SystemExit("--bbo-workers must be positive")
    if args.pair_refresh_seconds <= 0:
        raise SystemExit("--pair-refresh-seconds must be positive")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    iteration = 1
    while not STOP:
        run_iteration(args, iteration)
        if args.max_iterations and iteration >= args.max_iterations:
            break
        iteration += 1
    log("loop stopped")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
