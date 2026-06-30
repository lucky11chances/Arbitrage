#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import market_db
import staging_collect_market_db
import staging_generate_human_review
import staging_pair_from_db as pairer


REQUIRED_MATCHES = (
    ("Auger-Aliassime / Shevchenko", ("auger", "shevchenko")),
    ("Trungelliti / Damm", ("trungelliti", "damm")),
    ("Sonego / Etcheverry", ("sonego", "etcheverry")),
    ("Michelsen / Fearnley", ("michelsen", "fearnley")),
    ("Griekspoor / Duckworth", ("griekspoor", "duckworth")),
    ("Tabilo / Majchrzak", ("tabilo", "majchrzak")),
    ("Kovacevic / Van de Zandschulp", ("kovacevic", "zandschulp")),
    ("CS2 Berg / Aimhau", ("berg", "aimh")),
)

REQUIRED_PM_SLUGS = (
    "atp-majchrz-tabilo-2026-06-29",
    "atp-kovacev-zandsch-2026-06-29",
    "cs2-berg-aimhau-2026-06-29",
)


def pair_contains(pair, needles: tuple[str, ...]) -> bool:
    haystack = " ".join(
        (
            pair.universe,
            pair.match_name,
            pair.pm_yes_outcome,
            pair.ks_yes_outcome,
            pair.pm_event_slug,
            pair.ks_event_ticker,
            pair.ks_market_ticker,
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
        if pair.market_type not in pairer.SAFE_MARKET_TYPES:
            errors.append(f"unsafe market_type paired: {pair.market_type} {pair.pm_event_slug} {pair.ks_market_ticker}")
        if pair.universe not in pairer.SAFE_UNIVERSES:
            errors.append(f"unsafe universe paired: {pair.universe} {pair.pm_event_slug} {pair.ks_market_ticker}")
        if not pair.pm_token_id or not pair.ks_market_ticker:
            errors.append(f"missing instrument id: {pair.pm_event_slug} {pair.ks_event_ticker}")
    return errors


def unmatched_counts(pm_rows, ks_rows, pairs) -> tuple[int, int, int]:
    matched_pm_tokens = {pair.pm_token_id for pair in pairs}
    matched_ks_tickers = {pair.ks_market_ticker for pair in pairs}
    pm_events = staging_generate_human_review.group_pm(pm_rows)
    ks_events = staging_generate_human_review.group_ks(ks_rows)
    unmatched_pm = [event for event in pm_events if not any(row.pm_token_id in matched_pm_tokens for row in event)]
    unmatched_ks = [event for event in ks_events if not any(row.ks_market_ticker in matched_ks_tickers for row in event)]

    alias_rows = 0
    for pm_event in unmatched_pm:
        for ks_event in unmatched_ks:
            if not staging_generate_human_review.same_core_scope(pm_event, ks_event):
                continue
            if not pairer.dates_compatible(staging_generate_human_review.event_ref(pm_event), staging_generate_human_review.event_ref(ks_event)):
                continue
            if staging_generate_human_review.name_similarity(pm_event, ks_event) >= 0.70:
                alias_rows += 1
    return alias_rows, len(unmatched_pm), len(unmatched_ks)


def fetch_missing_pm_fallback(conn, args) -> int:
    return staging_collect_market_db.collect_pm_repair_metadata(
        conn,
        SimpleNamespace(
            sports=args.sports,
            pm_extra_slug=[],
            pm_fallback_max_slugs=args.pm_fallback_max_slugs,
            pm_fallback_search=args.pm_fallback_search,
            pm_fallback_max_searches=args.pm_fallback_max_searches,
            pm_page_limit=args.pm_page_limit,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate PM/KS pairing repair across tennis and CS2 staging candidates.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="tennis,cs2,esports")
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--fetch-missing-pm-fallback", action="store_true")
    parser.add_argument("--pm-page-limit", type=int, default=50)
    parser.add_argument("--pm-fallback-max-slugs", type=int, default=500)
    parser.add_argument("--pm-fallback-search", action="store_true")
    parser.add_argument("--pm-fallback-max-searches", type=int, default=50)
    args = parser.parse_args()

    conn = market_db.connect(Path(args.db))
    try:
        if args.fetch_missing_pm_fallback:
            fetched = fetch_missing_pm_fallback(conn, args)
            print(f"pm_fallback_events_fetched={fetched}")

        missing_slugs = [
            slug
            for slug in REQUIRED_PM_SLUGS
            if conn.execute("SELECT 1 FROM pm_events WHERE event_slug = ?", (slug,)).fetchone() is None
        ]
        pm_rows, pm_skipped = pairer.build_pm_outcomes(conn, args.sports, args.from_date)
        ks_rows, ks_skipped = pairer.build_ks_outcomes(conn, args.sports, args.from_date)
        pairs, diagnostics, warnings = pairer.safe_pair_rows(pm_rows, ks_rows)
    finally:
        conn.close()

    alias_rows, pm_only, ks_only = unmatched_counts(pm_rows, ks_rows, pairs)
    by_universe = Counter(pair.universe for pair in pairs)
    diagnostics_by_reason = Counter(str(row.get("reason") or "") for row in diagnostics if row.get("status") != "paired")

    print("pairing repair validation")
    print(f"pm_safe_candidates={len(pm_rows)} ks_safe_candidates={len(ks_rows)} safe_contract_pairs={len(pairs)}")
    print(f"safe_pairs_by_universe={dict(sorted(by_universe.items()))}")
    print(f"after_alias_rows={alias_rows} after_pm_only_events={pm_only} after_ks_only_events={ks_only}")
    print(f"missing_required_pm_slugs={missing_slugs}")
    print(f"pm_skipped={dict(pm_skipped)}")
    print(f"ks_skipped={dict(ks_skipped)}")
    print(f"warnings={len(warnings)} diagnostics={len(diagnostics)}")
    print(f"top_unmatched_reasons={dict(diagnostics_by_reason.most_common(10))}")

    errors = validate_pairs(pairs)
    if missing_slugs:
        errors.append(f"required PM slugs missing from local DB: {', '.join(missing_slugs)}")
    for label, needles in REQUIRED_MATCHES:
        hits = [pair for pair in pairs if pair_contains(pair, needles)]
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
