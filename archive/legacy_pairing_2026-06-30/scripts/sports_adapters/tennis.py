from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import pipeline_core as core
import sports_pairing
import sports_registry
import universe_adapters
from sports_adapters.base import SportAdapter


PM_TENNIS_SLUG_RE = re.compile(r"^(?P<namespace>atp|wta|itf)-.+-(?P<date>\d{4}-\d{2}-\d{2})$")
KS_TENNIS_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
SCHEDULE_SOURCE = "pm_ks_tennis_full_name_date_crosscheck"
KS_SERIES_NAMESPACES = {
    "KXATPMATCH": "atp",
    "KXATPCHALLENGERMATCH": "atp_challenger",
    "KXWTAMATCH": "wta",
    "KXITFMATCH": "itf_men",
    "KXITFWMATCH": "itf_women",
}
KS_TENNIS_SERIES = tuple(KS_SERIES_NAMESPACES)


@dataclass(frozen=True)
class TennisOutcome:
    event_date: str
    namespace: str
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
        return f"tennis:{self.namespace}:{self.event_date}:{self.entity_key}"


def player_key(value: str) -> str:
    return sports_pairing.normalize_key(value)


def entity_key(players: list[str]) -> str:
    return "|".join(sorted(player_key(player) for player in players))


def parse_pm_tennis_slug(slug: str) -> tuple[str, str] | None:
    match = PM_TENNIS_SLUG_RE.match(slug)
    if not match:
        return None
    return match.group("namespace"), match.group("date")


def parse_ks_tennis_date(event_ticker: str) -> str | None:
    match = KS_TENNIS_DATE_RE.search(event_ticker)
    if not match:
        return None
    return universe_adapters.date_from_yy_mon_day(match.group(1), match.group(2), match.group(3))


def is_full_match_market(question: str, outcomes: list[str]) -> bool:
    lowered = question.lower()
    if any(term in lowered for term in ("set ", "set 1", "set 2", "set 3", "spread", "total", "over", "under")):
        return False
    outcome_set = {outcome.strip().lower() for outcome in outcomes}
    if outcome_set in ({"yes", "no"}, {"over", "under"}):
        return False
    return len(outcomes) == 2


def discover_pm_tennis(limit: int, from_date: str | None) -> list[TennisOutcome]:
    rows: list[TennisOutcome] = []
    for event in core.fetch_polymarket_events(("tennis",), limit):
        slug = str(event.get("slug") or "")
        parsed = parse_pm_tennis_slug(slug)
        if parsed is None:
            continue
        namespace, event_date = parsed
        if from_date and event_date < from_date:
            continue
        title = str(event.get("title") or "")
        for market in event.get("markets") or []:
            if not market.get("active") or market.get("closed") or not market.get("enableOrderBook", True):
                continue
            question = str(market.get("question") or market.get("groupItemTitle") or "")
            outcomes = [str(outcome) for outcome in core.parse_json_list(market.get("outcomes"))]
            token_ids = [str(token_id) for token_id in core.parse_json_list(market.get("clobTokenIds"))]
            if len(outcomes) != len(token_ids) or not is_full_match_market(question, outcomes):
                continue
            key = entity_key(outcomes)
            for outcome, token_id in zip(outcomes, token_ids):
                rows.append(
                    TennisOutcome(
                        event_date=event_date,
                        namespace=namespace,
                        entity_key=key,
                        outcome_key=player_key(outcome),
                        outcome_name=outcome,
                        match_name=title or question,
                        pm_event_slug=slug,
                        pm_market_id=str(market.get("id") or ""),
                        pm_token_id=token_id,
                        pm_question=question,
                    )
                )
    rows.sort(key=lambda row: (row.event_date, row.namespace, row.entity_key, row.outcome_key))
    return rows


def discover_ks_tennis(limit: int) -> list[TennisOutcome]:
    rows: list[TennisOutcome] = []
    for series_ticker in KS_TENNIS_SERIES:
        namespace = KS_SERIES_NAMESPACES[series_ticker]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for market in core.fetch_kalshi_series_markets(series_ticker, limit):
            event_ticker = str(market.get("event_ticker") or "")
            if event_ticker:
                grouped[event_ticker].append(market)
        for event_ticker, markets in grouped.items():
            if len(markets) != 2:
                continue
            event_date = parse_ks_tennis_date(event_ticker)
            if not event_date:
                continue
            outcomes = [str(market.get("yes_sub_title") or "") for market in markets]
            if any(not outcome for outcome in outcomes):
                continue
            key = entity_key(outcomes)
            title = str(markets[0].get("title") or "")
            for market, outcome in zip(markets, outcomes):
                rows.append(
                    TennisOutcome(
                        event_date=event_date,
                        namespace=namespace,
                        entity_key=key,
                        outcome_key=player_key(outcome),
                        outcome_name=outcome,
                        match_name=title,
                        ks_series_ticker=series_ticker,
                        ks_event_ticker=event_ticker,
                        ks_market_ticker=str(market.get("ticker") or ""),
                        ks_title=str(market.get("title") or title),
                    )
                )
    rows.sort(key=lambda row: (row.event_date, row.namespace, row.entity_key, row.outcome_key))
    return rows


def pair_tennis(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    pm_rows = discover_pm_tennis(pm_limit, from_date)
    ks_rows = discover_ks_tennis(ks_limit)
    if from_date:
        ks_rows = [row for row in ks_rows if row.event_date >= from_date]

    pm_counts = Counter((row.event_date, row.namespace, row.entity_key, row.outcome_key) for row in pm_rows)
    ks_counts = Counter((row.event_date, row.namespace, row.entity_key, row.outcome_key) for row in ks_rows)
    pm_index = {(row.event_date, row.namespace, row.entity_key, row.outcome_key): row for row in pm_rows}

    warnings: list[str] = []
    pairs: list[core.PairedContract] = []
    for ks_row in ks_rows:
        key = (ks_row.event_date, ks_row.namespace, ks_row.entity_key, ks_row.outcome_key)
        if ks_counts[key] != 1:
            warnings.append(f"ambiguous KS tennis outcome skipped: {key}")
            continue
        if pm_counts[key] != 1:
            continue
        pm_row = pm_index[key]
        pairs.append(
            core.PairedContract(
                universe="tennis",
                category="tennis",
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


def build_tennis_diagnostics(
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    pairs, warnings = pair_tennis(pm_limit, ks_limit, from_date)
    rows = [sports_pairing.pair_status_row("tennis", pair, "paired_existing", "tennis full-name/date pairer") for pair in pairs]
    if not rows:
        rows = [sports_pairing.empty_status("tennis", "unmatched_semantics", "tennis pairer returned no safe pairs")]
    return rows, warnings


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("tennis"),
    pairer=pair_tennis,
    diagnostics_builder=build_tennis_diagnostics,
)
