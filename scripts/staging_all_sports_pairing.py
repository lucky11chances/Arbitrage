#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pipeline_core as core
import sports_pairing
import sports_inventory
import sports_registry
import universe_adapters


OUTPUT_FIELDS = sports_pairing.DIAGNOSTIC_FIELDS
DEFAULT_OUTPUT = "data/staging/all_sports_pairing_candidates_latest.csv"
DEFAULT_ARB_OUTPUT = "data/staging/all_sports_arb_snapshot_latest.csv"
MATCH_SOURCE = "staging_date_entity_semantic_match"
SERIES_BY_CATEGORY = {
    "world_cup": ("KXWCGAME",),
    "mlb": ("KXMLBGAME",),
    "nba": ("KXNBAGAME",),
    "football": ("KXNFLGAME", "KXCFBGAME", "KXNCAAFGAME"),
    "basketball": ("KXWNBA", "KXNBAGAME", "KXNCAABGAME"),
    "hockey": ("KXNHLGAME",),
    "formula_1": ("KXF1",),
    "soccer": ("KXWCGAME", "KXSOCCERGAME"),
    "ufc": ("KXUFC", "KXUFCGAME"),
    "boxing": ("KXBOXING", "KXBOXINGGAME"),
    "tennis": ("KXTENNIS", "KXTENNISGAME"),
    "table_tennis": ("KXTTGAME", "KXTABLETENNIS"),
    "pickleball": ("KXPICKLEBALL", "KXPICKLEBALLGAME"),
    "cricket": ("KXCRICKET", "KXCRICKETGAME"),
    "rugby": ("KXRUGBY", "KXRUGBYGAME"),
    "lacrosse": ("KXLACROSSE", "KXLACROSSEGAME"),
    "baseball": ("KXMLBGAME", "KXBASEBALLGAME"),
    "golf": ("KXGOLF",),
    "esports": ("KXLOLGAME", "KXCS2GAME", "KXVALORANTGAME"),
}
VS_RE = re.compile(r"^(?P<a>.+?)\s+(?:vs\.?|v\.?|at|@)\s+(?P<b>.+?)(?:\s+\(|$)", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
KS_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
YES_WIN_RE = re.compile(r"^Will (?P<outcome>.+?) win(?:\s+the|\s+on|\?)", re.IGNORECASE)
YES_GENERIC_RE = re.compile(r"^Will (?P<outcome>.+?) (?:win|be|make|have|get|score|lead|finish)", re.IGNORECASE)


@dataclass(frozen=True)
class CandidateOutcome:
    source: str
    category_key: str
    event_date: str
    canonical_event_id: str
    market_type: str
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
    ks_yes_outcome: str = ""


def normalize_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", lowered)


def comparable_key(value: str) -> str:
    value = re.sub(r"^\s*the\s+", "", value, flags=re.IGNORECASE)
    key = normalize_key(value)
    aliases = universe_adapters.SOCCER_TEAM_ALIASES
    return aliases.get(key, key)


def entity_set_key(values: list[str]) -> str:
    return "|".join(sorted(comparable_key(value) for value in values if comparable_key(value)))


def extract_event_date(event: dict[str, Any]) -> str:
    for value in (event.get("slug"), event.get("startDate"), event.get("endDate")):
        match = DATE_RE.search(str(value or ""))
        if match:
            return match.group(1)
    return ""


def extract_year(value: str) -> str:
    match = re.search(r"\b(20\d{2})\b", value)
    return match.group(1) if match else ""


def parse_ks_date(ticker: str, fallback: str) -> str:
    match = KS_DATE_RE.search(ticker)
    if match:
        return universe_adapters.date_from_yy_mon_day(match.group(1), match.group(2), match.group(3)) or ""
    close_year = extract_year(fallback)
    return close_year


def parse_matchup(title: str) -> tuple[str, str] | None:
    cleaned = clean_title(title)
    cleaned = re.sub(r"^Will .+? win the ", "", cleaned, flags=re.IGNORECASE)
    match = VS_RE.match(cleaned)
    if not match:
        return None
    left = strip_market_words(match.group("a"))
    right = strip_market_words(match.group("b"))
    if not left or not right:
        return None
    return left, right


def clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("Winner?", "").replace(" winner?", "")).strip(" ?")


def strip_market_words(value: str) -> str:
    value = re.sub(r"^Will\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+win\s+the\s+.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+win\s+.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\s+(?:Pro Football|College Football|Women'?s Pro Basketball|MLB|NBA|NHL|F1|Formula 1)\s+game$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip(" ?")


def yes_no_outcome(question: str) -> str:
    if universe_adapters.is_draw_or_tie(question):
        return "Draw"
    for pattern in (YES_WIN_RE, YES_GENERIC_RE):
        match = pattern.match(question.strip())
        if match:
            return strip_market_words(match.group("outcome"))
    return strip_market_words(question)


def market_type_for(category_key: str, outcomes: list[str], question: str) -> str:
    lowered = [outcome.strip().lower() for outcome in outcomes]
    if len(outcomes) == 3 and any(universe_adapters.is_draw_or_tie(outcome) for outcome in outcomes):
        return "three_way_moneyline"
    if category_key in {"formula_1", "golf"}:
        return "future_winner"
    if category_key in {"ufc", "boxing", "tennis", "table_tennis", "pickleball"}:
        return "match_winner"
    if len(outcomes) == 2 and lowered == ["yes", "no"]:
        question_lower = question.lower()
        if "championship" in question_lower or "tournament" in question_lower:
            return "future_winner"
        return "proposition_yes"
    return "game_winner"


def pm_candidates(category: sports_registry.SportsCategory, limit: int) -> tuple[list[CandidateOutcome], list[str]]:
    outcomes: list[CandidateOutcome] = []
    warnings: list[str] = []
    for _tag_slug, _tag_id, events in sports_inventory.fetch_category_events(category, limit, warnings):
        for event in events:
            event_date = extract_event_date(event)
            title = str(event.get("title") or "")
            title_matchup = parse_matchup(title)
            for market in event.get("markets") or []:
                if not market.get("active") or market.get("closed") or not market.get("enableOrderBook", True):
                    continue
                labels = [str(outcome) for outcome in core.parse_json_list(market.get("outcomes"))]
                token_ids = [str(token_id) for token_id in core.parse_json_list(market.get("clobTokenIds"))]
                if len(labels) != len(token_ids) or not labels:
                    continue
                question = str(market.get("question") or market.get("groupItemTitle") or "")
                market_type = market_type_for(category.key, labels, question)
                parsed = pm_market_outcomes(category, event, market, labels, token_ids, event_date, title_matchup, market_type)
                outcomes.extend(parsed)
    return outcomes, warnings


def pm_market_outcomes(
    category: sports_registry.SportsCategory,
    event: dict[str, Any],
    market: dict[str, Any],
    labels: list[str],
    token_ids: list[str],
    event_date: str,
    title_matchup: tuple[str, str] | None,
    market_type: str,
) -> list[CandidateOutcome]:
    event_slug = str(event.get("slug") or "")
    title = str(event.get("title") or "")
    question = str(market.get("question") or market.get("groupItemTitle") or "")
    lowered = [label.strip().lower() for label in labels]
    if lowered == ["yes", "no"]:
        outcome = yes_no_outcome(question)
        if market_type == "future_winner":
            row_event_date = extract_year(question) or extract_year(title)
        else:
            row_event_date = event_date
        entities = list(title_matchup or ())
        if not entities and market_type == "future_winner":
            entity_key = f"single:{comparable_key(future_context(category.key, question, outcome))}"
        else:
            entity_key = entity_set_key(entities) if entities else f"single:{comparable_key(future_context(category.key, question, outcome))}"
        return [
            CandidateOutcome(
                source="pm",
                category_key=category.key,
                event_date=row_event_date,
                canonical_event_id=canonical_id(category.key, row_event_date, entity_key),
                market_type=market_type,
                match_name=clean_title(title or question),
                outcome=outcome,
                entity_key=entity_key,
                outcome_key=comparable_key(outcome),
                pm_event_slug=event_slug,
                pm_market_id=str(market.get("id") or ""),
                pm_token_id=token_ids[0],
                pm_question=question,
            )
        ]

    if market_type == "future_winner":
        event_date = extract_year(question) or extract_year(title) or event_date
        context_key = f"single:{comparable_key(future_context(category.key, question or title, labels[0]))}"
        return [
            CandidateOutcome(
                source="pm",
                category_key=category.key,
                event_date=event_date,
                canonical_event_id=canonical_id(category.key, event_date, context_key),
                market_type=market_type,
                match_name=clean_title(title or question),
                outcome=label,
                entity_key=context_key,
                outcome_key=outcome_key(label),
                pm_event_slug=event_slug,
                pm_market_id=str(market.get("id") or ""),
                pm_token_id=token_id,
                pm_question=question,
            )
            for label, token_id in zip(labels, token_ids)
        ]

    entities = [label for label in labels if not universe_adapters.is_draw_or_tie(label)]
    if title_matchup:
        entities = list(title_matchup)
    entity_key = entity_set_key(entities)
    rows: list[CandidateOutcome] = []
    for label, token_id in zip(labels, token_ids):
        rows.append(
            CandidateOutcome(
                source="pm",
                category_key=category.key,
                event_date=event_date,
                canonical_event_id=canonical_id(category.key, event_date, entity_key),
                market_type=market_type,
                match_name=clean_title(title),
                outcome=label,
                entity_key=entity_key,
                outcome_key=outcome_key(label),
                pm_event_slug=event_slug,
                pm_market_id=str(market.get("id") or ""),
                pm_token_id=token_id,
                pm_question=question,
            )
        )
    return rows


def future_context(category_key: str, question: str, outcome: str) -> str:
    normalized_outcome = re.escape(outcome)
    context = re.sub(normalized_outcome, "", question, flags=re.IGNORECASE)
    context = re.sub(r"\bWill\b|\bwin\b|\bbe\b|\?", "", context, flags=re.IGNORECASE)
    context = re.sub(r"\bthe\b", "", context, flags=re.IGNORECASE)
    context = re.sub(r"\bchampion\b", "championship", context, flags=re.IGNORECASE)
    year = extract_year(question)
    if year:
        context = context.replace(year, "")
    return f"{category_key}:{context.strip()}"


def ks_candidates(category_key: str, series_tickers: tuple[str, ...], limit: int) -> tuple[list[CandidateOutcome], list[str]]:
    outcomes: list[CandidateOutcome] = []
    warnings: list[str] = []
    for series_ticker in series_tickers:
        try:
            markets = core.fetch_kalshi_series_markets(series_ticker, limit)
        except Exception as exc:  # noqa: BLE001 - staging should continue across missing series.
            warnings.append(f"{category_key} KS fetch failed for {series_ticker}: {exc}")
            continue
        if not markets:
            warnings.append(f"{category_key} KS series has no open markets: {series_ticker}")
            continue
        outcomes.extend(ks_outcomes_from_markets(category_key, series_ticker, markets, limit))
    return outcomes, warnings


def ks_outcomes_from_markets(
    category_key: str,
    series_ticker: str,
    markets: list[dict[str, Any]],
    limit: int,
) -> list[CandidateOutcome]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        grouped[str(market.get("event_ticker") or market.get("ticker") or "")].append(market)

    rows: list[CandidateOutcome] = []
    for event_ticker, event_markets in grouped.items():
        if not event_ticker:
            continue
        title = clean_title(str(event_markets[0].get("title") or ""))
        close_time = str(event_markets[0].get("close_time") or "")
        event_date = parse_ks_date(event_ticker, close_time or title)
        yes_outcomes = [str(market.get("yes_sub_title") or "") for market in event_markets if market.get("yes_sub_title")]
        title_matchup = parse_matchup(title)
        market_type = ks_market_type(category_key, title, yes_outcomes)
        if market_type == "future_winner":
            entities = []
        elif title_matchup:
            entities = list(title_matchup)
        elif len(yes_outcomes) >= 2:
            entities = [outcome for outcome in yes_outcomes if not universe_adapters.is_draw_or_tie(outcome)]
        else:
            entities = [future_context(category_key, title, yes_outcomes[0])] if yes_outcomes else []
        entity_key = entity_set_key(entities) if len(entities) > 1 else f"single:{comparable_key(entities[0])}" if entities else ""
        for market in event_markets:
            yes_outcome = str(market.get("yes_sub_title") or "")
            if not yes_outcome:
                continue
            row_title = clean_title(str(market.get("title") or title))
            row_event_date = event_date
            row_entity_key = entity_key
            if market_type == "future_winner":
                row_event_date = extract_year(row_title) or event_date
                row_entity_key = f"single:{comparable_key(future_context(category_key, row_title, yes_outcome))}"
            rows.append(
                CandidateOutcome(
                    source="ks",
                    category_key=category_key,
                    event_date=row_event_date,
                    canonical_event_id=canonical_id(category_key, row_event_date, row_entity_key),
                    market_type=market_type,
                    match_name=row_title,
                    outcome=yes_outcome,
                    entity_key=row_entity_key,
                    outcome_key=outcome_key(yes_outcome),
                    ks_series_ticker=series_ticker,
                    ks_event_ticker=event_ticker,
                    ks_market_ticker=str(market.get("ticker") or ""),
                    ks_title=row_title,
                    ks_yes_outcome=yes_outcome,
                )
            )
            if len(rows) >= limit * 4:
                return rows
    return rows


def ks_market_type(category_key: str, title: str, yes_outcomes: list[str]) -> str:
    if len(yes_outcomes) == 3 and any(universe_adapters.is_draw_or_tie(outcome) for outcome in yes_outcomes):
        return "three_way_moneyline"
    if category_key in {"formula_1", "golf"} or "championship" in title.lower():
        return "future_winner"
    if category_key in {"ufc", "boxing", "tennis", "table_tennis", "pickleball"}:
        return "match_winner"
    return "game_winner"


def outcome_key(value: str) -> str:
    return "draw" if universe_adapters.is_draw_or_tie(value) else comparable_key(value)


def canonical_id(category_key: str, event_date: str, entity_key: str) -> str:
    return f"{category_key}:{event_date}:{entity_key}"


def pair_candidates(
    category: sports_registry.SportsCategory,
    pm_rows: list[CandidateOutcome],
    ks_rows: list[CandidateOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    output_rows: list[dict[str, Any]] = []
    pairs: list[core.PairedContract] = []
    pm_index: dict[tuple[str, str, str, str], list[CandidateOutcome]] = defaultdict(list)
    for row in pm_rows:
        if not row.event_date or not row.entity_key or not row.outcome_key:
            continue
        pm_index[(row.event_date, row.entity_key, row.market_type, row.outcome_key)].append(row)

    for ks_row in ks_rows:
        key = (ks_row.event_date, ks_row.entity_key, ks_row.market_type, ks_row.outcome_key)
        matches = pm_index.get(key, [])
        if not matches:
            output_rows.append(candidate_status_row(category.key, "unmatched", "no PM outcome with same date/entity/outcome", None, ks_row))
            continue
        if len(matches) > 1:
            warnings.append(f"{category.key} ambiguous PM matches for {key}; skipped")
            output_rows.append(candidate_status_row(category.key, "ambiguous", "multiple PM outcomes matched same KS outcome", matches[0], ks_row))
            continue
        pm_row = matches[0]
        pair = core.PairedContract(
            universe=category.arb_universe or category.key,
            category="sports",
            match_name=pm_row.match_name or ks_row.match_name,
            event_date=pm_row.event_date,
            canonical_event_id=pm_row.canonical_event_id,
            market_type=pm_row.market_type,
            pm_yes_outcome=pm_row.outcome,
            pm_event_slug=pm_row.pm_event_slug,
            pm_market_id=pm_row.pm_market_id,
            pm_token_id=pm_row.pm_token_id,
            ks_yes_outcome=ks_row.outcome,
            ks_event_ticker=ks_row.ks_event_ticker,
            ks_market_ticker=ks_row.ks_market_ticker,
            schedule_source=MATCH_SOURCE,
        )
        pairs.append(pair)
        output_rows.append(candidate_status_row(category.key, "paired", "", pm_row, ks_row))
    return pairs, output_rows, warnings


def candidate_status_row(
    category_key: str,
    status: str,
    reason: str,
    pm_row: CandidateOutcome | None,
    ks_row: CandidateOutcome | None,
) -> dict[str, Any]:
    base = pm_row or ks_row
    assert base is not None
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "category_key": category_key,
        "status": status,
        "reason": reason,
        "event_date": base.event_date,
        "canonical_event_id": base.canonical_event_id,
        "market_type": base.market_type,
        "match_name": base.match_name,
        "outcome": base.outcome,
        "pm_event_slug": pm_row.pm_event_slug if pm_row else "",
        "pm_market_id": pm_row.pm_market_id if pm_row else "",
        "pm_token_id": pm_row.pm_token_id if pm_row else "",
        "pm_question": pm_row.pm_question if pm_row else "",
        "ks_series_ticker": ks_row.ks_series_ticker if ks_row else "",
        "ks_event_ticker": ks_row.ks_event_ticker if ks_row else "",
        "ks_market_ticker": ks_row.ks_market_ticker if ks_row else "",
        "ks_title": ks_row.ks_title if ks_row else "",
        "ks_yes_outcome": ks_row.ks_yes_outcome if ks_row else "",
        "entity_key": base.entity_key,
        "outcome_key": base.outcome_key,
    }


def run(args: argparse.Namespace) -> None:
    selected = sports_registry.inventory_categories() if args.sports == "all" else [
        sports_registry.category_for_key(sport.strip()) for sport in args.sports.split(",") if sport.strip()
    ]
    all_pairs: list[core.PairedContract] = []
    all_rows: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    for category in selected:
        if category.arb_status == "paired" and category.arb_universe:
            pairs, warnings = universe_adapters.pair_binary_universe(
                category.arb_universe,
                args.pm_limit,
                args.ks_limit,
                args.from_date,
            ) if category.key != "formula_1" else sports_pairing.f1_drivers_championship_pairing(
                args.pm_limit,
                args.ks_limit,
                args.from_date,
            )
            all_pairs.extend(pairs)
            all_rows.extend(sports_pairing.pair_status_row(category.key, pair, "paired_existing", "existing production pairer") for pair in pairs)
            all_warnings.extend(warnings)
            print(f"{category.key}: existing_pairer paired={len(pairs)} universe={category.arb_universe}")
            continue
        pairs, rows, pair_warnings = sports_pairing.build_category_pairing(category, args.pm_limit, args.ks_limit, args.from_date)
        all_pairs.extend(pairs)
        all_rows.extend(rows)
        all_warnings.extend(pair_warnings)
        print(
            f"{category.key}: diagnostics={len(rows)} paired={len(pairs)} "
            f"series={','.join(category.kalshi_candidate_series)}"
        )

    core.write_latest_csv(all_rows, OUTPUT_FIELDS, Path(args.output))
    print(f"wrote {len(all_rows)} pairing candidate rows to {args.output}")
    if args.with_bbo:
        arb_rows, quote_warnings = core.build_binary_rows(all_pairs, args.bbo_workers)
        all_warnings.extend(quote_warnings)
        core.write_latest_csv(arb_rows, core.BINARY_CSV_FIELDS, Path(args.arb_output))
        positives = sum(1 for row in arb_rows if row.get("alert") == "ALERT")
        print(f"wrote {len(arb_rows)} staging arb rows to {args.arb_output}; positive_edges={positives}")
    if args.show_warnings:
        for warning in all_warnings:
            print(f"warning: {warning}")


def empty_status(category_key: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "category_key": category_key,
        "status": status,
        "reason": reason,
        "event_date": "",
        "canonical_event_id": "",
        "market_type": "",
        "match_name": "",
        "outcome": "",
        "pm_event_slug": "",
        "pm_market_id": "",
        "pm_token_id": "",
        "pm_question": "",
        "ks_series_ticker": "",
        "ks_event_ticker": "",
        "ks_market_ticker": "",
        "ks_title": "",
        "ks_yes_outcome": "",
        "entity_key": "",
        "outcome_key": "",
    }


def pair_status_row(category_key: str, pair: core.PairedContract, status: str, reason: str) -> dict[str, Any]:
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "category_key": category_key,
        "status": status,
        "reason": reason,
        "event_date": pair.event_date,
        "canonical_event_id": pair.canonical_event_id,
        "market_type": pair.market_type,
        "match_name": pair.match_name,
        "outcome": pair.pm_yes_outcome,
        "pm_event_slug": pair.pm_event_slug,
        "pm_market_id": pair.pm_market_id,
        "pm_token_id": pair.pm_token_id,
        "pm_question": "",
        "ks_series_ticker": pair.ks_event_ticker.split("-")[0] if pair.ks_event_ticker else "",
        "ks_event_ticker": pair.ks_event_ticker,
        "ks_market_ticker": pair.ks_market_ticker,
        "ks_title": "",
        "ks_yes_outcome": pair.ks_yes_outcome,
        "entity_key": pair.canonical_event_id,
        "outcome_key": outcome_key(pair.pm_yes_outcome),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage all-sports PM/KS semantic pairing without touching production loop.")
    parser.add_argument("--sports", default="all")
    parser.add_argument("--pm-limit", type=int, default=200)
    parser.add_argument("--ks-limit", type=int, default=200)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--arb-output", default=DEFAULT_ARB_OUTPUT)
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--with-bbo", action="store_true")
    parser.add_argument("--bbo-workers", type=int, default=12)
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
