#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import new_ws_orderbook_core as core


def make_book() -> core.PriceBook:
    book = core.PriceBook()
    book.replace(
        bids=[["0.40", "10"], ["0.39", "9"], ["0.38", "8"], ["0.37", "7"]],
        asks=[["0.60", "10"], ["0.61", "9"], ["0.62", "8"], ["0.63", "7"]],
    )
    return book


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark SQLite writer cost for selected WS sports.")
    parser.add_argument("--source-db", default="data/new/new_arb_research.sqlite")
    parser.add_argument("--sports", default=core.DEFAULT_WS_SPORTS)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--depth", type=int, default=4)
    args = parser.parse_args()

    if args.cycles <= 0:
        raise SystemExit("--cycles must be positive")

    try:
        selected_sports = core.normalize_sports_arg(args.sports)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    with core.connect_db(Path(args.source_db)) as source:
        pm_ids = core.active_pm_token_ids(source, selected_sports)
        ks_ids = core.active_ks_market_tickers(source, selected_sports)

    if not pm_ids or not ks_ids:
        raise SystemExit(f"missing metadata for benchmark: pm={len(pm_ids)} ks={len(ks_ids)}")

    with tempfile.TemporaryDirectory() as tmp:
        with core.connect_db(Path(tmp) / "bench.sqlite") as conn:
            pm_books = {instrument_id: make_book() for instrument_id in pm_ids}
            ks_books = {instrument_id: make_book() for instrument_id in ks_ids}

            print(
                f"benchmark_counts sports={args.sports} pm={len(pm_ids)} "
                f"ks={len(ks_ids)} total={len(pm_ids) + len(ks_ids)}"
            )
            elapsed_values: list[float] = []
            for index in range(args.cycles):
                batch_ts = core.utc_now()
                started = time.perf_counter()
                core.flush_books(
                    conn,
                    venue="pm",
                    instrument_ids=pm_ids,
                    books=pm_books,
                    depth=args.depth,
                    max_stale_seconds=999999,
                    request_path="benchmark",
                    write_payloads=False,
                    collected_ts_utc=batch_ts,
                )
                core.flush_books(
                    conn,
                    venue="ks",
                    instrument_ids=ks_ids,
                    books=ks_books,
                    depth=args.depth,
                    max_stale_seconds=999999,
                    request_path="benchmark",
                    write_payloads=False,
                    collected_ts_utc=batch_ts,
                )
                elapsed = time.perf_counter() - started
                elapsed_values.append(elapsed)
                print(f"cycle={index + 1} seconds={elapsed:.3f}")

            observation_count = conn.execute("SELECT COUNT(*) FROM orderbook_observations").fetchone()[0]
            level_count = conn.execute("SELECT COUNT(*) FROM orderbook_levels").fetchone()[0]
            print(f"rows observations={observation_count} levels={level_count}")
            print(
                f"avg_seconds={sum(elapsed_values) / len(elapsed_values):.3f} "
                f"min_seconds={min(elapsed_values):.3f} max_seconds={max(elapsed_values):.3f}"
            )

            if max(elapsed_values) > 2.0:
                print("result=FAIL writer exceeded 2 seconds", file=sys.stderr)
                raise SystemExit(1)
            print("result=PASS writer stayed under 2 seconds")


if __name__ == "__main__":
    main()
