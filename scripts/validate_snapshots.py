#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


BINARY_FILES = {
    "mlb": Path("data/mlb_arb_snapshot_latest.csv"),
    "nba": Path("data/nba_arb_snapshot_latest.csv"),
    "cs2": Path("data/cs2_arb_snapshot_latest.csv"),
    "lol": Path("data/lol_arb_snapshot_latest.csv"),
    "valorant": Path("data/valorant_arb_snapshot_latest.csv"),
}
SOCCER_FILE = Path("data/worldcup_soccer_snapshot_latest.csv")
BINARY_REQUIRED_FIELDS = [
    "ts_utc",
    "universe",
    "category",
    "match_name",
    "event_date",
    "canonical_event_id",
    "market_type",
    "pm_yes_outcome",
    "ks_yes_outcome",
    "pm_bid",
    "pm_ask",
    "pm_bid_sz",
    "pm_ask_sz",
    "ks_bid",
    "ks_ask",
    "ks_bid_sz",
    "ks_ask_sz",
    "net_edge",
    "best_leg",
    "gross_cost",
    "best_leg_bbo_size",
    "net_profit_at_bbo",
    "pm_event_slug",
    "pm_market_id",
    "pm_token_id",
    "ks_event_ticker",
    "ks_market_ticker",
    "schedule_source",
]
SOCCER_REQUIRED_FIELDS = [
    "ts_utc",
    "source",
    "universe",
    "event_id",
    "title",
    "market_count",
    "outcomes",
    "is_binary_candidate",
    "skip_binary_arb",
    "reason",
]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


def require_fields(path: Path, actual: list[str], expected: list[str]) -> None:
    missing = [field for field in expected if field not in actual]
    if missing:
        raise AssertionError(f"{path} missing fields: {', '.join(missing)}")


def validate_binary(universe: str, path: Path) -> dict[str, Any]:
    fields, rows = read_csv(path)
    require_fields(path, fields, BINARY_REQUIRED_FIELDS)
    if universe == "cs2" and rows:
        raise AssertionError("cs2 must stay header-only until an official schedule adapter is configured")

    max_edge = None
    for index, row in enumerate(rows, start=2):
        if row.get("universe") != universe:
            raise AssertionError(f"{path}:{index} universe mismatch: {row.get('universe')}")
        missing = [field for field in BINARY_REQUIRED_FIELDS if not row.get(field)]
        if missing:
            raise AssertionError(f"{path}:{index} empty required fields: {', '.join(missing)}")
        edge = float(row["net_edge"])
        bbo_size = float(row["best_leg_bbo_size"])
        max_edge = edge if max_edge is None else max(max_edge, edge)
        if edge > 0.30:
            raise AssertionError(f"{path}:{index} net_edge over 30%: {edge}")
        if bbo_size < 0:
            raise AssertionError(f"{path}:{index} best_leg_bbo_size is negative: {bbo_size}")
        if universe in {"lol", "valorant"} and not row["schedule_source"].startswith("riot_"):
            raise AssertionError(f"{path}:{index} esports row lacks Riot official schedule source")
    return {"rows": len(rows), "max_edge": max_edge}


def validate_soccer(path: Path) -> dict[str, Any]:
    fields, rows = read_csv(path)
    require_fields(path, fields, SOCCER_REQUIRED_FIELDS)
    if "net_edge" in fields:
        raise AssertionError("soccer compatibility snapshot must not include net_edge")
    skipped = 0
    binary_candidates = 0
    for index, row in enumerate(rows, start=2):
        if row.get("skip_binary_arb") == "True":
            skipped += 1
        if row.get("is_binary_candidate") == "True":
            binary_candidates += 1
        if row.get("skip_binary_arb") != "True":
            raise AssertionError(f"{path}:{index} soccer row must skip binary arb")
        if not row.get("reason"):
            raise AssertionError(f"{path}:{index} soccer skip row lacks reason")
    return {"rows": len(rows), "binary_candidates": binary_candidates, "skipped": skipped}


def main() -> None:
    summaries: dict[str, dict[str, Any]] = {}
    for universe, path in BINARY_FILES.items():
        summaries[universe] = validate_binary(universe, path)
    summaries["soccer"] = validate_soccer(SOCCER_FILE)
    for universe, summary in summaries.items():
        details = ", ".join(f"{key}={value}" for key, value in summary.items())
        print(f"{universe}: ok ({details})")


if __name__ == "__main__":
    main()
