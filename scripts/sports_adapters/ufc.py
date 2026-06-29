from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import pipeline_core as core
import sports_pairing
import sports_registry
import universe_adapters
from sports_adapters.base import SportAdapter


PM_UFC_SLUG_RE = re.compile(r"^ufc-.+-(?P<date>\d{4}-\d{2}-\d{2})$")
KS_UFC_SERIES = ("KXUFCFIGHT",)
SCHEDULE_SOURCE = "pm_ks_ufc_full_name_date_crosscheck"
FIGHTER_ALIASES = {
    "abusmagomedov": "abusupiyanmagomedov",
    "sharamagomedov": "sharabutdinmagomedov",
}


@dataclass(frozen=True)
class UfcOutcome:
    event_date: str
    entity_key: str
    outcome_key: str
    outcome_name: str
    match_name: str
    pm_event_slug: str = ""
    pm_market_id: str = ""
    pm_token_id: str = ""
    pm_question: str = ""
    ks_series_ticker: str = ""
    ks_event_ticker: str = ""
    ks_market_ticker: str = ""
    ks_title: str = ""

    @property
    def canonical_event_id(self) -> str:
        return f"ufc:{self.event_date}:{self.entity_key}"


def fighter_key(value: str) -> str:
    key = sports_pairing.normalize_key(value)
    return FIGHTER_ALIASES.get(key, key)


def entity_key(fighters: list[str]) -> str:
    return "|".join(sorted(fighter_key(fighter) for fighter in fighters))


def parse_pm_ufc_date(slug: str) -> str | None:
    match = PM_UFC_SLUG_RE.match(slug)
    return match.group("date") if match else None


def parse_ks_ufc_date(event_ticker: str) -> str | None:
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not match:
        return None
    return universe_adapters.date_from_yy_mon_day(match.group(1), match.group(2), match.group(3))


def is_full_fight_market(question: str, outcomes: list[str]) -> bool:
    lowered = question.lower()
    if any(term in lowered for term in ("ko", "tko", "submission", "distance", "round", "o/u", "over", "under")):
        return False
    outcome_set = {outcome.strip().lower() for outcome in outcomes}
    if outcome_set in ({"yes", "no"}, {"over", "under"}):
        return False
    return len(outcomes) == 2


@lru_cache(maxsize=16)
def discover_pm_ufc(limit: int, from_date: str | None) -> list[UfcOutcome]:
    rows: list[UfcOutcome] = []
    for event in core.fetch_polymarket_events(("ufc",), limit):
        slug = str(event.get("slug") or "")
        event_date = parse_pm_ufc_date(slug)
        if not event_date or (from_date and event_date < from_date):
            continue
        title = str(event.get("title") or "")
        for market in event.get("markets") or []:
            if not market.get("active") or market.get("closed") or not market.get("enableOrderBook", True):
                continue
            question = str(market.get("question") or market.get("groupItemTitle") or "")
            outcomes = [str(outcome) for outcome in core.parse_json_list(market.get("outcomes"))]
            token_ids = [str(token_id) for token_id in core.parse_json_list(market.get("clobTokenIds"))]
            if len(outcomes) != len(token_ids) or not is_full_fight_market(question, outcomes):
                continue
            key = entity_key(outcomes)
            for outcome, token_id in zip(outcomes, token_ids):
                rows.append(
                    UfcOutcome(
                        event_date=event_date,
                        entity_key=key,
                        outcome_key=fighter_key(outcome),
                        outcome_name=outcome,
                        match_name=title or question,
                        pm_event_slug=slug,
                        pm_market_id=str(market.get("id") or ""),
                        pm_token_id=token_id,
                        pm_question=question,
                    )
                )
    rows.sort(key=lambda row: (row.event_date, row.entity_key, row.outcome_key))
    return rows


@lru_cache(maxsize=16)
def discover_ks_ufc(limit: int) -> list[UfcOutcome]:
    rows: list[UfcOutcome] = []
    for series_ticker in KS_UFC_SERIES:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for market in core.fetch_kalshi_series_markets(series_ticker, limit):
            event_ticker = str(market.get("event_ticker") or "")
            if event_ticker:
                grouped[event_ticker].append(market)
        for event_ticker, markets in grouped.items():
            if len(markets) != 2:
                continue
            event_date = parse_ks_ufc_date(event_ticker)
            if not event_date:
                continue
            outcomes = [str(market.get("yes_sub_title") or "") for market in markets]
            if any(not outcome for outcome in outcomes):
                continue
            key = entity_key(outcomes)
            title = str(markets[0].get("title") or "")
            for market, outcome in zip(markets, outcomes):
                rows.append(
                    UfcOutcome(
                        event_date=event_date,
                        entity_key=key,
                        outcome_key=fighter_key(outcome),
                        outcome_name=outcome,
                        match_name=title,
                        ks_series_ticker=series_ticker,
                        ks_event_ticker=event_ticker,
                        ks_market_ticker=str(market.get("ticker") or ""),
                        ks_title=str(market.get("title") or title),
                    )
                )
    rows.sort(key=lambda row: (row.event_date, row.entity_key, row.outcome_key))
    return rows


def pair_ufc(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    pm_rows = discover_pm_ufc(pm_limit, from_date)
    ks_rows = discover_ks_ufc(ks_limit)
    if from_date:
        ks_rows = [row for row in ks_rows if row.event_date >= from_date]

    pm_counts = Counter((row.event_date, row.entity_key, row.outcome_key) for row in pm_rows)
    ks_counts = Counter((row.event_date, row.entity_key, row.outcome_key) for row in ks_rows)
    pm_index = {(row.event_date, row.entity_key, row.outcome_key): row for row in pm_rows}

    warnings: list[str] = []
    pairs: list[core.PairedContract] = []
    for ks_row in ks_rows:
        key = (ks_row.event_date, ks_row.entity_key, ks_row.outcome_key)
        if ks_counts[key] != 1:
            warnings.append(f"ambiguous KS UFC outcome skipped: {key}")
            continue
        if pm_counts[key] != 1:
            continue
        pm_row = pm_index[key]
        pairs.append(
            core.PairedContract(
                universe="ufc",
                category="ufc",
                match_name=pm_row.match_name or ks_row.match_name,
                event_date=pm_row.event_date,
                canonical_event_id=pm_row.canonical_event_id,
                market_type="match_winner",
                pm_yes_outcome=pm_row.outcome_name,
                pm_event_slug=pm_row.pm_event_slug,
                pm_market_id=pm_row.pm_market_id,
                pm_token_id=pm_row.pm_token_id,
                ks_yes_outcome=ks_row.outcome_name,
                ks_event_ticker=ks_row.ks_event_ticker,
                ks_market_ticker=ks_row.ks_market_ticker,
                schedule_source=SCHEDULE_SOURCE,
            )
        )
    pairs.sort(key=lambda pair: (pair.event_date, pair.canonical_event_id, pair.pm_yes_outcome))
    return pairs, warnings


def build_ufc_diagnostics(
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    pairs, warnings = pair_ufc(pm_limit, ks_limit, from_date)
    rows = [sports_pairing.pair_status_row("ufc", pair, "paired_existing", "UFC full-name/date pairer") for pair in pairs]
    if not rows:
        rows = [sports_pairing.empty_status("ufc", "unmatched_semantics", "UFC pairer returned no safe pairs")]
    return rows, warnings


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("ufc"),
    pairer=pair_ufc,
    diagnostics_builder=build_ufc_diagnostics,
)
