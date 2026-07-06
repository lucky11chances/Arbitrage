#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import old_market_db
import old_staging_generate_human_review
import old_staging_pair_from_db as pairer


REQUIRED_MATCHES = (
    ("Auger-Aliassime / Shevchenko", ("auger", "shevchenko")),
    ("Trungelliti / Damm", ("trungelliti", "damm")),
    ("Sonego / Etcheverry", ("sonego", "etcheverry")),
    ("Michelsen / Fearnley", ("michelsen", "fearnley")),
    ("Griekspoor / Duckworth", ("griekspoor", "duckworth")),
)


def event_key_from_pair(pair) -> tuple[str, str, str, str]:
    return (pair.universe, pair.event_date, pair.pm_event_slug, pair.ks_event_ticker)


def strict_exact_tennis_pairs(pm_rows, ks_rows) -> list[tuple[object, object]]:
    pm_by_key = {(row.event_date, row.universe, row.entity_key, row.outcome_key): row for row in pm_rows}
    pm_counts = defaultdict(int)
    ks_counts = defaultdict(int)
    for row in pm_rows:
        pm_counts[(row.event_date, row.universe, row.entity_key, row.outcome_key)] += 1
    for row in ks_rows:
        ks_counts[(row.event_date, row.universe, row.entity_key, row.outcome_key)] += 1

    pairs = []
    for ks_row in ks_rows:
        key = (ks_row.event_date, ks_row.universe, ks_row.entity_key, ks_row.outcome_key)
        if pm_counts[key] == 1 and ks_counts[key] == 1:
            pairs.append((pm_by_key[key], ks_row))
    return pairs


def pair_contains(pair, needles: tuple[str, str]) -> bool:
    haystack = " ".join(
        (
            pair.match_name,
            pair.pm_yes_outcome,
            pair.ks_yes_outcome,
            pair.pm_event_slug,
            pair.ks_event_ticker,
        )
    ).lower()
    return all(needle in haystack for needle in needles)


def validate_pairs(pairs) -> list[str]:
    errors = []
    seen = set()
    for pair in pairs:
        pair_id = (pair.pm_token_id, pair.ks_market_ticker)
        if pair_id in seen:
            errors.append(f"duplicate pair: {pair_id}")
        seen.add(pair_id)
        if pair.universe not in pairer.TENNIS_UNIVERSES:
            continue
        if pair.market_type != "match_winner":
            errors.append(f"non-match-winner tennis pair: {pair.pm_event_slug} {pair.ks_market_ticker}")
        if not pair.pm_token_id or not pair.ks_market_ticker:
            errors.append(f"missing instrument id: {pair.pm_event_slug} {pair.ks_event_ticker}")
    return errors


def unmatched_counts(pm_rows, ks_rows, pairs) -> tuple[int, int, int]:
    matched_pm_tokens = {pair.pm_token_id for pair in pairs}
    matched_ks_tickers = {pair.ks_market_ticker for pair in pairs}
    pm_events = old_staging_generate_human_review.group_pm(pm_rows)
    ks_events = old_staging_generate_human_review.group_ks(ks_rows)
    unmatched_pm = [event for event in pm_events if not any(row.pm_token_id in matched_pm_tokens for row in event)]
    unmatched_ks = [event for event in ks_events if not any(row.ks_market_ticker in matched_ks_tickers for row in event)]

    alias_rows = 0
    for pm_event in unmatched_pm:
        for ks_event in unmatched_ks:
            if not old_staging_generate_human_review.same_core_scope(pm_event, ks_event):
                continue
            if not pairer.dates_compatible(old_staging_generate_human_review.event_ref(pm_event), old_staging_generate_human_review.event_ref(ks_event)):
                continue
            if old_staging_generate_human_review.name_similarity(pm_event, ks_event) >= 0.70:
                alias_rows += 1
    return alias_rows, len(unmatched_pm), len(unmatched_ks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the conservative PM/KS tennis pairing repair against every local DB row.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--from-date", default=None)
    args = parser.parse_args()

    conn = old_market_db.connect(Path(args.db))
    try:
        pm_rows, pm_skipped = pairer.build_pm_outcomes(conn, "tennis", args.from_date)
        ks_rows, ks_skipped = pairer.build_ks_outcomes(conn, "tennis", args.from_date)
        repaired_pairs, diagnostics, warnings = pairer.safe_pair_rows(pm_rows, ks_rows)
    finally:
        conn.close()

    strict_pairs = strict_exact_tennis_pairs(pm_rows, ks_rows)
    strict_events = {(pm.event_date, pm.universe, pm.entity_key) for pm, _ks in strict_pairs}
    repaired_events = {event_key_from_pair(pair) for pair in repaired_pairs if pair.universe in pairer.TENNIS_UNIVERSES}
    alias_rows, pm_only, ks_only = unmatched_counts(pm_rows, ks_rows, repaired_pairs)

    print("tennis pairing repair validation")
    print(f"pm_safe_candidates={len(pm_rows)} ks_safe_candidates={len(ks_rows)}")
    print(f"strict_exact_contract_pairs={len(strict_pairs)} strict_exact_events={len(strict_events)}")
    print(f"repaired_contract_pairs={len(repaired_pairs)} repaired_events={len(repaired_events)}")
    print(f"after_alias_rows={alias_rows} after_pm_only_events={pm_only} after_ks_only_events={ks_only}")
    print(f"pm_skipped={dict(pm_skipped)}")
    print(f"ks_skipped={dict(ks_skipped)}")
    print(f"warnings={len(warnings)} diagnostics={len(diagnostics)}")

    errors = validate_pairs(repaired_pairs)
    for label, needles in REQUIRED_MATCHES:
        hits = [pair for pair in repaired_pairs if pair_contains(pair, needles)]
        print(f"required_match {label}: contract_pairs={len(hits)}")
        if len(hits) < 2:
            errors.append(f"required match not safely paired: {label}")

    if errors:
        print("validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
