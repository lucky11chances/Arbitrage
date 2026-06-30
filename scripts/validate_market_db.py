#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import market_db


RAW_JSON_COLUMNS = {
    "pm_events": "raw_json",
    "pm_markets": "raw_json",
    "ks_events": "raw_json",
    "ks_markets": "raw_json",
    "orderbook_payloads": "raw_json",
}

REQUIRED_TABLES = {
    "pm_events",
    "pm_event_sources",
    "pm_markets",
    "pm_tokens",
    "ks_events",
    "ks_markets",
    "orderbook_payloads",
    "orderbook_observations",
    "orderbook_levels",
    "normalized_contracts",
    "paired_contracts",
    "edge_snapshots",
    "data_quality_warnings",
}

ALLOWED_GENDERS = {"men", "women", "mixed", "open", "unknown"}
SAFE_PAIR_MARKET_TYPES = {"game_winner", "match_winner", "fighter_winner"}
SAFE_PAIR_UNIVERSES = {
    "mlb",
    "nba",
    "wnba",
    "nfl",
    "cfb",
    "atp",
    "atp_challenger",
    "wta",
    "itf_men",
    "itf_women",
    "valorant",
    "cs2",
    "lol",
}
DEFAULT_MAX_EDGE_BOOK_AGE_SECONDS = 10.0
DEFAULT_MAX_EDGE_SKEW_SECONDS = 5.0


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_tables(conn) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    actual = {str(row["name"]) for row in rows}
    missing = sorted(REQUIRED_TABLES - actual)
    require(not missing, f"missing tables: {', '.join(missing)}")


def validate_integrity(conn) -> None:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    require(integrity == "ok", f"sqlite integrity_check failed: {integrity}")
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    require(not fk_rows, f"foreign_key_check failed: {len(fk_rows)} row(s)")


def validate_raw_json(conn) -> int:
    checked = 0
    for table, column in RAW_JSON_COLUMNS.items():
        for row in conn.execute(f"SELECT rowid AS row_id, {column} AS raw_json FROM {table}"):
            try:
                json.loads(str(row["raw_json"]))
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{table}:{row['row_id']} invalid JSON: {exc}") from exc
            checked += 1
    return checked


def validate_observations(conn) -> dict[str, int]:
    ok_rows = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orderbook_observations obs
        LEFT JOIN orderbook_payloads payload ON payload.payload_hash = obs.payload_hash
        WHERE obs.status = 'ok'
          AND (obs.payload_hash IS NULL OR payload.payload_hash IS NULL)
        """
    ).fetchone()["count"]
    require(ok_rows == 0, f"ok observations without payload: {ok_rows}")

    error_rows = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orderbook_observations
        WHERE status = 'error'
          AND COALESCE(error_message, '') = ''
        """
    ).fetchone()["count"]
    require(error_rows == 0, f"error observations without error_message: {error_rows}")

    incomplete_ok = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orderbook_observations
        WHERE status = 'ok'
          AND (
            best_bid_scaled IS NULL OR best_bid_size_scaled IS NULL
            OR best_ask_scaled IS NULL OR best_ask_size_scaled IS NULL
          )
        """
    ).fetchone()["count"]

    negative_levels = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orderbook_levels
        WHERE price_scaled < 0 OR size_scaled < 0
        """
    ).fetchone()["count"]
    require(negative_levels == 0, f"negative orderbook levels: {negative_levels}")

    orphan_levels = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM orderbook_levels level
        JOIN orderbook_observations obs ON obs.observation_id = level.observation_id
        LEFT JOIN orderbook_payloads payload ON payload.payload_hash = obs.payload_hash
        WHERE obs.status != 'ok' OR payload.payload_hash IS NULL
        """
    ).fetchone()["count"]
    require(orphan_levels == 0, f"levels not linked to ok payload observations: {orphan_levels}")

    return {"incomplete_ok_observations": int(incomplete_ok)}


def validate_pairs_and_edges(
    conn,
    *,
    max_age_seconds: float = DEFAULT_MAX_EDGE_BOOK_AGE_SECONDS,
    max_skew_seconds: float = DEFAULT_MAX_EDGE_SKEW_SECONDS,
) -> dict[str, int]:
    unsafe_pairs = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM paired_contracts
        WHERE safe_paired != 1
           OR universe NOT IN ({",".join("?" for _ in SAFE_PAIR_UNIVERSES)})
           OR market_type NOT IN ({",".join("?" for _ in SAFE_PAIR_MARKET_TYPES)})
        """,
        (*sorted(SAFE_PAIR_UNIVERSES), *sorted(SAFE_PAIR_MARKET_TYPES)),
    ).fetchone()["count"]
    require(unsafe_pairs == 0, f"unsafe paired_contract rows: {unsafe_pairs}")

    normalized_semantic_mismatch = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM paired_contracts pair
        JOIN normalized_contracts pm
          ON pm.venue = 'pm'
         AND pm.pm_token_id = pair.pm_token_id
        JOIN normalized_contracts ks
          ON ks.venue = 'ks'
         AND ks.ks_market_ticker = pair.ks_market_ticker
        WHERE pm.universe != ks.universe
           OR pm.competition_gender != ks.competition_gender
           OR pm.competition_gender = 'unknown'
           OR ks.competition_gender = 'unknown'
           OR pm.market_type != ks.market_type
        """
    ).fetchone()["count"]
    require(normalized_semantic_mismatch == 0, f"paired rows with normalized semantic mismatch: {normalized_semantic_mismatch}")

    unsafe_edges = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots edge
        JOIN paired_contracts pair ON pair.paired_contract_id = edge.paired_contract_id
        WHERE pair.safe_paired != 1
        """
    ).fetchone()["count"]
    require(unsafe_edges == 0, f"edge rows linked to unsafe pairs: {unsafe_edges}")

    bad_edge_obs = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots edge
        JOIN orderbook_observations pm ON pm.observation_id = edge.pm_observation_id
        JOIN orderbook_observations ks ON ks.observation_id = edge.ks_observation_id
        WHERE pm.status != 'ok' OR ks.status != 'ok'
        """
    ).fetchone()["count"]
    require(bad_edge_obs == 0, f"edge rows linked to non-ok observations: {bad_edge_obs}")

    bad_edge_venue = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots edge
        JOIN orderbook_observations pm ON pm.observation_id = edge.pm_observation_id
        JOIN orderbook_observations ks ON ks.observation_id = edge.ks_observation_id
        WHERE pm.venue != 'pm'
           OR ks.venue != 'ks'
        """
    ).fetchone()["count"]
    require(bad_edge_venue == 0, f"edge rows linked to wrong observation venues: {bad_edge_venue}")

    huge_edges = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots
        WHERE net_edge_scaled > ?
        """,
        (int(0.30 * market_db.SCALE),),
    ).fetchone()["count"]
    require(huge_edges == 0, f"net_edge over 30%: {huge_edges}")

    positive_without_alert = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots
        WHERE net_edge_scaled > 0
          AND alert != 'ALERT'
        """
    ).fetchone()["count"]
    require(positive_without_alert == 0, f"positive edge rows without ALERT: {positive_without_alert}")

    nonpositive_with_alert = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots
        WHERE net_edge_scaled <= 0
          AND COALESCE(alert, '') != ''
        """
    ).fetchone()["count"]
    require(nonpositive_with_alert == 0, f"non-positive edge rows with alert: {nonpositive_with_alert}")

    non_executable_alert = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots
        WHERE alert = 'ALERT'
          AND (
            best_leg_bbo_size_scaled IS NULL
            OR best_leg_bbo_size_scaled <= 0
            OR net_profit_at_bbo_scaled IS NULL
            OR net_profit_at_bbo_scaled <= 0
          )
        """
    ).fetchone()["count"]
    require(non_executable_alert == 0, f"alert rows without executable positive BBO depth/profit: {non_executable_alert}")

    stale_or_skewed_edges = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM edge_snapshots
        WHERE book_age_seconds > ?
           OR snapshot_skew_seconds > ?
        """,
        (max_age_seconds, max_skew_seconds),
    ).fetchone()["count"]
    require(stale_or_skewed_edges == 0, f"stale/skewed edge rows: {stale_or_skewed_edges}")

    formula_errors = validate_edge_formulas(conn)

    return {
        "unsafe_pairs": int(unsafe_pairs),
        "normalized_semantic_mismatch": int(normalized_semantic_mismatch),
        "unsafe_edges": int(unsafe_edges),
        "bad_edge_obs": int(bad_edge_obs),
        "bad_edge_venue": int(bad_edge_venue),
        "huge_edges": int(huge_edges),
        "edge_formula_errors": int(formula_errors),
    }


def validate_edge_formulas(conn) -> int:
    checked = 0
    for row in conn.execute(
        """
        SELECT
            edge_snapshot_id,
            pm_bid_scaled,
            pm_ask_scaled,
            pm_bid_size_scaled,
            pm_ask_size_scaled,
            ks_bid_scaled,
            ks_ask_scaled,
            ks_bid_size_scaled,
            ks_ask_size_scaled,
            best_leg,
            gross_cost_scaled,
            net_edge_scaled,
            best_leg_bbo_size_scaled,
            net_profit_at_bbo_scaled
        FROM edge_snapshots
        """
    ):
        pm_bid = market_db.scaled_to_decimal(row["pm_bid_scaled"])
        pm_ask = market_db.scaled_to_decimal(row["pm_ask_scaled"])
        ks_bid = market_db.scaled_to_decimal(row["ks_bid_scaled"])
        ks_ask = market_db.scaled_to_decimal(row["ks_ask_scaled"])
        require(
            pm_bid is not None and pm_ask is not None and ks_bid is not None and ks_ask is not None,
            f"edge {row['edge_snapshot_id']} missing price inputs",
        )
        expected_leg, expected_net, expected_cost = market_db.best_net_decimal(pm_bid, pm_ask, ks_bid, ks_ask)
        require(row["best_leg"] == expected_leg, f"edge {row['edge_snapshot_id']} best_leg mismatch")
        require(row["gross_cost_scaled"] == market_db.to_scaled(expected_cost), f"edge {row['edge_snapshot_id']} gross_cost mismatch")
        require(row["net_edge_scaled"] == market_db.to_scaled(expected_net), f"edge {row['edge_snapshot_id']} net_edge mismatch")

        pm_bid_size = market_db.scaled_to_decimal(row["pm_bid_size_scaled"])
        pm_ask_size = market_db.scaled_to_decimal(row["pm_ask_size_scaled"])
        ks_bid_size = market_db.scaled_to_decimal(row["ks_bid_size_scaled"])
        ks_ask_size = market_db.scaled_to_decimal(row["ks_ask_size_scaled"])
        if expected_leg == "PM_YES_KS_NO":
            executable_size = min(pm_ask_size, ks_bid_size)
        else:
            executable_size = min(pm_bid_size, ks_ask_size)
        require(executable_size is not None and executable_size > 0, f"edge {row['edge_snapshot_id']} has non-executable BBO size")
        require(
            row["best_leg_bbo_size_scaled"] == market_db.to_scaled(executable_size),
            f"edge {row['edge_snapshot_id']} executable size mismatch",
        )
        require(
            row["net_profit_at_bbo_scaled"] == market_db.to_scaled(expected_net * executable_size),
            f"edge {row['edge_snapshot_id']} net_profit_at_bbo mismatch",
        )
        checked += 1
    return 0 if checked >= 0 else 0


def validate_token_and_market_links(conn) -> dict[str, int]:
    pm_tokens_without_market = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM pm_tokens token
        LEFT JOIN pm_markets market ON market.market_id = token.market_id
        WHERE market.market_id IS NULL
        """
    ).fetchone()["count"]
    require(pm_tokens_without_market == 0, f"PM tokens without market: {pm_tokens_without_market}")

    ks_markets_without_event = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM ks_markets market
        LEFT JOIN ks_events event ON event.event_ticker = market.event_ticker
        WHERE event.event_ticker IS NULL
        """
    ).fetchone()["count"]
    require(ks_markets_without_event == 0, f"KS markets without event: {ks_markets_without_event}")

    return {
        "pm_tokens_without_market": int(pm_tokens_without_market),
        "ks_markets_without_event": int(ks_markets_without_event),
    }


def validate_normalized_contracts(conn) -> dict[str, int]:
    invalid_gender = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM normalized_contracts
        WHERE competition_gender NOT IN ('men', 'women', 'mixed', 'open', 'unknown')
        """
    ).fetchone()["count"]
    require(invalid_gender == 0, f"normalized rows with invalid gender: {invalid_gender}")

    missing_required = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM normalized_contracts
        WHERE COALESCE(category_key, '') = ''
           OR COALESCE(universe, '') = ''
           OR COALESCE(market_type, '') = ''
           OR COALESCE(competition_gender, '') = ''
           OR COALESCE(instrument_id, '') = ''
        """
    ).fetchone()["count"]
    require(missing_required == 0, f"normalized rows missing required semantics: {missing_required}")

    unknown_safe_candidates = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM normalized_contracts
        WHERE competition_gender = 'unknown'
          AND safe_pair_candidate != 0
        """
    ).fetchone()["count"]
    require(unknown_safe_candidates == 0, f"unknown-gender safe candidates: {unknown_safe_candidates}")

    missing_pm_link = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM normalized_contracts norm
        LEFT JOIN pm_tokens token ON token.token_id = norm.pm_token_id
        WHERE norm.venue = 'pm'
          AND token.token_id IS NULL
        """
    ).fetchone()["count"]
    require(missing_pm_link == 0, f"normalized PM rows without token link: {missing_pm_link}")

    missing_ks_link = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM normalized_contracts norm
        LEFT JOIN ks_markets market ON market.market_ticker = norm.ks_market_ticker
        WHERE norm.venue = 'ks'
          AND market.market_ticker IS NULL
        """
    ).fetchone()["count"]
    require(missing_ks_link == 0, f"normalized KS rows without market link: {missing_ks_link}")

    return {
        "invalid_gender_rows": int(invalid_gender),
        "normalized_missing_required": int(missing_required),
        "unknown_safe_candidates": int(unknown_safe_candidates),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate every row in the local PM/KS orderbook SQLite DB.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--max-age-seconds", type=float, default=DEFAULT_MAX_EDGE_BOOK_AGE_SECONDS)
    parser.add_argument("--max-skew-seconds", type=float, default=DEFAULT_MAX_EDGE_SKEW_SECONDS)
    args = parser.parse_args()

    with market_db.connect(Path(args.db)) as conn:
        market_db.init_db(conn)
        validate_tables(conn)
        validate_integrity(conn)
        json_rows = validate_raw_json(conn)
        observation_summary = validate_observations(conn)
        edge_summary = validate_pairs_and_edges(
            conn,
            max_age_seconds=args.max_age_seconds,
            max_skew_seconds=args.max_skew_seconds,
        )
        link_summary = validate_token_and_market_links(conn)
        normalized_summary = validate_normalized_contracts(conn)
        counts = market_db.row_counts(conn)

    print(f"market DB ok: db={args.db}; raw_json_rows_checked={json_rows}")
    print(", ".join(f"{key}={value}" for key, value in counts.items()))
    details: dict[str, Any] = {}
    details.update(observation_summary)
    details.update(edge_summary)
    details.update(link_summary)
    details.update(normalized_summary)
    print(", ".join(f"{key}={value}" for key, value in details.items()))


if __name__ == "__main__":
    main()
