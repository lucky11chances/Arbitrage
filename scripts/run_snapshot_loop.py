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
import sports_adapters
import sports_inventory
import sports_pairing
import sports_registry
from sports_adapters.base import SportAdapter


DEFAULT_SPORTS = ("mlb", "nba", "world_cup")
STOP = False
PAIR_CACHE: dict[str, tuple[float, list[core.PairedContract]]] = {}
DIAGNOSTICS_CACHE: dict[str, tuple[float, list[dict[str, object]]]] = {}
INVENTORY_NEXT_REFRESH = 0.0


def parse_adapters(value: str) -> list[SportAdapter]:
    try:
        return sports_adapters.select_adapters(value)
    except (KeyError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


def run_adapter(adapter: SportAdapter, args: argparse.Namespace) -> list[dict[str, object]]:
    universe = adapter.category.arb_universe
    pairs, warnings = get_pairs(adapter, args)
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
    positive_edges, max_edge = edge_summary(rows)
    log(
        f"{universe}: wrote {len(rows)} rows to {output}; "
        f"history={'off' if args.no_history else 'on'}; "
        f"positive_edges={positive_edges}; max_edge={max_edge}"
    )
    return rows


def edge_summary(rows: list[dict[str, object]]) -> tuple[int, float | None]:
    edges = [float(row["net_edge"]) for row in rows if row.get("net_edge") not in (None, "")]
    return sum(1 for edge in edges if edge > 0), max(edges) if edges else None


def get_pairs(adapter: SportAdapter, args: argparse.Namespace) -> tuple[list[core.PairedContract], list[str]]:
    now = time.monotonic()
    cached = PAIR_CACHE.get(adapter.key)
    if cached is not None and now < cached[0]:
        return cached[1], []

    pairs, warnings = adapter.pair_contracts(args.pm_limit, args.ks_limit, args.from_date or None)
    PAIR_CACHE[adapter.key] = (now + args.pair_refresh_seconds, pairs)
    warnings.append(f"pair cache refreshed; pairs={len(pairs)}; ttl_sec={args.pair_refresh_seconds:.1f}")
    return pairs, warnings


def get_pairing_diagnostics(adapter: SportAdapter, args: argparse.Namespace) -> tuple[list[dict[str, object]], list[str]]:
    now = time.monotonic()
    cached = DIAGNOSTICS_CACHE.get(adapter.key)
    if cached is not None and now < cached[0]:
        return cached[1], []

    rows, warnings = adapter.build_pairing_diagnostics(args.pm_limit, args.ks_limit, args.from_date or None)
    DIAGNOSTICS_CACHE[adapter.key] = (now + args.pair_refresh_seconds, rows)
    warnings.append(f"diagnostics cache refreshed; rows={len(rows)}; ttl_sec={args.pair_refresh_seconds:.1f}")
    return rows, warnings


def run_iteration(args: argparse.Namespace, iteration: int) -> None:
    started = time.monotonic()
    paired = sports_adapters.paired_adapters(args.adapters)
    log(f"iteration {iteration} start; adapters={','.join(adapter.key for adapter in args.adapters)}")
    maybe_write_inventory_csv(args)
    all_rows: list[dict[str, object]] = []
    diagnostic_rows: list[dict[str, object]] = []
    for adapter in args.adapters:
        try:
            rows, warnings = get_pairing_diagnostics(adapter, args)
            diagnostic_rows.extend(rows)
            if args.show_warnings:
                for warning in warnings:
                    log(f"{adapter.key} diagnostics warning: {warning}")
        except Exception as exc:  # noqa: BLE001 - diagnostics must not stop executable pair snapshots.
            log(f"{adapter.key} diagnostics error: {exc}")
            traceback.print_exc()
    write_pairing_diagnostics_csv(diagnostic_rows)
    for adapter in paired:
        try:
            all_rows.extend(run_adapter(adapter, args))
        except Exception as exc:  # noqa: BLE001 - keep collector alive after transient API failures.
            log(f"{adapter.key} error: {exc}")
            traceback.print_exc()
    alert_count = write_alert_csv(all_rows, args)
    if alert_count:
        log(f"ALERT: wrote {alert_count} positive-edge rows to {build_all_snapshots.ALERT_OUTPUT}")
    else:
        log(f"alerts: wrote 0 rows to {build_all_snapshots.ALERT_OUTPUT}")
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


def write_alert_csv(rows: list[dict[str, object]], args: argparse.Namespace) -> int:
    alerts = core.alert_rows(rows)
    core.write_latest_csv(alerts, core.BINARY_CSV_FIELDS, Path(build_all_snapshots.ALERT_OUTPUT))
    core.write_alert_folder(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_dir))
    if alerts and not args.no_history:
        core.append_history_csv(alerts, core.BINARY_CSV_FIELDS, build_all_snapshots.alert_history_path(args.history_dir))
    return len(alerts)


def write_pairing_diagnostics_csv(rows: list[dict[str, object]]) -> None:
    core.write_latest_csv(rows, sports_pairing.DIAGNOSTIC_FIELDS, Path(build_all_snapshots.PAIRING_DIAGNOSTICS_OUTPUT))
    log(f"diagnostics: wrote {len(rows)} rows to {build_all_snapshots.PAIRING_DIAGNOSTICS_OUTPUT}")


def maybe_write_inventory_csv(args: argparse.Namespace) -> None:
    global INVENTORY_NEXT_REFRESH
    if not args.inventory_enabled:
        return
    now = time.monotonic()
    if now < INVENTORY_NEXT_REFRESH:
        return
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for adapter in args.adapters:
        adapter_rows, adapter_warnings = adapter.build_inventory_rows(args.inventory_limit)
        rows.extend(adapter_rows)
        warnings.extend(adapter_warnings)
        output = Path(build_all_snapshots.SPORT_OUTPUT_DIR) / f"{adapter.key}_latest.csv"
        core.write_latest_csv(adapter_rows, sports_inventory.INVENTORY_FIELDS, output)
        if adapter_rows and not args.no_history:
            core.append_history_csv(adapter_rows, sports_inventory.INVENTORY_FIELDS, build_all_snapshots.sport_history_path(adapter, args.history_dir))
    if sports_registry.sports_all_requested(args.sports_raw):
        core.write_latest_csv(rows, sports_inventory.INVENTORY_FIELDS, Path(build_all_snapshots.INVENTORY_OUTPUT))
    if rows and not args.no_history and sports_registry.sports_all_requested(args.sports_raw):
        core.append_history_csv(
            rows,
            sports_inventory.INVENTORY_FIELDS,
            build_all_snapshots.inventory_history_path(args.history_dir),
        )
    if args.show_warnings:
        for warning in warnings:
            log(f"inventory warning: {warning}")
    INVENTORY_NEXT_REFRESH = now + args.inventory_refresh_seconds
    log(
        f"inventory: wrote {len(rows)} rows to {build_all_snapshots.INVENTORY_OUTPUT}; "
        f"history={'off' if args.no_history else 'on'}; ttl_sec={args.inventory_refresh_seconds:.1f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuously build MLB/NBA/World Cup PM/Kalshi snapshots.")
    parser.add_argument("--sports", default=",".join(DEFAULT_SPORTS))
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--pm-limit", type=int, default=200)
    parser.add_argument("--ks-limit", type=int, default=200)
    parser.add_argument("--inventory-limit", type=int, default=200)
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--history-dir", default="data/history")
    parser.add_argument("--alert-dir", default=build_all_snapshots.ALERT_DIR)
    parser.add_argument("--bbo-workers", type=int, default=12)
    parser.add_argument("--pair-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--inventory-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--show-warnings", action="store_true")
    parser.add_argument("--inventory", action="store_true", help="Also write the Polymarket all-sports inventory CSV.")
    parser.add_argument("--no-inventory", action="store_true", help="Disable inventory writes even when --sports all is used.")
    args = parser.parse_args()
    args.sports_raw = args.sports
    try:
        args.adapters = parse_adapters(args.sports_raw)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    args.inventory_enabled = False if args.no_inventory else (
        args.inventory or sports_registry.sports_all_requested(args.sports_raw)
    )

    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    if args.bbo_workers <= 0:
        raise SystemExit("--bbo-workers must be positive")
    if args.pair_refresh_seconds <= 0:
        raise SystemExit("--pair-refresh-seconds must be positive")
    if args.inventory_refresh_seconds <= 0:
        raise SystemExit("--inventory-refresh-seconds must be positive")

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
