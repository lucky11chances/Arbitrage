#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import old_index_event_matching as matching
import old_market_db


def refine_pm_competition(sport_key: str, competition_key: str, slug: str, title: str, question: str) -> tuple[str, str]:
    text = f"{slug} {title} {question}".lower()
    if sport_key == "basketball":
        if slug.startswith("wnba-") or " wnba" in f" {text}":
            return sport_key, "wnba"
        if slug.startswith("nba-") or " nba" in f" {text}":
            return sport_key, "nba"
    if sport_key == "baseball":
        if slug.startswith("mlb-") or " mlb" in f" {text}":
            return sport_key, "mlb"
        if slug.startswith("kbo-") or " kbo" in f" {text}":
            return sport_key, "kbo"
    if sport_key == "tennis":
        if slug.startswith("wta-") or " wta" in f" {text}" or "women" in text:
            return sport_key, "wta"
        if slug.startswith("atp-") and "challenger" in text:
            return sport_key, "atp_challenger"
        if slug.startswith("atp-"):
            return sport_key, "atp"
        if slug.startswith("itf-") and ("women" in text or "womens" in text or "women's" in text):
            return sport_key, "itf_women"
        if slug.startswith("itf-"):
            return sport_key, "itf_men"
    if sport_key == "esports":
        if "valorant" in text:
            return sport_key, "valorant"
        if "league of legends" in text or slug.startswith("lol-"):
            return sport_key, "lol"
        if "counter-strike" in text or "counter strike" in text or slug.startswith("cs2-"):
            return sport_key, "cs2"
    return sport_key, competition_key


def pm_candidates(conn: sqlite3.Connection) -> tuple[list[matching.PlatformCandidate], dict[str, int]]:
    rows = conn.execute(
        """
        SELECT event.event_slug, event.title AS event_title, event.start_date, event.tag_slug, event.tag_id,
               market.market_id, market.question, market.outcomes_json, market.clob_token_ids_json,
               market.active, market.closed, market.enable_order_book
        FROM pm_events AS event
        JOIN pm_markets AS market ON market.event_slug = event.event_slug
        ORDER BY event.event_slug, market.market_id
        """
    ).fetchall()
    candidates: list[matching.PlatformCandidate] = []
    counts = defaultdict(int)
    seen_events: set[str] = set()
    for row in rows:
        event_slug = str(row["event_slug"] or "")
        if event_slug in seen_events:
            continue
        text = f"{row['event_title'] or ''} {row['question'] or ''} {event_slug}"
        if matching.is_excluded_market(text):
            counts["excluded_market"] += 1
            continue
        outcomes = matching.valid_two_participants(matching.parse_json_list(row["outcomes_json"]))
        token_ids = matching.parse_json_list(row["clob_token_ids_json"])
        if outcomes is None or len([token for token in token_ids if str(token or "").strip()]) != 2:
            counts["non_binary"] += 1
            continue
        mapped = None
        tag_slug = str(row["tag_slug"] or "")
        if tag_slug:
            mapped = matching.mapped_category(conn, "pm", "tag_slug", tag_slug)
        if mapped is None and row["tag_id"]:
            mapped = matching.mapped_category(conn, "pm", "tag_slug", f"tag_id:{row['tag_id']}")
        if mapped is None:
            counts["missing_category"] += 1
            continue
        sport_key, competition_key, mapping_status = mapped
        sport_key, competition_key = refine_pm_competition(
            sport_key,
            competition_key,
            event_slug,
            str(row["event_title"] or ""),
            str(row["question"] or ""),
        )
        event_date = matching.date_from_slug_or_text(event_slug, str(row["event_title"] or ""), str(row["question"] or ""))
        if not event_date:
            counts["missing_date"] += 1
            continue
        market_type = matching.market_type_for_sport(sport_key, competition_key)
        candidates.append(
            matching.PlatformCandidate(
                platform="pm",
                platform_event_id=event_slug,
                platform_market_id=str(row["market_id"] or ""),
                sport_key=sport_key,
                competition_key=competition_key,
                event_date=event_date,
                start_time_utc="",
                market_type=market_type,
                participant_names=outcomes,
                source_name=str(row["event_title"] or row["question"] or event_slug),
                context={
                    "tag_slug": tag_slug,
                    "tag_id": str(row["tag_id"] or ""),
                    "mapping_status": mapping_status,
                    "question": str(row["question"] or ""),
                },
            )
        )
        seen_events.add(event_slug)
        counts["candidates"] += 1
    return candidates, dict(counts)


def ks_candidates(conn: sqlite3.Connection) -> tuple[list[matching.PlatformCandidate], dict[str, int]]:
    rows = conn.execute(
        """
        SELECT event.event_ticker, event.series_ticker, event.title AS event_title,
               market.market_ticker, market.title AS market_title, market.yes_sub_title,
               market.status, market.close_time
        FROM ks_events AS event
        JOIN ks_markets AS market ON market.event_ticker = event.event_ticker
        ORDER BY event.event_ticker, market.market_ticker
        """
    ).fetchall()
    by_event: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_event[str(row["event_ticker"] or "")].append(row)

    candidates: list[matching.PlatformCandidate] = []
    counts = defaultdict(int)
    for event_ticker, event_rows in by_event.items():
        if not event_ticker or not event_rows:
            continue
        first = event_rows[0]
        text = " ".join(
            str(value or "")
            for row in event_rows
            for value in (row["event_title"], row["market_title"], row["yes_sub_title"], row["market_ticker"])
        )
        if matching.is_excluded_market(text):
            counts["excluded_market"] += 1
            continue
        outcomes = matching.valid_two_participants([row["yes_sub_title"] for row in event_rows])
        if outcomes is None:
            counts["non_binary"] += 1
            continue
        series_ticker = str(first["series_ticker"] or "")
        mapped = matching.mapped_category(conn, "ks", "series_ticker", series_ticker)
        if mapped is None:
            counts["missing_category"] += 1
            continue
        sport_key, competition_key, mapping_status = mapped
        event_date = matching.date_from_ks_ticker(event_ticker, *(str(row["market_ticker"] or "") for row in event_rows))
        if not event_date:
            counts["missing_date"] += 1
            continue
        candidates.append(
            matching.PlatformCandidate(
                platform="ks",
                platform_event_id=event_ticker,
                platform_market_id=str(first["market_ticker"] or ""),
                sport_key=sport_key,
                competition_key=competition_key,
                event_date=event_date,
                start_time_utc="",
                market_type=matching.market_type_for_sport(sport_key, competition_key),
                participant_names=outcomes,
                source_name=str(first["event_title"] or first["market_title"] or event_ticker),
                context={
                    "series_ticker": series_ticker,
                    "mapping_status": mapping_status,
                    "market_tickers": [str(row["market_ticker"] or "") for row in event_rows],
                },
            )
        )
        counts["candidates"] += 1
    return candidates, dict(counts)


def clear_previous_outputs(conn: sqlite3.Connection, platform: str) -> None:
    conn.execute("DELETE FROM old_index_event_mapping_diagnostics WHERE platform = ?", (platform,))
    if platform == "pm":
        conn.execute("DELETE FROM pm_canonical_event_map WHERE mapping_source = 'index_snapshot_match'")
    elif platform == "ks":
        conn.execute("DELETE FROM ks_canonical_event_map WHERE mapping_source = 'index_snapshot_match'")


def refresh_platform(conn: sqlite3.Connection, platform: str, *, strict_sources: bool = False) -> dict[str, int]:
    matching.ensure_old_index_tables(conn)
    clear_previous_outputs(conn, platform)
    index_events = matching.load_index_events_from_old(conn)
    candidates, extraction_counts = pm_candidates(conn) if platform == "pm" else ks_candidates(conn)
    counts = defaultdict(int)
    counts.update(extraction_counts)
    for candidate in candidates:
        result = matching.match_candidate(candidate, index_events, strict_sources=strict_sources)
        eligible = True
        if result.status == "mapped":
            if platform == "pm":
                matching.upsert_pm_map(conn, candidate, result)
            else:
                matching.upsert_ks_map(conn, candidate, result)
            counts["mapped"] += 1
        else:
            counts[result.reason] += 1
        matching.record_diagnostic(conn, candidate, result, eligible=eligible)
    conn.commit()
    return dict(counts)


def refresh(old_db: Path, platform: str, *, strict_sources: bool = False) -> dict[str, dict[str, int]]:
    with old_market_db.connect(old_db) as conn:
        old_market_db.init_db(conn)
        matching.ensure_old_index_tables(conn)
        platforms = ("pm", "ks") if platform == "all" else (platform,)
        return {selected: refresh_platform(conn, selected, strict_sources=strict_sources) for selected in platforms}


def main() -> int:
    parser = argparse.ArgumentParser(description="Map old DB PM/KS events independently to the copied index snapshot.")
    parser.add_argument("--old-db", default=str(matching.OLD_DB_PATH))
    parser.add_argument("--platform", choices=("pm", "ks", "all"), default="all")
    parser.add_argument("--strict-sources", action="store_true", help="Only match official/static source types, excluding vetted public sources.")
    args = parser.parse_args()

    old_db = old_market_db.ensure_legacy_db_path(Path(args.old_db))
    counts = refresh(old_db, args.platform, strict_sources=args.strict_sources)
    for platform, platform_counts in counts.items():
        print(
            f"{platform} index event map refresh complete: "
            + ", ".join(f"{key}={value}" for key, value in sorted(platform_counts.items()))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
