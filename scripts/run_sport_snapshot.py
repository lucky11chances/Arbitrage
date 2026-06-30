#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import build_all_snapshots
import pipeline_core as core
import sports_adapters
import sports_inventory
import sports_pairing


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one Polymarket sport adapter snapshot.")
    parser.add_argument("--sport", required=True, help="Sport adapter key, e.g. tennis, formula_1, ufc, mlb, world_cup.")
    parser.add_argument("--pm-limit", type=int, default=200)
    parser.add_argument("--ks-limit", type=int, default=200)
    parser.add_argument("--inventory-limit", type=int, default=200)
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--history-dir", default="data/history")
    parser.add_argument("--bbo-workers", type=int, default=12)
    parser.add_argument("--alert-dir", default=build_all_snapshots.ALERT_DIR)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    adapter = sports_adapters.get_adapter(args.sport)
    inventory_rows, inventory_warnings = adapter.build_inventory_rows(args.inventory_limit)
    sport_output = Path(build_all_snapshots.SPORT_OUTPUT_DIR) / f"{adapter.key}_latest.csv"
    core.write_latest_csv(inventory_rows, sports_inventory.INVENTORY_FIELDS, sport_output)
    if inventory_rows and not args.no_history:
        core.append_history_csv(
            inventory_rows,
            sports_inventory.INVENTORY_FIELDS,
            build_all_snapshots.sport_history_path(adapter, args.history_dir),
        )
    if args.show_warnings:
        for warning in inventory_warnings:
            print(f"{adapter.key} warning: {warning}")
    print(f"{adapter.key}: wrote {len(inventory_rows)} inventory rows to {sport_output}")

    diagnostic_rows, diagnostic_warnings = adapter.build_pairing_diagnostics(args.pm_limit, args.ks_limit, args.from_date or None)
    core.write_latest_csv(diagnostic_rows, sports_pairing.DIAGNOSTIC_FIELDS, Path(build_all_snapshots.PAIRING_DIAGNOSTICS_OUTPUT))
    if args.show_warnings:
        for warning in diagnostic_warnings:
            print(f"{adapter.key} diagnostics warning: {warning}")
    print(f"{adapter.key}: wrote {len(diagnostic_rows)} pairing diagnostics rows to {build_all_snapshots.PAIRING_DIAGNOSTICS_OUTPUT}")

    if adapter.category.arb_status != "paired":
        print(f"{adapter.key}: inventory-only; no arb snapshot written")
        return

    pairs, pair_warnings = adapter.pair_contracts(args.pm_limit, args.ks_limit, args.from_date or None)
    rows, quote_warnings = core.build_binary_rows(pairs, args.bbo_workers)
    warnings = pair_warnings + quote_warnings
    if args.show_warnings:
        for warning in warnings:
            print(f"{adapter.key} warning: {warning}")
    arb_output = Path(build_all_snapshots.DEFAULT_OUTPUTS[adapter.category.arb_universe])
    core.write_latest_csv(rows, core.BINARY_CSV_FIELDS, arb_output)
    if rows and not args.no_history:
        core.append_history_csv(
            rows,
            core.BINARY_CSV_FIELDS,
            build_all_snapshots.history_path(adapter.category.arb_universe, args.history_dir),
        )
    alerts = core.alert_rows(rows)
    core.write_latest_csv(alerts, core.BINARY_CSV_FIELDS, Path(build_all_snapshots.ALERT_OUTPUT))
    core.write_alert_folder(alerts, core.BINARY_CSV_FIELDS, Path(args.alert_dir))
    print(f"{adapter.key}: wrote {len(rows)} arb rows to {arb_output}; alerts={len(alerts)}")


if __name__ == "__main__":
    main()
