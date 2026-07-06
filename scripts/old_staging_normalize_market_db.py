#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import old_market_db
import old_sports_taxonomy


@dataclass(frozen=True)
class NormalizedCandidate:
    source: str
    category_key: str
    universe: str
    event_date: str
    canonical_event_id: str
    market_type: str
    competition_gender: str
    gender_source: str
    gender_confidence: str
    match_name: str
    outcome: str
    entity_key: str
    outcome_key: str
    pm_event_slug: str = ""
    pm_market_id: str = ""
    pm_token_id: str = ""
    pm_question: str = ""
    ks_series_ticker: str = ""
    ks_event_ticker: str = ""
    ks_market_ticker: str = ""
    ks_title: str = ""


def normalize_db(conn: sqlite3.Connection, sports: str = "all") -> dict[str, int]:
    # normalized_contracts is a derived inventory table for this staging phase.
    old_market_db.reset_normalized_contracts(conn)
    conn.execute("DELETE FROM data_quality_warnings WHERE stage = 'normalize_old_market_db'")
    pm_count = normalize_pm(conn, sports)
    ks_count = normalize_ks(conn, sports)
    conn.commit()
    return {"pm_normalized": pm_count, "ks_normalized": ks_count}


def normalize_pm(conn: sqlite3.Connection, sports: str) -> int:
    count = 0
    for row in pm_token_rows(conn):
        sources = pm_sources(conn, str(row["event_slug"]))
        market_raw = parse_json_object(row["market_raw_json"])
        event_raw = parse_json_object(row["event_raw_json"])
        classification_context = pm_classification_context(
            str(row["market_question"] or ""),
            market_raw,
            event_raw,
            str(row["event_slug"] or ""),
        )
        classification = old_sports_taxonomy.classify_pm_event(
            str(row["event_slug"] or ""),
            str(row["event_title"] or ""),
            sources,
            classification_context,
        )
        if not include_sport(classification.category_key, classification.universe, sports):
            continue
        if classification.warning_message:
            old_market_db.record_warning(
                conn,
                "normalize_old_market_db",
                classification.warning_message,
                category_key=classification.category_key,
                context={
                    "venue": "pm",
                    "event_slug": row["event_slug"],
                    "token_id": row["token_id"],
                    "sources": sources,
                },
            )
        event_date = old_sports_taxonomy.event_date_from_pm(
            str(row["event_slug"] or ""),
            str(row["start_date"] or ""),
            str(row["event_title"] or ""),
        )
        entity_key = old_sports_taxonomy.entity_key(
            classification.category_key,
            classification.universe,
            classification.competition_gender,
            str(row["outcome_name"] or ""),
        )
        candidate = NormalizedCandidate(
            source="pm",
            category_key=classification.category_key,
            universe=classification.universe,
            event_date=event_date,
            canonical_event_id=f"pm:{row['event_slug']}",
            market_type=classification.market_type,
            competition_gender=classification.competition_gender,
            gender_source=classification.gender_source,
            gender_confidence=classification.gender_confidence,
            match_name=str(row["event_title"] or row["market_question"] or ""),
            outcome=str(row["outcome_name"] or ""),
            entity_key=entity_key,
            outcome_key=entity_key or old_sports_taxonomy.normalize_key(str(row["outcome_name"] or "")),
            pm_event_slug=str(row["event_slug"] or ""),
            pm_market_id=str(row["market_id"] or ""),
            pm_token_id=str(row["token_id"] or ""),
            pm_question=str(row["market_question"] or ""),
        )
        old_market_db.upsert_normalized_candidate(conn, candidate, universe=classification.universe)
        count += 1
    return count


def normalize_ks(conn: sqlite3.Connection, sports: str) -> int:
    count = 0
    for row in ks_market_rows(conn):
        classification = old_sports_taxonomy.classify_ks_market(
            str(row["series_ticker"] or ""),
            str(row["market_ticker"] or ""),
            str(row["title"] or ""),
        )
        if not include_sport(classification.category_key, classification.universe, sports):
            continue
        if classification.warning_message:
            old_market_db.record_warning(
                conn,
                "normalize_old_market_db",
                classification.warning_message,
                category_key=classification.category_key,
                context={
                    "venue": "ks",
                    "series_ticker": row["series_ticker"],
                    "market_ticker": row["market_ticker"],
                },
            )
        event_date = old_sports_taxonomy.event_date_from_ks(
            str(row["market_ticker"] or ""),
            str(row["close_time"] or ""),
            str(row["title"] or ""),
        )
        entity_key = old_sports_taxonomy.entity_key(
            classification.category_key,
            classification.universe,
            classification.competition_gender,
            str(row["yes_sub_title"] or ""),
        )
        candidate = NormalizedCandidate(
            source="ks",
            category_key=classification.category_key,
            universe=classification.universe,
            event_date=event_date,
            canonical_event_id=f"ks:{row['event_ticker']}",
            market_type=classification.market_type,
            competition_gender=classification.competition_gender,
            gender_source=classification.gender_source,
            gender_confidence=classification.gender_confidence,
            match_name=str(row["title"] or ""),
            outcome=str(row["yes_sub_title"] or ""),
            entity_key=entity_key,
            outcome_key=entity_key or old_sports_taxonomy.normalize_key(str(row["yes_sub_title"] or "")),
            ks_series_ticker=str(row["series_ticker"] or ""),
            ks_event_ticker=str(row["event_ticker"] or ""),
            ks_market_ticker=str(row["market_ticker"] or ""),
            ks_title=str(row["title"] or ""),
        )
        old_market_db.upsert_normalized_candidate(conn, candidate, universe=classification.universe)
        count += 1
    return count


def pm_token_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            token.token_id,
            token.outcome_name,
            token.event_slug,
            market.market_id,
            market.question AS market_question,
            event.title AS event_title,
            event.start_date,
            event.raw_json AS event_raw_json,
            event.tag_slug,
            event.tag_id,
            market.raw_json AS market_raw_json
        FROM pm_tokens token
        JOIN pm_markets market ON market.market_id = token.market_id
        JOIN pm_events event ON event.event_slug = token.event_slug
        WHERE token.active = 1
          AND token.closed = 0
          AND market.active = 1
          AND market.closed = 0
          AND market.enable_order_book = 1
          AND event.active = 1
          AND event.closed = 0
        ORDER BY event.event_slug, market.market_id, token.outcome_index
        """
    ).fetchall()


def parse_json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = old_market_db.json.loads(str(value or "{}"))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def pm_classification_context(question: str, market_raw: dict[str, Any], event_raw: dict[str, Any], event_slug: str = "") -> str:
    parts = [question]
    for payload in (market_raw, event_raw):
        for key in ("description", "rules", "resolutionSource", "league", "groupItemTitle", "question", "title"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                parts.append(value)

    for payload in (market_raw, event_raw):
        metadata = payload.get("eventMetadata")
        if isinstance(metadata, dict):
            for key in ("league", "tournament", "competition", "series"):
                value = metadata.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
        sport = payload.get("sport")
        if isinstance(sport, dict):
            for key in ("sport", "league", "series"):
                value = sport.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
        for key in ("seriesSlug", "series_slug", "ticker"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        series = payload.get("series")
        if isinstance(series, list):
            for item in series:
                if not isinstance(item, dict):
                    continue
                for key in ("slug", "ticker", "title"):
                    value = item.get(key)
                    if isinstance(value, str) and value:
                        parts.append(value)

    strong_text = " ".join(parts).lower()
    slug = event_slug.lower()
    if "itf women" in strong_text or "women's itf" in strong_text or "womens itf" in strong_text:
        parts.append("ITF Women")
    if "itf men" in strong_text or "men's itf" in strong_text or "mens itf" in strong_text:
        parts.append("ITF Men")
    if slug.startswith("atp-") and ("atp challenger" in strong_text or "challenger" in strong_text):
        parts.append("ATP Challenger")
    return " ".join(part for part in parts if part)


def ks_market_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            market_ticker,
            event_ticker,
            series_ticker,
            title,
            yes_sub_title,
            close_time,
            status
        FROM ks_markets
        WHERE LOWER(COALESCE(status, 'open')) IN ('', 'open', 'active')
        ORDER BY series_ticker, event_ticker, market_ticker
        """
    ).fetchall()


def pm_sources(conn: sqlite3.Connection, event_slug: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT source_type, source_value
        FROM pm_event_sources
        WHERE event_slug = ?
        ORDER BY source_type, source_value
        """,
        (event_slug,),
    ).fetchall()
    return [f"{row['source_type']}:{row['source_value']}" for row in rows]


def include_sport(category_key: str, universe: str, sports: str) -> bool:
    requested = {part.strip().lower().replace("-", "_") for part in sports.split(",") if part.strip()}
    if not requested or "all" in requested:
        return True
    return category_key in requested or universe in requested


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize PM/KS sports inventory from the local SQLite market DB.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="all")
    args = parser.parse_args()

    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        counts = normalize_db(conn, args.sports)
        row_counts = old_market_db.row_counts(conn)

    print(
        "staging DB normalize complete: "
        f"pm_normalized={counts['pm_normalized']}; ks_normalized={counts['ks_normalized']}; db={args.db}"
    )
    print(", ".join(f"{key}={value}" for key, value in row_counts.items()))


if __name__ == "__main__":
    main()
