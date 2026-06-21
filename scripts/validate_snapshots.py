#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import sports_inventory
import sports_registry
import universe_adapters


BINARY_FILES = {
    "mlb": Path("data/mlb_arb_snapshot_latest.csv"),
    "nba": Path("data/nba_arb_snapshot_latest.csv"),
    "soccer": Path("data/worldcup_soccer_snapshot_latest.csv"),
    "cs2": Path("data/cs2_arb_snapshot_latest.csv"),
    "lol": Path("data/lol_arb_snapshot_latest.csv"),
    "valorant": Path("data/valorant_arb_snapshot_latest.csv"),
}
INVENTORY_FILE = Path("data/polymarket_sports_inventory_latest.csv")
ALERT_DIR = Path("data/alerts")
SPORT_FILES = {
    category.key: Path("data/sports") / f"{category.key}_latest.csv"
    for category in sports_registry.inventory_categories()
}
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
INVENTORY_REQUIRED_FIELDS = sports_inventory.INVENTORY_FIELDS


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
        validate_alert_fields(path, index, row, edge)
        if edge > 0.30:
            raise AssertionError(f"{path}:{index} net_edge over 30%: {edge}")
        if bbo_size < 0:
            raise AssertionError(f"{path}:{index} best_leg_bbo_size is negative: {bbo_size}")
        if universe == "soccer":
            validate_soccer_binary_row(path, index, row)
        if universe in {"lol", "valorant"} and not row["schedule_source"].startswith("riot_"):
            raise AssertionError(f"{path}:{index} esports row lacks Riot official schedule source")
    return {"rows": len(rows), "max_edge": max_edge}


def validate_soccer_binary_row(path: Path, index: int, row: dict[str, str]) -> None:
    if row["market_type"] not in {"team_win_90min", "draw_90min"}:
        raise AssertionError(f"{path}:{index} soccer row has unexpected market_type: {row['market_type']}")
    if row["schedule_source"] != universe_adapters.WORLDCUP_SCHEDULE_SOURCE:
        raise AssertionError(f"{path}:{index} soccer row lacks local World Cup schedule source")
    if row["canonical_event_id"] not in universe_adapters.worldcup_schedule_by_id():
        raise AssertionError(f"{path}:{index} soccer row does not map to local schedule: {row['canonical_event_id']}")
    pm_outcome = universe_adapters.soccer_outcome_key(row["pm_yes_outcome"])
    ks_outcome = universe_adapters.soccer_outcome_key(row["ks_yes_outcome"])
    if pm_outcome != ks_outcome:
        raise AssertionError(
            f"{path}:{index} soccer PM/KS team mismatch: {row['pm_yes_outcome']} vs {row['ks_yes_outcome']}"
        )
    if row["market_type"] == "draw_90min" and pm_outcome != "draw":
        raise AssertionError(f"{path}:{index} soccer draw row does not use draw outcome")
    if row["market_type"] == "team_win_90min" and pm_outcome == "draw":
        raise AssertionError(f"{path}:{index} soccer team-win row uses draw outcome")


def validate_alert_fields(path: Path, index: int, row: dict[str, str], edge: float) -> None:
    if "alert" not in row:
        return
    alert = row.get("alert", "")
    if edge > 0 and alert != "ALERT":
        raise AssertionError(f"{path}:{index} positive net_edge missing ALERT marker")
    if edge <= 0 and alert:
        raise AssertionError(f"{path}:{index} non-positive net_edge has alert marker: {alert}")


def validate_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"rows": 0, "status": "missing"}
    fields, rows = read_csv(path)
    require_fields(path, fields, INVENTORY_REQUIRED_FIELDS)
    categories: set[str] = set()
    for index, row in enumerate(rows, start=2):
        missing = [field for field in ("source", "category_key", "category_label", "tag_slug", "event_slug", "event_title") if not row.get(field)]
        if missing:
            raise AssertionError(f"{path}:{index} empty inventory fields: {', '.join(missing)}")
        if row["source"] != "polymarket":
            raise AssertionError(f"{path}:{index} unexpected source: {row['source']}")
        if row.get("arb_enabled") == "true" and not row.get("arb_universe"):
            raise AssertionError(f"{path}:{index} arb-enabled inventory row lacks arb_universe")
        if row.get("market_type_guess") not in {
            "three_way_moneyline",
            "binary_spread",
            "binary_total",
            "binary_yes_no",
            "total",
            "two_outcome_winner",
            "multi_outcome",
            "unknown",
        }:
            raise AssertionError(f"{path}:{index} unexpected market_type_guess: {row.get('market_type_guess')}")
        categories.add(row["category_key"])
    return {"rows": len(rows), "categories": len(categories), "status": "ok"}


def validate_sport_inventory(key: str, path: Path) -> dict[str, Any]:
    fields, rows = read_csv(path)
    require_fields(path, fields, INVENTORY_REQUIRED_FIELDS)
    if "net_edge" in fields:
        raise AssertionError(f"{path} sport inventory must not include net_edge")
    for index, row in enumerate(rows, start=2):
        if row.get("category_key") != key:
            raise AssertionError(f"{path}:{index} category mismatch: {row.get('category_key')} != {key}")
        missing = [field for field in ("source", "category_key", "category_label", "event_slug", "event_title") if not row.get(field)]
        if missing:
            raise AssertionError(f"{path}:{index} empty sport inventory fields: {', '.join(missing)}")
    if key == "formula_1" and not rows:
        raise AssertionError("Formula 1 inventory must have rows from Polymarket tag_id=435")
    return {"rows": len(rows), "status": "ok"}


def validate_alert_folder(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing"}
    latest_csv = path / "latest_opportunities.csv"
    latest_summary = path / "latest_opportunities.txt"
    if not latest_csv.exists():
        raise AssertionError(f"missing alert folder CSV: {latest_csv}")
    if not latest_summary.exists():
        raise AssertionError(f"missing alert folder summary: {latest_summary}")
    fields, rows = read_csv(latest_csv)
    require_fields(latest_csv, fields, BINARY_REQUIRED_FIELDS)
    summary = latest_summary.read_text(encoding="utf-8")
    if rows and "ALERT:" not in summary:
        raise AssertionError(f"{latest_summary} lacks ALERT summary for non-empty alert CSV")
    if not rows and "No current positive-edge" not in summary:
        raise AssertionError(f"{latest_summary} lacks no-alert summary for empty alert CSV")
    return {"rows": len(rows), "status": "ok"}


def main() -> None:
    summaries: dict[str, dict[str, Any]] = {}
    for universe, path in BINARY_FILES.items():
        summaries[universe] = validate_binary(universe, path)
    summaries["inventory"] = validate_inventory(INVENTORY_FILE)
    for key, path in SPORT_FILES.items():
        summaries[f"sport:{key}"] = validate_sport_inventory(key, path)
    summaries["alert_folder"] = validate_alert_folder(ALERT_DIR)
    for universe, summary in summaries.items():
        details = ", ".join(f"{key}={value}" for key, value in summary.items())
        print(f"{universe}: ok ({details})")


if __name__ == "__main__":
    main()
