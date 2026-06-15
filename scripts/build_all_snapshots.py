#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pipeline_core as core
import universe_adapters


BINARY_UNIVERSES = ("mlb", "nba", "cs2", "lol", "valorant")
SUPPORTED_UNIVERSES = (*BINARY_UNIVERSES, "soccer")
DEFAULT_OUTPUTS = {
    "mlb": "data/mlb_arb_snapshot_latest.csv",
    "nba": "data/nba_arb_snapshot_latest.csv",
    "cs2": "data/cs2_arb_snapshot_latest.csv",
    "lol": "data/lol_arb_snapshot_latest.csv",
    "valorant": "data/valorant_arb_snapshot_latest.csv",
    "soccer": "data/worldcup_soccer_snapshot_latest.csv",
}
HISTORY_PREFIXES = {
    "mlb": "mlb_arb_snapshot",
    "nba": "nba_arb_snapshot",
    "cs2": "cs2_arb_snapshot",
    "lol": "lol_arb_snapshot",
    "valorant": "valorant_arb_snapshot",
    "soccer": "worldcup_soccer_snapshot",
}


def parse_universes(value: str) -> list[str]:
    universes = [universe.strip().lower() for universe in value.split(",") if universe.strip()]
    unknown = [universe for universe in universes if universe not in SUPPORTED_UNIVERSES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unsupported universes: {', '.join(unknown)}")
    return universes


def print_warnings(universe: str, warnings: list[str], show_warnings: bool) -> None:
    if not show_warnings:
        return
    for warning in warnings:
        print(f"{universe} warning: {warning}", file=sys.stderr)


def history_path(universe: str, history_dir: str) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return Path(history_dir) / f"{HISTORY_PREFIXES[universe]}_{today}.csv"


def run_binary_universe(universe: str, args: argparse.Namespace) -> int:
    pairs, warnings = universe_adapters.pair_binary_universe(universe, args.pm_limit, args.ks_limit, args.from_date or None)
    rows, quote_warnings = core.build_binary_rows(pairs)
    warnings.extend(quote_warnings)
    output = Path(DEFAULT_OUTPUTS[universe])
    core.write_latest_csv(rows, core.BINARY_CSV_FIELDS, output)
    if not args.no_history:
        core.append_history_csv(rows, core.BINARY_CSV_FIELDS, history_path(universe, args.history_dir))
    print_warnings(universe, warnings, args.show_warnings)
    print_result(universe, len(rows), output, not args.no_history)
    return len(rows)


def run_soccer(args: argparse.Namespace) -> int:
    rows = universe_adapters.build_worldcup_soccer_rows(args.pm_limit, args.ks_limit)
    output = Path(DEFAULT_OUTPUTS["soccer"])
    core.write_latest_csv(rows, core.SOCCER_COMPAT_CSV_FIELDS, output)
    if not args.no_history:
        core.append_history_csv(rows, core.SOCCER_COMPAT_CSV_FIELDS, history_path("soccer", args.history_dir))
    print_result("soccer", len(rows), output, not args.no_history)
    pm_rows = sum(1 for row in rows if row["source"] == "polymarket")
    ks_rows = sum(1 for row in rows if row["source"] == "kalshi")
    skipped = sum(1 for row in rows if row["skip_binary_arb"] is True)
    binary = sum(1 for row in rows if row["is_binary_candidate"] is True)
    print(f"soccer summary: PM rows={pm_rows}, KS rows={ks_rows}, binary_candidates={binary}, skipped={skipped}")
    return len(rows)


def print_result(universe: str, row_count: int, output: Path, wrote_history: bool) -> None:
    message = f"{datetime.now(timezone.utc).isoformat()} {universe}: wrote {row_count} rows to {output}"
    if wrote_history:
        message = f"{message}; history appended"
    print(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build all Week 1-2 PM/Kalshi snapshots by universe.")
    parser.add_argument("--sports", type=parse_universes, default=list(SUPPORTED_UNIVERSES))
    parser.add_argument("--pm-limit", type=int, default=200)
    parser.add_argument("--ks-limit", type=int, default=200)
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--history-dir", default="data/history")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    for universe in args.sports:
        if universe == "soccer":
            run_soccer(args)
        else:
            run_binary_universe(universe, args)


if __name__ == "__main__":
    main()
