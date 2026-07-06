#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import old_market_db
import old_sports_taxonomy
import old_staging_pair_from_db


STAGE = "prune_non_binary_markets"
PM_KEEP_REASONS = {"pm_moneyline_binary", "pm_main_binary_winner"}
KS_KEEP_REASONS = {"ks_binary_winner_event"}


@dataclass(frozen=True)
class PrunePlan:
    pm_keep_markets: set[str]
    pm_remove_markets: dict[str, str]
    pm_remove_tokens: set[str]
    ks_keep_events: set[str]
    ks_remove_events: dict[str, str]
    ks_remove_markets: set[str]
    pm_reason_counts: Counter[str]
    ks_reason_counts: Counter[str]


def parse_json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = old_market_db.json.loads(str(value or "{}"))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def outcome_keys(outcomes: list[str]) -> set[str]:
    return {old_sports_taxonomy.normalize_key(outcome) for outcome in outcomes}


def pm_moneyline_binary(title: str, question: str, outcomes: list[str], token_ids: list[str], market_raw: dict[str, Any]) -> tuple[bool, str]:
    if len(outcomes) != 2 or len(token_ids) != 2:
        return False, "PM market is not exactly binary"
    keys = outcome_keys(outcomes)
    if any(old_staging_pair_from_db.is_draw_or_tie(outcome) for outcome in outcomes):
        return False, "PM binary contains draw/tie outcome"
    if keys in ({"over", "under"}, {"odd", "even"}):
        return False, "PM binary is total/parity prop"
    text = f"{title} {question}"
    if "/" in text or "doubles" in text.lower():
        return False, "PM doubles/team-tennis market excluded"
    if old_staging_pair_from_db.DISALLOWED_PM_TERMS_RE.search(question) or old_staging_pair_from_db.DISALLOWED_PM_TERMS_RE.search(title):
        return False, "PM market text indicates prop/spread/total/future"

    sports_market_type = str(market_raw.get("sportsMarketType") or "").lower()
    group_title = str(market_raw.get("groupItemTitle") or "").lower()
    if sports_market_type != "moneyline":
        return False, "PM market is not sports moneyline"
    if any(term in group_title for term in ("map ", "set ", "spread", "total")):
        return False, "PM market group indicates map/set/spread/total"

    if keys == {"yes", "no"} and not old_staging_pair_from_db.MATCHUP_RE.search(text):
        return False, "PM yes/no moneyline lacks matchup context"
    return True, "pm_moneyline_binary"


def classify_pm_market(row: sqlite3.Row) -> tuple[bool, str]:
    title = str(row["event_title"] or "")
    question = str(row["question"] or "")
    outcomes = [str(outcome) for outcome in old_market_db.parse_json_list(row["outcomes_json"])]
    token_ids = [str(token_id) for token_id in old_market_db.parse_json_list(row["clob_token_ids_json"])]
    market_raw = parse_json_object(row["market_raw_json"])

    ok, reason = pm_moneyline_binary(title, question, outcomes, token_ids, market_raw)
    if ok:
        return True, reason

    ok, reason = old_staging_pair_from_db.pm_is_main_binary_market(title, question, outcomes, token_ids)
    if ok:
        return True, "pm_main_binary_winner"
    return False, reason


def classify_ks_event(event_rows: list[sqlite3.Row]) -> tuple[bool, str]:
    if len(event_rows) != 2:
        return False, "KS event is not exactly binary"
    classifications = [
        old_sports_taxonomy.classify_ks_market(
            str(row["series_ticker"] or ""),
            str(row["market_ticker"] or ""),
            str(row["title"] or ""),
        )
        for row in event_rows
    ]
    first = classifications[0]
    if any(
        (
            item.category_key,
            item.universe,
            item.market_type,
            item.competition_gender,
        )
        != (first.category_key, first.universe, first.market_type, first.competition_gender)
        for item in classifications
    ):
        return False, "KS event taxonomy mismatch"
    if first.market_type not in old_staging_pair_from_db.SAFE_MARKET_TYPES:
        return False, f"KS market_type excluded: {first.market_type}"
    outcomes = [str(row["yes_sub_title"] or "") for row in event_rows]
    if any(not outcome for outcome in outcomes):
        return False, "KS event has missing outcome"
    keys = outcome_keys(outcomes)
    if any(old_staging_pair_from_db.is_draw_or_tie(outcome) for outcome in outcomes) or keys in ({"over", "under"}, {"yes", "no"}):
        return False, "KS event has prop/draw/tie outcome"
    return True, "ks_binary_winner_event"


def build_plan(conn: sqlite3.Connection) -> PrunePlan:
    pm_keep_markets: set[str] = set()
    pm_remove_markets: dict[str, str] = {}
    pm_reason_counts: Counter[str] = Counter()
    for row in conn.execute(
        """
        SELECT
            market.market_id,
            market.question,
            market.outcomes_json,
            market.clob_token_ids_json,
            market.raw_json AS market_raw_json,
            event.title AS event_title
        FROM pm_markets market
        JOIN pm_events event ON event.event_slug = market.event_slug
        ORDER BY market.market_id
        """
    ):
        keep, reason = classify_pm_market(row)
        pm_reason_counts[reason] += 1
        market_id = str(row["market_id"] or "")
        if keep:
            pm_keep_markets.add(market_id)
        else:
            pm_remove_markets[market_id] = reason

    pm_remove_tokens = {
        str(row["token_id"])
        for row in conn.execute(
            f"""
            SELECT token.token_id
            FROM pm_tokens token
            WHERE token.market_id IN ({",".join("?" for _ in pm_remove_markets)})
            """,
            tuple(pm_remove_markets),
        )
    } if pm_remove_markets else set()

    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT market_ticker, event_ticker, series_ticker, title, yes_sub_title
        FROM ks_markets
        ORDER BY event_ticker, market_ticker
        """
    ):
        grouped[str(row["event_ticker"] or "")].append(row)

    ks_keep_events: set[str] = set()
    ks_remove_events: dict[str, str] = {}
    ks_remove_markets: set[str] = set()
    ks_reason_counts: Counter[str] = Counter()
    for event_ticker, event_rows in grouped.items():
        keep, reason = classify_ks_event(event_rows)
        ks_reason_counts[reason] += 1
        if keep:
            ks_keep_events.add(event_ticker)
        else:
            ks_remove_events[event_ticker] = reason
            ks_remove_markets.update(str(row["market_ticker"] or "") for row in event_rows)

    return PrunePlan(
        pm_keep_markets=pm_keep_markets,
        pm_remove_markets=pm_remove_markets,
        pm_remove_tokens=pm_remove_tokens,
        ks_keep_events=ks_keep_events,
        ks_remove_events=ks_remove_events,
        ks_remove_markets=ks_remove_markets,
        pm_reason_counts=pm_reason_counts,
        ks_reason_counts=ks_reason_counts,
    )


def count_matching_observations(conn: sqlite3.Connection, venue: str, instruments: set[str]) -> int:
    if not instruments:
        return 0
    return sum(
        1
        for row in conn.execute("SELECT instrument_id FROM orderbook_observations WHERE venue = ?", (venue,))
        if str(row["instrument_id"] or "") in instruments
    )


def create_backup(db_path: Path, backup_dir: Path) -> Path:
    db_path = old_market_db.ensure_legacy_db_path(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = old_market_db.utc_now().replace(":", "").replace("+", "_").replace(".", "_")
    backup_path = backup_dir / f"{db_path.stem}.pre_non_binary_prune.{timestamp}.sqlite"
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as dest:
        source.backup(dest)
    return backup_path


def delete_in_chunks(conn: sqlite3.Connection, sql_template: str, values: set[str], chunk_size: int = 500) -> int:
    values_list = sorted(values)
    deleted = 0
    for index in range(0, len(values_list), chunk_size):
        chunk = values_list[index : index + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor = conn.execute(sql_template.format(placeholders=placeholders), tuple(chunk))
        deleted += cursor.rowcount if cursor.rowcount is not None else 0
    return deleted


def execute_prune(conn: sqlite3.Connection, plan: PrunePlan) -> dict[str, int]:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM edge_snapshots")
    conn.execute("DELETE FROM paired_contracts")
    conn.execute("DELETE FROM normalized_contracts")
    conn.execute("DELETE FROM data_quality_warnings WHERE stage IN ('normalize_old_market_db', 'pair_from_db', 'compute_edges', ?)", (STAGE,))

    pm_obs_deleted = delete_in_chunks(
        conn,
        "DELETE FROM orderbook_observations WHERE venue = 'pm' AND instrument_id IN ({placeholders})",
        plan.pm_remove_tokens,
    )
    ks_obs_deleted = delete_in_chunks(
        conn,
        "DELETE FROM orderbook_observations WHERE venue = 'ks' AND instrument_id IN ({placeholders})",
        plan.ks_remove_markets,
    )
    pm_markets_deleted = delete_in_chunks(
        conn,
        "DELETE FROM pm_markets WHERE market_id IN ({placeholders})",
        set(plan.pm_remove_markets),
    )
    ks_markets_deleted = delete_in_chunks(
        conn,
        "DELETE FROM ks_markets WHERE market_ticker IN ({placeholders})",
        plan.ks_remove_markets,
    )
    pm_events_deleted = conn.execute(
        """
        DELETE FROM pm_events
        WHERE NOT EXISTS (
            SELECT 1 FROM pm_markets market WHERE market.event_slug = pm_events.event_slug
        )
        """
    ).rowcount
    ks_events_deleted = conn.execute(
        """
        DELETE FROM ks_events
        WHERE NOT EXISTS (
            SELECT 1 FROM ks_markets market WHERE market.event_ticker = ks_events.event_ticker
        )
        """
    ).rowcount
    payloads_deleted = conn.execute(
        """
        DELETE FROM orderbook_payloads
        WHERE NOT EXISTS (
            SELECT 1 FROM orderbook_observations obs
            WHERE obs.payload_hash = orderbook_payloads.payload_hash
        )
        """
    ).rowcount
    old_market_db.record_warning(
        conn,
        STAGE,
        "Pruned non-binary/props/futures markets from local DB",
        context={
            "pm_markets_removed": len(plan.pm_remove_markets),
            "ks_markets_removed": len(plan.ks_remove_markets),
            "pm_keep_reasons": dict(plan.pm_reason_counts),
            "ks_keep_reasons": dict(plan.ks_reason_counts),
        },
    )
    conn.commit()
    return {
        "pm_observations_deleted": int(pm_obs_deleted),
        "ks_observations_deleted": int(ks_obs_deleted),
        "pm_markets_deleted": int(pm_markets_deleted),
        "ks_markets_deleted": int(ks_markets_deleted),
        "pm_events_deleted": int(pm_events_deleted or 0),
        "ks_events_deleted": int(ks_events_deleted or 0),
        "payloads_deleted": int(payloads_deleted or 0),
    }


def print_summary(conn: sqlite3.Connection, plan: PrunePlan) -> None:
    pm_obs = count_matching_observations(conn, "pm", plan.pm_remove_tokens)
    ks_obs = count_matching_observations(conn, "ks", plan.ks_remove_markets)
    print("prune_non_binary_markets plan:")
    print(f"  PM keep_markets={len(plan.pm_keep_markets)} remove_markets={len(plan.pm_remove_markets)} remove_tokens={len(plan.pm_remove_tokens)} remove_observations={pm_obs}")
    print(f"  KS keep_events={len(plan.ks_keep_events)} remove_events={len(plan.ks_remove_events)} remove_markets={len(plan.ks_remove_markets)} remove_observations={ks_obs}")
    print("  PM reasons:")
    for reason, count in plan.pm_reason_counts.most_common():
        prefix = "keep" if reason in PM_KEEP_REASONS else "remove"
        print(f"    {prefix}: {reason}: {count}")
    print("  KS reasons:")
    for reason, count in plan.ks_reason_counts.most_common():
        prefix = "keep" if reason in KS_KEEP_REASONS else "remove"
        print(f"    {prefix}: {reason}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune PM/KS non-binary sports markets from the local SQLite DB.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--execute", action="store_true", help="Apply deletion. Without this flag, only prints a dry-run plan.")
    parser.add_argument("--backup-dir", default="data/old/old_backups")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    args = parser.parse_args()

    db_path = old_market_db.ensure_legacy_db_path(args.db)
    with old_market_db.connect(db_path) as conn:
        old_market_db.init_db(conn)
        plan = build_plan(conn)
        print_summary(conn, plan)
        if not args.execute:
            print("dry_run=true; no rows deleted")
            return
        backup_path = None
        if not args.no_backup:
            backup_path = create_backup(db_path, Path(args.backup_dir))
            print(f"backup={backup_path}")
        summary = execute_prune(conn, plan)
        if args.vacuum:
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        counts = old_market_db.row_counts(conn)
    print("deleted: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
    print("remaining: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
