#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import market_db
import pipeline_core as core
import sports_taxonomy
import staging_normalize_market_db


PAIR_FIELDS = [
    "universe",
    "category",
    "match_name",
    "event_date",
    "canonical_event_id",
    "market_type",
    "pm_yes_outcome",
    "ks_yes_outcome",
    "pm_event_slug",
    "pm_market_id",
    "pm_token_id",
    "ks_event_ticker",
    "ks_market_ticker",
    "match_format",
    "schedule_source",
    "safe_paired",
]

DIAGNOSTIC_FIELDS = [
    "ts_utc",
    "category_key",
    "status",
    "reason",
    "safe_paired",
    "event_date",
    "canonical_event_id",
    "market_type",
    "match_name",
    "outcome",
    "pm_event_slug",
    "pm_market_id",
    "pm_token_id",
    "pm_question",
    "ks_series_ticker",
    "ks_event_ticker",
    "ks_market_ticker",
    "ks_title",
    "ks_yes_outcome",
    "entity_key",
    "outcome_key",
]

SAFE_MARKET_TYPES = {"game_winner", "match_winner", "fighter_winner"}
SAFE_UNIVERSES = {
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
TENNIS_UNIVERSES = {"atp", "atp_challenger", "wta", "itf_men", "itf_women"}
ESPORTS_ALIAS_UNIVERSES = {"valorant", "cs2", "lol"}
MATCH_SOURCE = "db_semantic_binary_match"
MATCHUP_RE = re.compile(r"\b(?:vs\.?|v\.?|at|@)\b|[A-Za-z0-9]\s*[-\u2013\u2014]\s*[A-Za-z0-9]", re.IGNORECASE)
DISALLOWED_PM_TERMS_RE = re.compile(
    r"\b(spread|handicap|total|o/u|over|under|set\s*\d+|set winner|completed match|"
    r"first inning|extra innings|series|tournament|championship|winner)\b",
    re.IGNORECASE,
)
DRAW_TIE_KEYS = {"draw", "tie"}


@dataclass(frozen=True)
class DbOutcome:
    venue: str
    category_key: str
    universe: str
    competition_gender: str
    event_date: str
    market_type: str
    match_name: str
    outcome_name: str
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
    date_candidates: tuple[str, ...] = ()
    outcome_alias_keys: tuple[str, ...] = ()

    @property
    def event_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.category_key,
            self.universe,
            self.competition_gender,
            self.event_date,
            self.market_type,
            self.entity_key,
        )

    @property
    def semantic_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.category_key,
            self.universe,
            self.competition_gender,
            self.market_type,
            self.entity_key,
        )

    @property
    def canonical_event_id(self) -> str:
        return f"{self.universe}:{self.event_date}:{self.entity_key}"


MLB_TEAM_ALIASES = {
    "Arizona Diamondbacks": ("ari", "az", "arizona", "diamondbacks", "arizona diamondbacks"),
    "Atlanta Braves": ("atl", "atlanta", "braves", "atlanta braves"),
    "Baltimore Orioles": ("bal", "baltimore", "orioles", "baltimore orioles"),
    "Boston Red Sox": ("bos", "boston", "red sox", "boston red sox"),
    "Chicago Cubs": ("chc", "chicago c", "chicago cubs", "cubs"),
    "Chicago White Sox": ("cws", "chicago ws", "chicago white sox", "white sox"),
    "Cincinnati Reds": ("cin", "cincinnati", "reds", "cincinnati reds"),
    "Cleveland Guardians": ("cle", "cleveland", "guardians", "cleveland guardians"),
    "Colorado Rockies": ("col", "colorado", "rockies", "colorado rockies"),
    "Detroit Tigers": ("det", "detroit", "tigers", "detroit tigers"),
    "Houston Astros": ("hou", "houston", "astros", "houston astros"),
    "Kansas City Royals": ("kc", "kansas city", "royals", "kansas city royals"),
    "Los Angeles Angels": ("laa", "los angeles a", "los angeles angels", "angels"),
    "Los Angeles Dodgers": ("lad", "los angeles d", "los angeles dodgers", "dodgers"),
    "Miami Marlins": ("mia", "miami", "marlins", "miami marlins"),
    "Milwaukee Brewers": ("mil", "milwaukee", "brewers", "milwaukee brewers"),
    "Minnesota Twins": ("min", "minnesota", "twins", "minnesota twins"),
    "New York Mets": ("nym", "new york m", "new york mets", "mets"),
    "New York Yankees": ("nyy", "new york y", "new york yankees", "yankees"),
    "Athletics": ("ath", "as", "a's", "athletics", "oakland athletics", "sacramento athletics"),
    "Philadelphia Phillies": ("phi", "philadelphia", "phillies", "philadelphia phillies"),
    "Pittsburgh Pirates": ("pit", "pittsburgh", "pirates", "pittsburgh pirates"),
    "San Diego Padres": ("sd", "san diego", "padres", "san diego padres"),
    "San Francisco Giants": ("sf", "san francisco", "giants", "san francisco giants"),
    "Seattle Mariners": ("sea", "seattle", "mariners", "seattle mariners"),
    "St. Louis Cardinals": ("stl", "st louis", "st. louis", "cardinals", "st louis cardinals", "st. louis cardinals"),
    "Tampa Bay Rays": ("tb", "tampa bay", "rays", "tampa bay rays"),
    "Texas Rangers": ("tex", "texas", "rangers", "texas rangers"),
    "Toronto Blue Jays": ("tor", "toronto", "blue jays", "toronto blue jays"),
    "Washington Nationals": ("wsh", "was", "washington", "nationals", "washington nationals"),
}
MLB_ALIAS_TO_KEY = {
    sports_taxonomy.normalize_key(alias): sports_taxonomy.normalize_key(team)
    for team, aliases in MLB_TEAM_ALIASES.items()
    for alias in (team, *aliases)
}


def include_sport(category_key: str, universe: str, sports: str) -> bool:
    requested = {part.strip().lower().replace("-", "_") for part in sports.split(",") if part.strip()}
    if not requested or "all" in requested:
        return True
    return category_key in requested or universe in requested


def is_draw_or_tie(value: str) -> bool:
    return sports_taxonomy.normalize_key(value) in DRAW_TIE_KEYS


def date_from_text(value: Any) -> str:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(value or ""))
    return match.group(1) if match else ""


def unique_dates(*values: Any) -> tuple[str, ...]:
    dates = []
    seen = set()
    for value in values:
        parsed = date_from_text(value)
        if parsed and parsed not in seen:
            seen.add(parsed)
            dates.append(parsed)
    return tuple(dates)


def date_distance(left: str, right: str) -> int | None:
    if not left or not right:
        return None
    try:
        return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)
    except ValueError:
        return None


def date_window_days(universe: str) -> int:
    # Tennis/esports venue metadata mixes local date, UTC start, and ticker date.
    # Require exact participant semantics and allow a one-day calendar skew only
    # for these event-level match markets.
    if universe in TENNIS_UNIVERSES | {"valorant", "cs2", "lol"}:
        return 1
    return 0


def dates_compatible(pm_row: DbOutcome, ks_row: DbOutcome) -> bool:
    window = date_window_days(pm_row.universe)
    if window == 0:
        return pm_row.event_date == ks_row.event_date
    pm_dates = pm_row.date_candidates or (pm_row.event_date,)
    ks_dates = ks_row.date_candidates or (ks_row.event_date,)
    for pm_date in pm_dates:
        for ks_date in ks_dates:
            distance = date_distance(pm_date, ks_date)
            if distance is not None and distance <= window:
                return True
    return False


def is_safe_taxonomy(classification: sports_taxonomy.TaxonomyResult, sports: str, market_type: str | None = None) -> tuple[bool, str]:
    if not include_sport(classification.category_key, classification.universe, sports):
        return False, "sport filtered out"
    if classification.universe not in SAFE_UNIVERSES:
        return False, f"universe not enabled for this DB-only binary stage: {classification.universe}"
    effective_market_type = market_type or classification.market_type
    if effective_market_type not in SAFE_MARKET_TYPES:
        return False, f"market_type excluded: {effective_market_type}"
    if classification.competition_gender == "unknown":
        return False, "unknown competition_gender inventory only"
    return True, ""


def pm_is_main_binary_market(title: str, question: str, outcomes: list[str], token_ids: list[str]) -> tuple[bool, str]:
    if len(outcomes) != 2 or len(token_ids) != 2:
        return False, "PM market is not exactly binary"
    outcome_keys = {sports_taxonomy.normalize_key(outcome) for outcome in outcomes}
    if outcome_keys in ({"yes", "no"}, {"over", "under"}, {"odd", "even"}) or any(is_draw_or_tie(outcome) for outcome in outcomes):
        return False, "PM market outcome set is not a team/player binary winner"
    compact_title = " ".join(title.split()).lower()
    compact_question = " ".join(question.split()).lower()
    if compact_title != compact_question:
        return False, "PM market question is not the event-level winner market"
    if not MATCHUP_RE.search(question):
        return False, "PM market has no matchup delimiter"
    if DISALLOWED_PM_TERMS_RE.search(question):
        return False, "PM market text indicates prop/spread/total/future"
    if any("/" in outcome for outcome in outcomes) or "doubles" in question.lower():
        return False, "PM doubles/team-tennis market excluded"
    return True, ""


def pm_market_type_override(classification: sports_taxonomy.TaxonomyResult, market_raw: dict[str, Any]) -> str:
    market_type = classification.market_type
    sports_market_type = str(market_raw.get("sportsMarketType") or "").lower()
    group_title = str(market_raw.get("groupItemTitle") or "").lower()
    if sports_market_type != "moneyline":
        return market_type
    if any(term in group_title for term in ("map ", "set ", "spread", "total")):
        return market_type
    if classification.category_key == "esports":
        return "match_winner"
    if classification.category_key == "combat":
        return "fighter_winner"
    if classification.category_key in {"pickleball", "table_tennis", "tennis"}:
        return "match_winner"
    return market_type


def participant_key(category_key: str, universe: str, gender: str, value: str) -> str:
    if is_draw_or_tie(value):
        return ""
    if universe == "mlb":
        team = MLB_ALIAS_TO_KEY.get(sports_taxonomy.normalize_key(value))
        return f"baseball:mlb:men:{team}" if team else ""
    if universe in {"nba", "wnba"}:
        return sports_taxonomy.entity_key(category_key, universe, gender, value)
    if universe in TENNIS_UNIVERSES:
        key = sports_taxonomy.tennis_player_key(value)
        return f"tennis:{universe}:{gender}:{key}" if key else ""
    key = sports_taxonomy.normalize_key(value)
    return f"{category_key}:{universe}:{gender}:{key}" if key else ""


ESPORTS_STRIP_WORDS = {"esport", "esports"}


def esports_team_alias_keys(value: str) -> set[str]:
    folded = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    tokens = [token for token in folded.split() if token]
    base = sports_taxonomy.normalize_key(value)
    aliases = {base} if base else set()
    if tokens:
        without_esports = [token for token in tokens if token not in ESPORTS_STRIP_WORDS]
        if without_esports:
            aliases.add("".join(without_esports))
            aliases.update(without_esports)
        if tokens[0] in ESPORTS_STRIP_WORDS and len(tokens) > 1:
            aliases.add("".join(tokens[1:]))
        if tokens[-1] in ESPORTS_STRIP_WORDS and len(tokens) > 1:
            aliases.add("".join(tokens[:-1]))
    return {alias for alias in aliases if alias}


def participant_alias_keys(category_key: str, universe: str, gender: str, value: str) -> tuple[str, ...]:
    if universe in TENNIS_UNIVERSES:
        prefix = f"tennis:{universe}:{gender}:"
        return tuple(sorted(prefix + alias for alias in sports_taxonomy.tennis_player_alias_keys(value)))
    if universe in ESPORTS_ALIAS_UNIVERSES:
        prefix = f"esports:{universe}:{gender}:"
        return tuple(sorted(prefix + alias for alias in esports_team_alias_keys(value)))
    key = participant_key(category_key, universe, gender, value)
    return (key,) if key else ()


def entity_key_for(participants: list[str]) -> str:
    cleaned = [participant for participant in participants if participant]
    if len(cleaned) != 2 or len(set(cleaned)) != 2:
        return ""
    return "|".join(sorted(cleaned))


def build_pm_outcomes(conn, sports: str, from_date: str | None) -> tuple[list[DbOutcome], Counter[str]]:
    rows: list[DbOutcome] = []
    skipped: Counter[str] = Counter()
    for row in conn.execute(
        """
        SELECT
            event.event_slug,
            event.title AS event_title,
            event.start_date,
            event.end_date,
            event.raw_json AS event_raw_json,
            market.market_id,
            market.question,
            market.outcomes_json,
            market.clob_token_ids_json,
            market.raw_json AS market_raw_json
        FROM pm_events event
        JOIN pm_markets market ON market.event_slug = event.event_slug
        WHERE event.active = 1
          AND event.closed = 0
          AND market.active = 1
          AND market.closed = 0
          AND market.enable_order_book = 1
        ORDER BY event.event_slug, market.market_id
        """
    ):
        event_slug = str(row["event_slug"] or "")
        title = str(row["event_title"] or "")
        question = str(row["question"] or "")
        outcomes = [str(outcome) for outcome in market_db.parse_json_list(row["outcomes_json"])]
        token_ids = [str(token_id) for token_id in market_db.parse_json_list(row["clob_token_ids_json"])]
        try:
            market_raw = market_db.json.loads(str(row["market_raw_json"] or "{}"))
        except ValueError:
            market_raw = {}
        event_raw = staging_normalize_market_db.parse_json_object(row["event_raw_json"])
        ok, reason = pm_is_main_binary_market(title, question, outcomes, token_ids)
        if not ok:
            skipped[reason] += 1
            continue
        sources = staging_normalize_market_db.pm_sources(conn, event_slug)
        classification_context = staging_normalize_market_db.pm_classification_context(
            question,
            market_raw if isinstance(market_raw, dict) else {},
            event_raw,
            event_slug,
        )
        classification = sports_taxonomy.classify_pm_event(event_slug, title, sources, classification_context)
        market_type = pm_market_type_override(classification, market_raw if isinstance(market_raw, dict) else {})
        ok, reason = is_safe_taxonomy(classification, sports, market_type)
        if not ok:
            skipped[reason] += 1
            continue
        if classification.universe in TENNIS_UNIVERSES:
            event_date_candidates = unique_dates(
                event_slug,
                title,
                market_raw.get("eventStartTime") if isinstance(market_raw, dict) else "",
                market_raw.get("gameStartTime") if isinstance(market_raw, dict) else "",
                row["start_date"],
                row["end_date"],
            )
        else:
            event_date_candidates = unique_dates(
                market_raw.get("eventStartTime") if isinstance(market_raw, dict) else "",
                market_raw.get("gameStartTime") if isinstance(market_raw, dict) else "",
                event_slug,
                row["start_date"],
                row["end_date"],
                title,
            )
        slug_event_date = sports_taxonomy.event_date_from_pm(event_slug, str(row["start_date"] or ""), title)
        if classification.universe in TENNIS_UNIVERSES:
            event_date = date_from_text(event_slug) or slug_event_date or (event_date_candidates[0] if event_date_candidates else "")
        elif classification.category_key in {"basketball", "baseball", "football", "hockey"}:
            event_date = slug_event_date
        else:
            event_date = event_date_candidates[0] if event_date_candidates else slug_event_date
        if not event_date:
            skipped["PM event date missing"] += 1
            continue
        if from_date and event_date < from_date:
            skipped["PM event before from_date"] += 1
            continue
        participant_keys = [
            participant_key(classification.category_key, classification.universe, classification.competition_gender, outcome)
            for outcome in outcomes
        ]
        participant_aliases = [
            participant_alias_keys(classification.category_key, classification.universe, classification.competition_gender, outcome)
            for outcome in outcomes
        ]
        entity_key = entity_key_for(participant_keys)
        if not entity_key:
            skipped["PM participants not safely mappable"] += 1
            continue
        for outcome, token_id, outcome_key, outcome_aliases in zip(outcomes, token_ids, participant_keys, participant_aliases):
            rows.append(
                DbOutcome(
                    venue="pm",
                    category_key=classification.category_key,
                    universe=classification.universe,
                    competition_gender=classification.competition_gender,
                    event_date=event_date,
                    market_type=market_type,
                    match_name=title,
                    outcome_name=outcome,
                    entity_key=entity_key,
                    outcome_key=outcome_key,
                    pm_event_slug=event_slug,
                    pm_market_id=str(row["market_id"] or ""),
                    pm_token_id=token_id,
                    pm_question=question,
                    date_candidates=event_date_candidates or (event_date,),
                    outcome_alias_keys=outcome_aliases,
                )
            )
    return rows, skipped


def build_ks_outcomes(conn, sports: str, from_date: str | None) -> tuple[list[DbOutcome], Counter[str]]:
    rows: list[DbOutcome] = []
    skipped: Counter[str] = Counter()
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT
            market_ticker,
            event_ticker,
            series_ticker,
            title,
            yes_sub_title,
            close_time,
            status,
            raw_json
        FROM ks_markets
        WHERE LOWER(COALESCE(status, 'open')) IN ('', 'open', 'active')
        ORDER BY series_ticker, event_ticker, market_ticker
        """
    ):
        grouped[str(row["event_ticker"] or "")].append(row)

    for event_ticker, event_rows in grouped.items():
        if not event_ticker:
            skipped["KS event_ticker missing"] += 1
            continue
        if len(event_rows) != 2:
            skipped["KS event is not exactly binary"] += 1
            continue
        classifications = [
            sports_taxonomy.classify_ks_market(
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
            skipped["KS event taxonomy mismatch"] += 1
            continue
        ok, reason = is_safe_taxonomy(first, sports)
        if not ok:
            skipped[reason] += 1
            continue
        outcomes = [str(row["yes_sub_title"] or "") for row in event_rows]
        if any(not outcome for outcome in outcomes) or any(is_draw_or_tie(outcome) for outcome in outcomes):
            skipped["KS event has missing/draw/tie outcome"] += 1
            continue
        event_date = sports_taxonomy.event_date_from_ks(
            event_ticker,
            str(event_rows[0]["close_time"] or ""),
            str(event_rows[0]["title"] or ""),
        )
        raw_payloads = []
        for row in event_rows:
            try:
                payload = market_db.json.loads(str(row["raw_json"] or "{}"))
            except ValueError:
                payload = {}
            raw_payloads.append(payload if isinstance(payload, dict) else {})
        event_date_candidates = unique_dates(
            event_ticker,
            event_rows[0]["close_time"],
            *(payload.get("occurrence_datetime") or payload.get("expected_expiration_time") or "" for payload in raw_payloads),
        )
        if not event_date:
            skipped["KS event date missing"] += 1
            continue
        if from_date and event_date < from_date:
            skipped["KS event before from_date"] += 1
            continue
        participant_keys = [
            participant_key(first.category_key, first.universe, first.competition_gender, outcome)
            for outcome in outcomes
        ]
        participant_aliases = [
            participant_alias_keys(first.category_key, first.universe, first.competition_gender, outcome)
            for outcome in outcomes
        ]
        entity_key = entity_key_for(participant_keys)
        if not entity_key:
            skipped["KS participants not safely mappable"] += 1
            continue
        for row, outcome, outcome_key, outcome_aliases in zip(event_rows, outcomes, participant_keys, participant_aliases):
            rows.append(
                DbOutcome(
                    venue="ks",
                    category_key=first.category_key,
                    universe=first.universe,
                    competition_gender=first.competition_gender,
                    event_date=event_date,
                    market_type=first.market_type,
                    match_name=str(row["title"] or ""),
                    outcome_name=outcome,
                    entity_key=entity_key,
                    outcome_key=outcome_key,
                    ks_series_ticker=str(row["series_ticker"] or ""),
                    ks_event_ticker=event_ticker,
                    ks_market_ticker=str(row["market_ticker"] or ""),
                    ks_title=str(row["title"] or ""),
                    date_candidates=event_date_candidates or (event_date,),
                    outcome_alias_keys=outcome_aliases,
                )
            )
    return rows, skipped


def diagnostic_row(status: str, reason: str, row: DbOutcome | None, safe_paired: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ts_utc": now,
        "category_key": row.category_key if row else "",
        "status": status,
        "reason": reason,
        "safe_paired": str(safe_paired).lower(),
        "event_date": row.event_date if row else "",
        "canonical_event_id": row.canonical_event_id if row else "",
        "market_type": row.market_type if row else "",
        "match_name": row.match_name if row else "",
        "outcome": row.outcome_name if row else "",
        "pm_event_slug": row.pm_event_slug if row else "",
        "pm_market_id": row.pm_market_id if row else "",
        "pm_token_id": row.pm_token_id if row else "",
        "pm_question": row.pm_question if row else "",
        "ks_series_ticker": row.ks_series_ticker if row else "",
        "ks_event_ticker": row.ks_event_ticker if row else "",
        "ks_market_ticker": row.ks_market_ticker if row else "",
        "ks_title": row.ks_title if row else "",
        "ks_yes_outcome": row.outcome_name if row and row.venue == "ks" else "",
        "entity_key": row.entity_key if row else "",
        "outcome_key": row.outcome_key if row else "",
    }


def pair_diagnostic_row(pair: core.PairedContract) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ts_utc": now,
        "category_key": pair.universe,
        "status": "paired",
        "reason": MATCH_SOURCE,
        "safe_paired": "true",
        "event_date": pair.event_date,
        "canonical_event_id": pair.canonical_event_id,
        "market_type": pair.market_type,
        "match_name": pair.match_name,
        "outcome": pair.pm_yes_outcome,
        "pm_event_slug": pair.pm_event_slug,
        "pm_market_id": pair.pm_market_id,
        "pm_token_id": pair.pm_token_id,
        "pm_question": "",
        "ks_series_ticker": pair.ks_event_ticker.split("-", 1)[0] if pair.ks_event_ticker else "",
        "ks_event_ticker": pair.ks_event_ticker,
        "ks_market_ticker": pair.ks_market_ticker,
        "ks_title": "",
        "ks_yes_outcome": pair.ks_yes_outcome,
        "entity_key": pair.canonical_event_id,
        "outcome_key": "",
    }


def ambiguous_event_keys(rows: list[DbOutcome]) -> set[tuple[str, str, str, str, str, str]]:
    grouped: dict[tuple[str, str, str, str, str, str], list[DbOutcome]] = defaultdict(list)
    for row in rows:
        grouped[row.event_key].append(row)
    ambiguous: set[tuple[str, str, str, str, str, str]] = set()
    for key, key_rows in grouped.items():
        if len(key_rows) != 2 or len({row.outcome_key for row in key_rows}) != 2:
            ambiguous.add(key)
            continue
        pm_markets = {row.pm_market_id for row in key_rows if row.pm_market_id}
        ks_events = {row.ks_event_ticker for row in key_rows if row.ks_event_ticker}
        if len(pm_markets) > 1 or len(ks_events) > 1:
            ambiguous.add(key)
    return ambiguous


def ambiguous_semantic_keys(rows: list[DbOutcome]) -> set[tuple[str, str, str, str, str]]:
    grouped: dict[tuple[str, str, str, str, str], list[DbOutcome]] = defaultdict(list)
    for row in rows:
        grouped[row.semantic_key].append(row)
    ambiguous: set[tuple[str, str, str, str, str]] = set()
    for key, key_rows in grouped.items():
        event_dates = {row.event_date for row in key_rows}
        outcome_keys = {row.outcome_key for row in key_rows}
        if len(event_dates) > 1:
            # Same participants can play multiple dates. Date compatibility below
            # can still disambiguate, so only mark ambiguous if an individual date
            # bucket is malformed.
            by_date: dict[str, list[DbOutcome]] = defaultdict(list)
            for row in key_rows:
                by_date[row.event_date].append(row)
            if any(len(rows_for_date) != 2 or len({row.outcome_key for row in rows_for_date}) != 2 for rows_for_date in by_date.values()):
                ambiguous.add(key)
            continue
        if len(key_rows) != 2 or len(outcome_keys) != 2:
            ambiguous.add(key)
    return ambiguous


def is_tennis_row(row: DbOutcome) -> bool:
    return row.universe in TENNIS_UNIVERSES


def is_esports_alias_row(row: DbOutcome) -> bool:
    return row.universe in ESPORTS_ALIAS_UNIVERSES


def tennis_event_group_id(row: DbOutcome) -> str:
    return row.pm_market_id if row.venue == "pm" else row.ks_event_ticker


def tennis_event_scope(row: DbOutcome) -> tuple[str, str, str, str]:
    return (row.universe, row.competition_gender, row.market_type, row.category_key)


def alias_event_group_id(row: DbOutcome) -> str:
    return row.pm_market_id if row.venue == "pm" else row.ks_event_ticker


def alias_event_scope(row: DbOutcome) -> tuple[str, str, str, str]:
    return (row.category_key, row.universe, row.competition_gender, row.market_type)


def event_is_binary(rows: list[DbOutcome]) -> bool:
    return len(rows) == 2 and len({row.outcome_key for row in rows}) == 2


def alias_overlap(left: DbOutcome, right: DbOutcome) -> bool:
    return bool(set(left.outcome_alias_keys or (left.outcome_key,)) & set(right.outcome_alias_keys or (right.outcome_key,)))


def alias_suffixes(row: DbOutcome) -> set[str]:
    prefix = f"{row.category_key}:{row.universe}:{row.competition_gender}:"
    values = row.outcome_alias_keys or (row.outcome_key,)
    suffixes = set()
    for value in values:
        text = str(value or "")
        suffixes.add(text.removeprefix(prefix))
    return {suffix for suffix in suffixes if suffix}


def one_edit_apart(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 5 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


def esports_alias_overlap(left: DbOutcome, right: DbOutcome) -> bool:
    left_aliases = alias_suffixes(left)
    right_aliases = alias_suffixes(right)
    if left_aliases & right_aliases:
        return True
    for left_alias in left_aliases:
        for right_alias in right_aliases:
            if one_edit_apart(left_alias, right_alias):
                return True
            if min(len(left_alias), len(right_alias)) >= 8 and SequenceMatcher(None, left_alias, right_alias).ratio() >= 0.94:
                return True
    return False


def tennis_alias_mapping(pm_rows: list[DbOutcome], ks_rows: list[DbOutcome]) -> tuple[dict[DbOutcome, DbOutcome] | None, str]:
    if not event_is_binary(pm_rows) or not event_is_binary(ks_rows):
        return None, "tennis event is not a clean two-outcome binary"

    candidates: dict[DbOutcome, list[DbOutcome]] = {}
    for ks_row in ks_rows:
        matches = [pm_row for pm_row in pm_rows if alias_overlap(pm_row, ks_row)]
        candidates[ks_row] = matches
        if len(matches) != 1:
            return None, "tennis alias outcome direction is ambiguous" if matches else "tennis alias outcome direction missing"

    mapping = {ks_row: matches[0] for ks_row, matches in candidates.items()}
    if len(set(mapping.values())) != len(mapping):
        return None, "tennis alias outcome direction is not one-to-one"
    return mapping, ""


def esports_alias_mapping(pm_rows: list[DbOutcome], ks_rows: list[DbOutcome]) -> tuple[dict[DbOutcome, DbOutcome] | None, str]:
    if not event_is_binary(pm_rows) or not event_is_binary(ks_rows):
        return None, "esports event is not a clean two-outcome binary"

    candidates: dict[DbOutcome, list[DbOutcome]] = {}
    for ks_row in ks_rows:
        matches = [pm_row for pm_row in pm_rows if esports_alias_overlap(pm_row, ks_row)]
        candidates[ks_row] = matches
        if len(matches) != 1:
            return None, "esports alias outcome direction is ambiguous" if matches else "esports alias outcome direction missing"

    mapping = {ks_row: matches[0] for ks_row, matches in candidates.items()}
    if len(set(mapping.values())) != len(mapping):
        return None, "esports alias outcome direction is not one-to-one"
    return mapping, ""


def paired_contract_from_rows(pm_row: DbOutcome, ks_row: DbOutcome) -> core.PairedContract:
    return core.PairedContract(
        universe=pm_row.universe,
        category="sports",
        match_name=pm_row.match_name or ks_row.match_name,
        event_date=pm_row.event_date,
        canonical_event_id=pm_row.canonical_event_id,
        market_type=pm_row.market_type,
        pm_yes_outcome=pm_row.outcome_name,
        pm_event_slug=pm_row.pm_event_slug,
        pm_market_id=pm_row.pm_market_id,
        pm_token_id=pm_row.pm_token_id,
        ks_yes_outcome=ks_row.outcome_name,
        ks_event_ticker=ks_row.ks_event_ticker,
        ks_market_ticker=ks_row.ks_market_ticker,
        schedule_source=MATCH_SOURCE,
    )


def safe_pair_tennis_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    pairs: list[core.PairedContract] = []

    pm_groups: dict[str, list[DbOutcome]] = defaultdict(list)
    ks_groups: dict[str, list[DbOutcome]] = defaultdict(list)
    for row in pm_rows:
        pm_groups[tennis_event_group_id(row)].append(row)
    for row in ks_rows:
        ks_groups[tennis_event_group_id(row)].append(row)

    clean_pm_groups = {key: rows for key, rows in pm_groups.items() if event_is_binary(rows)}
    clean_ks_groups = {key: rows for key, rows in ks_groups.items() if event_is_binary(rows)}
    for key, rows in pm_groups.items():
        if key and key not in clean_pm_groups:
            message = f"ambiguous PM tennis binary event skipped: {key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, rows[0]))
    for key, rows in ks_groups.items():
        if key and key not in clean_ks_groups:
            message = f"ambiguous KS tennis binary event skipped: {key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, rows[0]))

    matched_pm_groups: set[str] = set()
    matched_ks_groups: set[str] = set()

    for ks_key, event_ks_rows in sorted(clean_ks_groups.items(), key=lambda item: item[0]):
        representative = event_ks_rows[0]
        candidates = []
        mismatch_reasons: Counter[str] = Counter()
        for pm_key, event_pm_rows in clean_pm_groups.items():
            pm_ref = event_pm_rows[0]
            if tennis_event_scope(pm_ref) != tennis_event_scope(representative):
                continue
            if not dates_compatible(pm_ref, representative):
                continue
            mapping, reason = tennis_alias_mapping(event_pm_rows, event_ks_rows)
            if mapping is None:
                mismatch_reasons[reason] += 1
                continue
            candidates.append((pm_key, event_pm_rows, mapping))

        if not candidates:
            reason = "no PM event with same tennis scope/date/alias matchup"
            if mismatch_reasons:
                reason = f"likely_pair_after_alias rejected: {mismatch_reasons.most_common(1)[0][0]}"
            diagnostics.append(diagnostic_row("unmatched_semantics", reason, representative))
            continue
        if len(candidates) != 1:
            message = f"ambiguous tennis alias match skipped: {representative.semantic_key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, representative))
            continue

        pm_key, _event_pm_rows, mapping = candidates[0]
        for ks_row in sorted(event_ks_rows, key=lambda row: row.outcome_key):
            pm_row = mapping[ks_row]
            pair = paired_contract_from_rows(pm_row, ks_row)
            pairs.append(pair)
            diagnostics.append(pair_diagnostic_row(pair))
        matched_pm_groups.add(pm_key)
        matched_ks_groups.add(ks_key)

    for pm_key, event_pm_rows in sorted(clean_pm_groups.items(), key=lambda item: item[0]):
        if pm_key in matched_pm_groups:
            continue
        representative = event_pm_rows[0]
        if not any(
            tennis_event_scope(representative) == tennis_event_scope(ks_rows_for_event[0])
            and dates_compatible(representative, ks_rows_for_event[0])
            and tennis_alias_mapping(event_pm_rows, ks_rows_for_event)[0] is not None
            for ks_key, ks_rows_for_event in clean_ks_groups.items()
            if ks_key not in matched_ks_groups
        ):
            diagnostics.append(diagnostic_row("pm_only_inventory", "no KS event with same tennis scope/date/alias matchup", representative))

    return pairs, diagnostics, warnings


def safe_pair_esports_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    pairs: list[core.PairedContract] = []

    pm_groups: dict[str, list[DbOutcome]] = defaultdict(list)
    ks_groups: dict[str, list[DbOutcome]] = defaultdict(list)
    for row in pm_rows:
        pm_groups[alias_event_group_id(row)].append(row)
    for row in ks_rows:
        ks_groups[alias_event_group_id(row)].append(row)

    clean_pm_groups = {key: rows for key, rows in pm_groups.items() if event_is_binary(rows)}
    clean_ks_groups = {key: rows for key, rows in ks_groups.items() if event_is_binary(rows)}
    for key, rows in pm_groups.items():
        if key and key not in clean_pm_groups:
            message = f"ambiguous PM esports binary event skipped: {key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, rows[0]))
    for key, rows in ks_groups.items():
        if key and key not in clean_ks_groups:
            message = f"ambiguous KS esports binary event skipped: {key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, rows[0]))

    matched_pm_groups: set[str] = set()
    matched_ks_groups: set[str] = set()

    for ks_key, event_ks_rows in sorted(clean_ks_groups.items(), key=lambda item: item[0]):
        representative = event_ks_rows[0]
        candidates = []
        mismatch_reasons: Counter[str] = Counter()
        for pm_key, event_pm_rows in clean_pm_groups.items():
            pm_ref = event_pm_rows[0]
            if alias_event_scope(pm_ref) != alias_event_scope(representative):
                continue
            if not dates_compatible(pm_ref, representative):
                continue
            mapping, reason = esports_alias_mapping(event_pm_rows, event_ks_rows)
            if mapping is None:
                mismatch_reasons[reason] += 1
                continue
            candidates.append((pm_key, event_pm_rows, mapping))

        if not candidates:
            reason = "no PM event with same esports scope/date/alias matchup"
            if mismatch_reasons:
                reason = f"likely_pair_after_alias rejected: {mismatch_reasons.most_common(1)[0][0]}"
            diagnostics.append(diagnostic_row("unmatched_semantics", reason, representative))
            continue
        if len(candidates) != 1:
            message = f"ambiguous esports alias match skipped: {representative.semantic_key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, representative))
            continue

        pm_key, _event_pm_rows, mapping = candidates[0]
        for ks_row in sorted(event_ks_rows, key=lambda row: row.outcome_key):
            pm_row = mapping[ks_row]
            pair = paired_contract_from_rows(pm_row, ks_row)
            pairs.append(pair)
            diagnostics.append(pair_diagnostic_row(pair))
        matched_pm_groups.add(pm_key)
        matched_ks_groups.add(ks_key)

    for pm_key, event_pm_rows in sorted(clean_pm_groups.items(), key=lambda item: item[0]):
        if pm_key in matched_pm_groups:
            continue
        representative = event_pm_rows[0]
        if not any(
            alias_event_scope(representative) == alias_event_scope(ks_rows_for_event[0])
            and dates_compatible(representative, ks_rows_for_event[0])
            and esports_alias_mapping(event_pm_rows, ks_rows_for_event)[0] is not None
            for ks_key, ks_rows_for_event in clean_ks_groups.items()
            if ks_key not in matched_ks_groups
        ):
            diagnostics.append(diagnostic_row("pm_only_inventory", "no KS event with same esports scope/date/alias matchup", representative))

    return pairs, diagnostics, warnings


def safe_pair_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    pairs: list[core.PairedContract] = []
    tennis_pairs, tennis_diagnostics, tennis_warnings = safe_pair_tennis_rows(
        [row for row in pm_rows if is_tennis_row(row)],
        [row for row in ks_rows if is_tennis_row(row)],
    )
    pairs.extend(tennis_pairs)
    diagnostics.extend(tennis_diagnostics)
    warnings.extend(tennis_warnings)
    esports_pairs, esports_diagnostics, esports_warnings = safe_pair_esports_rows(
        [row for row in pm_rows if is_esports_alias_row(row)],
        [row for row in ks_rows if is_esports_alias_row(row)],
    )
    pairs.extend(esports_pairs)
    diagnostics.extend(esports_diagnostics)
    warnings.extend(esports_warnings)
    pm_rows = [row for row in pm_rows if not is_tennis_row(row) and not is_esports_alias_row(row)]
    ks_rows = [row for row in ks_rows if not is_tennis_row(row) and not is_esports_alias_row(row)]
    pm_ambiguous = ambiguous_event_keys(pm_rows)
    ks_ambiguous = ambiguous_event_keys(ks_rows)
    pm_semantic_ambiguous = ambiguous_semantic_keys(pm_rows)
    pm_by_semantic: dict[tuple[str, str, str, str, str], list[DbOutcome]] = defaultdict(list)
    ks_by_event: dict[tuple[str, str, str, str, str, str], list[DbOutcome]] = defaultdict(list)
    for row in pm_rows:
        pm_by_semantic[row.semantic_key].append(row)
    for row in ks_rows:
        ks_by_event[row.event_key].append(row)

    matched_pm_keys: set[tuple[tuple[str, str, str, str, str, str], str]] = set()

    for event_key, event_ks_rows in sorted(ks_by_event.items(), key=lambda item: item[0]):
        representative = event_ks_rows[0]
        semantic_key = representative.semantic_key
        if event_key in ks_ambiguous:
            message = f"ambiguous KS binary event skipped: {event_key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, representative))
            continue
        event_pm_rows = [
            row
            for row in pm_by_semantic.get(semantic_key, [])
            if row.event_key not in pm_ambiguous and dates_compatible(row, representative)
        ]
        if semantic_key in pm_semantic_ambiguous:
            message = f"ambiguous PM binary event skipped: {semantic_key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, representative))
            continue
        if not event_pm_rows:
            diagnostics.append(diagnostic_row("unmatched_semantics", "no PM event with same universe/gender/date/matchup", representative))
            continue
        if len(event_pm_rows) != 2 or len(event_ks_rows) != 2:
            message = f"ambiguous date-compatible binary event skipped: {semantic_key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, representative))
            continue
        pm_outcome_keys = {row.outcome_key for row in event_pm_rows}
        ks_outcome_keys = {row.outcome_key for row in event_ks_rows}
        if pm_outcome_keys != ks_outcome_keys:
            diagnostics.append(diagnostic_row("unmatched_semantics", "PM/KS outcome sets differ", representative))
            continue
        for ks_row in sorted(event_ks_rows, key=lambda row: row.outcome_key):
            pm_matches = [row for row in event_pm_rows if row.outcome_key == ks_row.outcome_key]
            pm_row = pm_matches[0] if len(pm_matches) == 1 else None
            if pm_row is None:
                diagnostics.append(diagnostic_row("unmatched_semantics", "no PM outcome with same direction", ks_row))
                continue
            matched_pm_keys.add((event_key, pm_row.outcome_key))
            pair = paired_contract_from_rows(pm_row, ks_row)
            pairs.append(pair)
            diagnostics.append(pair_diagnostic_row(pair))

    for row in pm_rows:
        if row.event_key in pm_ambiguous or (row.event_key, row.outcome_key) in matched_pm_keys:
            continue
        if not any(dates_compatible(row, ks_row) for rows_for_event in ks_by_event.values() for ks_row in rows_for_event if ks_row.semantic_key == row.semantic_key):
            diagnostics.append(diagnostic_row("pm_only_inventory", "no KS event with same universe/gender/date/matchup", row))

    pairs.sort(key=lambda pair: (pair.universe, pair.event_date, pair.canonical_event_id, pair.pm_yes_outcome))
    return pairs, diagnostics, warnings


def build_pairs_from_db(
    conn,
    *,
    sports: str = "all",
    from_date: str | None = None,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str], dict[str, int]]:
    market_db.reset_paired_contracts(conn)
    conn.execute("DELETE FROM data_quality_warnings WHERE stage = 'pair_from_db'")
    normalized_counts = staging_normalize_market_db.normalize_db(conn, sports)
    pm_rows, pm_skipped = build_pm_outcomes(conn, sports, from_date)
    ks_rows, ks_skipped = build_ks_outcomes(conn, sports, from_date)
    pairs, diagnostics, warnings = safe_pair_rows(pm_rows, ks_rows)

    for pair in pairs:
        market_db.upsert_paired_contract(conn, pair)
    for label, skipped in (("pm", pm_skipped), ("ks", ks_skipped)):
        for reason, count in skipped.items():
            if count:
                market_db.record_warning(
                    conn,
                    "pair_from_db",
                    f"{label.upper()} rows skipped during DB-only safe pairing: {reason}: {count}",
                    context={"venue": label, "reason": reason, "count": count},
                )
    for warning in warnings:
        market_db.record_warning(conn, "pair_from_db", warning)
    conn.commit()
    counts = {
        **normalized_counts,
        "pm_safe_candidates": len(pm_rows),
        "ks_safe_candidates": len(ks_rows),
        "safe_pairs": len(pairs),
    }
    return pairs, diagnostics, warnings, counts


def build_pairs_csv_only(
    conn,
    *,
    sports: str = "all",
    from_date: str | None = None,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str], dict[str, int]]:
    pm_rows, pm_skipped = build_pm_outcomes(conn, sports, from_date)
    ks_rows, ks_skipped = build_ks_outcomes(conn, sports, from_date)
    pairs, diagnostics, warnings = safe_pair_rows(pm_rows, ks_rows)
    counts = {
        "pm_safe_candidates": len(pm_rows),
        "ks_safe_candidates": len(ks_rows),
        "safe_pairs": len(pairs),
    }
    for label, skipped in (("pm", pm_skipped), ("ks", ks_skipped)):
        for reason, count in skipped.items():
            if count:
                warnings.append(f"{label.upper()} rows skipped during DB-only safe pairing: {reason}: {count}")
    return pairs, diagnostics, warnings, counts


def pair_to_output_row(pair: core.PairedContract) -> dict[str, Any]:
    row = market_db.pair_to_dict(pair)
    row["safe_paired"] = "true"
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Build conservative safe PM/KS binary pairs from the local SQLite DB only.")
    parser.add_argument("--db", default=str(market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="all")
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--all-dates", action="store_true")
    parser.add_argument("--pairs-output", default="data/staging/latest_paired_contracts.csv")
    parser.add_argument("--diagnostics-output", default="data/staging/latest_pairing_diagnostics.csv")
    parser.add_argument("--csv-only", action="store_true", help="Generate CSV outputs without writing paired_contracts/edge tables.")
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()
    if args.all_dates:
        args.from_date = None

    with market_db.connect(Path(args.db)) as conn:
        market_db.init_db(conn)
        if args.csv_only:
            pairs, diagnostics, warnings, build_counts = build_pairs_csv_only(
                conn,
                sports=args.sports,
                from_date=args.from_date,
            )
        else:
            pairs, diagnostics, warnings, build_counts = build_pairs_from_db(
                conn,
                sports=args.sports,
                from_date=args.from_date,
            )
        core.write_latest_csv([pair_to_output_row(pair) for pair in pairs], PAIR_FIELDS, Path(args.pairs_output))
        core.write_latest_csv(diagnostics, DIAGNOSTIC_FIELDS, Path(args.diagnostics_output))
        counts = market_db.row_counts(conn)

    if args.show_warnings:
        for warning in warnings:
            print(f"DB pair warning: {warning}", file=sys.stderr)
    print(
        "staging DB pair complete: "
        f"pm_safe_candidates={build_counts['pm_safe_candidates']}; "
        f"ks_safe_candidates={build_counts['ks_safe_candidates']}; "
        f"safe_pairs={build_counts['safe_pairs']}; "
        f"pairs_output={args.pairs_output}; diagnostics_output={args.diagnostics_output}"
    )
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
