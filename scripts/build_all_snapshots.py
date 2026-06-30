#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pipeline_core as core
import sports_adapters
import sports_inventory
import sports_pairing
import sports_registry
from sports_adapters.base import SportAdapter


BINARY_UNIVERSES = ("mlb", "nba", "soccer", "formula_1", "cs2", "lol", "valorant")
SUPPORTED_UNIVERSES = BINARY_UNIVERSES
DEFAULT_OUTPUTS = {
    "mlb": "data/mlb_arb_snapshot_latest.csv",
    "nba": "data/nba_arb_snapshot_latest.csv",
    "cs2": "data/cs2_arb_snapshot_latest.csv",
    "lol": "data/lol_arb_snapshot_latest.csv",
    "valorant": "data/valorant_arb_snapshot_latest.csv",
    "soccer": "data/worldcup_soccer_snapshot_latest.csv",
    "formula_1": "data/formula_1_arb_snapshot_latest.csv",
}
ALERT_OUTPUT = "data/arb_alerts_latest.csv"
ALERT_DIR = "data/alerts"
INVENTORY_OUTPUT = "data/polymarket_sports_inventory_latest.csv"
PAIRING_DIAGNOSTICS_OUTPUT = "data/pairing_diagnostics_latest.csv"
SPORT_OUTPUT_DIR = "data/sports"
HISTORY_PREFIXES = {
    "mlb": "mlb_arb_snapshot",
    "nba": "nba_arb_snapshot",
    "cs2": "cs2_arb_snapshot",
    "lol": "lol_arb_snapshot",
    "valorant": "valorant_arb_snapshot",
    "soccer": "worldcup_soccer_snapshot",
    "formula_1": "formula_1_arb_snapshot",
}
ALERT_HISTORY_PREFIX = "arb_alerts"
INVENTORY_HISTORY_PREFIX = "polymarket_sports_inventory"
SPORT_HISTORY_PREFIX = "sports"


def parse_adapters(value: str) -> list[SportAdapter]:
    try:
        return sports_adapters.select_adapters(value)
    except (KeyError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def print_warnings(universe: str, warnings: list[str], show_warnings: bool) -> None:
    if not show_warnings:
        return
    for warning in warnings:
        print(f"{universe} warning: {warning}", file=sys.stderr)


def history_path(universe: str, history_dir: str) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return Path(history_dir) / f"{HISTORY_PREFIXES[universe]}_{today}.csv"


def sport_history_path(adapter: SportAdapter, history_dir: str) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return Path(history_dir) / SPORT_HISTORY_PREFIX / f"{adapter.key}_{today}.csv"


def run_binary_adapter(adapter: SportAdapter, args: argparse.Namespace) -> list[dict[str, object]]:
    universe = adapter.category.arb_universe
    pairs, warnings = adapter.pair_contracts(args.pm_limit, args.ks_limit, args.from_date or None)
    rows, quote_warnings = core.build_binary_rows(pairs)
    warnings.extend(quote_warnings)
    output = Path(DEFAULT_OUTPUTS[universe])
    core.write_latest_csv(rows, core.BINARY_CSV_FIELDS, output)
    if not args.no_history:
        core.append_history_csv(rows, core.BINARY_CSV_FIELDS, history_path(universe, args.history_dir))
    print_warnings(universe, warnings, args.show_warnings)
    print_result(universe, len(rows), output, not args.no_history)
    print_edge_summary(universe, rows)
    return rows


def run_sport_inventory(adapter: SportAdapter, args: argparse.Namespace) -> list[dict[str, object]]:
    rows, warnings = adapter.build_inventory_rows(args.inventory_limit)
    output = Path(SPORT_OUTPUT_DIR) / f"{adapter.key}_latest.csv"
    core.write_latest_csv(rows, sports_inventory.INVENTORY_FIELDS, output)
    if rows and not args.no_history:
        core.append_history_csv(rows, sports_inventory.INVENTORY_FIELDS, sport_history_path(adapter, args.history_dir))
    print_warnings(adapter.key, warnings, args.show_warnings)
    print_result(adapter.key, len(rows), output, not args.no_history)
    return rows


def run_pairing_diagnostics(adapter: SportAdapter, args: argparse.Namespace) -> list[dict[str, object]]:
    rows, warnings = adapter.build_pairing_diagnostics(args.pm_limit, args.ks_limit, args.from_date or None)
    print_warnings(f"{adapter.key} diagnostics", warnings, args.show_warnings)
    return rows


def alert_history_path(history_dir: str) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return Path(history_dir) / f"{ALERT_HISTORY_PREFIX}_{today}.csv"


def inventory_history_path(history_dir: str) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return Path(history_dir) / f"{INVENTORY_HISTORY_PREFIX}_{today}.csv"


def write_alert_csv(rows: list[dict[str, object]], args: argparse.Namespace) -> int:
    alerts = core.alert_rows(rows)
    core.write_latest_csv(alerts, core.BINARY_CSV_FIELDS, Path(ALERT_OUTPUT))
    core.write_alert_folder(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_dir))
    if alerts and not args.no_history:
        core.append_history_csv(alerts, core.BINARY_CSV_FIELDS, alert_history_path(args.history_dir))
    return len(alerts)


def write_inventory_csv(args: argparse.Namespace, rows: list[dict[str, object]] | None = None) -> int:
    warnings: list[str] = []
    if rows is None:
        rows, warnings = sports_inventory.build_polymarket_sports_inventory(args.inventory_limit)
    core.write_latest_csv(rows, sports_inventory.INVENTORY_FIELDS, Path(INVENTORY_OUTPUT))
    if rows and not args.no_history:
        core.append_history_csv(rows, sports_inventory.INVENTORY_FIELDS, inventory_history_path(args.history_dir))
    print_warnings("inventory", warnings, args.show_warnings)
    print(f"{datetime.now(timezone.utc).isoformat()} inventory: wrote {len(rows)} rows to {INVENTORY_OUTPUT}")
    return len(rows)


def write_pairing_diagnostics_csv(rows: list[dict[str, object]]) -> int:
    core.write_latest_csv(rows, sports_pairing.DIAGNOSTIC_FIELDS, Path(PAIRING_DIAGNOSTICS_OUTPUT))
    print(f"{datetime.now(timezone.utc).isoformat()} diagnostics: wrote {len(rows)} rows to {PAIRING_DIAGNOSTICS_OUTPUT}")
    return len(rows)


def should_write_inventory(args: argparse.Namespace) -> bool:
    if args.no_inventory:
        return False
    return args.inventory or sports_registry.sports_all_requested(args.sports_raw)


def print_result(universe: str, row_count: int, output: Path, wrote_history: bool) -> None:
    message = f"{datetime.now(timezone.utc).isoformat()} {universe}: wrote {row_count} rows to {output}"
    if wrote_history:
        message = f"{message}; history appended"
    print(message)


def print_edge_summary(universe: str, rows: list[dict[str, object]]) -> None:
    edges = [float(row["net_edge"]) for row in rows if row.get("net_edge") not in (None, "")]
    positive_edges = sum(1 for edge in edges if edge > 0)
    max_edge = max(edges) if edges else None
    print(f"{universe} summary: positive_edges={positive_edges}; max_edge={max_edge}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build all Week 1-2 PM/Kalshi snapshots by universe.")
    parser.add_argument("--sports", default="all")
    parser.add_argument("--pm-limit", type=int, default=200)
    parser.add_argument("--ks-limit", type=int, default=200)
    parser.add_argument("--inventory-limit", type=int, default=200)
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--history-dir", default="data/history")
    parser.add_argument("--alert-dir", default=ALERT_DIR)
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

    all_rows: list[dict[str, object]] = []
    inventory_rows: list[dict[str, object]] = []
    diagnostic_rows: list[dict[str, object]] = []
    for adapter in args.adapters:
        inventory_rows.extend(run_sport_inventory(adapter, args))
        diagnostic_rows.extend(run_pairing_diagnostics(adapter, args))
        if adapter.category.arb_status == "paired":
            all_rows.extend(run_binary_adapter(adapter, args))
    write_pairing_diagnostics_csv(diagnostic_rows)
    alert_count = write_alert_csv(all_rows, args)
    print(f"alert summary: wrote {alert_count} rows to {ALERT_OUTPUT}")
    if should_write_inventory(args):
        write_inventory_csv(args, inventory_rows if sports_registry.sports_all_requested(args.sports_raw) else None)


if __name__ == "__main__":
    main()
