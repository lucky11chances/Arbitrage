#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import old_market_db
import old_pipeline_core as core
import old_sports_taxonomy
import old_staging_normalize_market_db


# Output schemas
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
    "universe",
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
    "pm_event_name",
    "ks_event_name",
    "pm_outcomes",
    "ks_outcomes",
    "pm_start_time_utc",
    "ks_start_time_utc",
    "candidate_side",
    "candidate_confidence",
    "why_candidate",
    "why_not_safe",
]

# Pairing policy
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
START_TIME_TOLERANCE_SECONDS = 5 * 60
CANDIDATE_START_TIME_TOLERANCE_SECONDS = 12 * 60 * 60
KALSHI_TICKER_TZ = ZoneInfo("America/New_York")
KALSHI_TICKER_START_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})", re.IGNORECASE)
MATCHUP_RE = re.compile(r"\b(?:vs\.?|v\.?|at|@)\b|[A-Za-z0-9]\s*[-\u2013\u2014]\s*[A-Za-z0-9]", re.IGNORECASE)
DISALLOWED_PM_TERMS_RE = re.compile(
    r"\b(spread|handicap|total|o/u|over|under|set\s*\d+|set winner|completed match|"
    r"first inning|extra innings|series|tournament|championship|winner)\b",
    re.IGNORECASE,
)
DRAW_TIE_KEYS = {"draw", "tie"}


# Normalized row model
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
    start_time_utc: str = ""
    start_time_source: str = ""

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


@dataclass(frozen=True)
class AliasPairingSpec:
    name: str
    reason_code: str
    missing_pm_reason: str
    missing_ks_reason: str
    ambiguous_pm_message: str
    ambiguous_ks_message: str
    candidate_label: str


TENNIS_ALIAS_SPEC = AliasPairingSpec(
    name="tennis",
    reason_code="candidate_tennis_alias_review",
    missing_pm_reason="no PM event with same tennis scope/start/player candidate",
    missing_ks_reason="no KS event with same tennis scope/start/player candidate",
    ambiguous_pm_message="ambiguous PM tennis binary event skipped",
    ambiguous_ks_message="ambiguous KS tennis binary event skipped",
    candidate_label="player",
)

ESPORTS_ALIAS_SPEC = AliasPairingSpec(
    name="esports",
    reason_code="candidate_esports_alias_review",
    missing_pm_reason="no PM event with same esports scope/start/team candidate",
    missing_ks_reason="no KS event with same esports scope/start/team candidate",
    ambiguous_pm_message="ambiguous PM esports binary event skipped",
    ambiguous_ks_message="ambiguous KS esports binary event skipped",
    candidate_label="team",
)


# League/team alias maps
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
    old_sports_taxonomy.normalize_key(alias): old_sports_taxonomy.normalize_key(team)
    for team, aliases in MLB_TEAM_ALIASES.items()
    for alias in (team, *aliases)
}


def include_sport(category_key: str, universe: str, sports: str) -> bool:
    requested = {part.strip().lower().replace("-", "_") for part in sports.split(",") if part.strip()}
    if not requested or "all" in requested:
        return True
    return category_key in requested or universe in requested


def is_draw_or_tie(value: str) -> bool:
    return old_sports_taxonomy.normalize_key(value) in DRAW_TIE_KEYS


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


def parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_minute_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()


def first_utc_minute(*values: Any) -> tuple[str, str]:
    for label, value in values:
        parsed = parse_utc_datetime(value)
        if parsed is not None:
            return utc_minute_text(parsed), str(label)
    return "", ""


def pm_start_time_utc(market_raw: dict[str, Any], event_raw: dict[str, Any]) -> tuple[str, str]:
    return first_utc_minute(
        ("pm_market_gameStartTime", market_raw.get("gameStartTime")),
        ("pm_market_eventStartTime", market_raw.get("eventStartTime")),
        ("pm_event_gameStartTime", event_raw.get("gameStartTime")),
        ("pm_event_eventStartTime", event_raw.get("eventStartTime")),
    )


def ks_ticker_start_time_utc(event_ticker: str) -> str:
    match = KALSHI_TICKER_START_RE.search(event_ticker.upper())
    if not match:
        return ""
    month = old_sports_taxonomy.MONTHS.get(match.group(2).upper())
    if month is None:
        return ""
    try:
        local = datetime(
            2000 + int(match.group(1)),
            month,
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            tzinfo=KALSHI_TICKER_TZ,
        )
    except ValueError:
        return ""
    return utc_minute_text(local)


def ks_start_time_utc(
    series_ticker: str,
    event_ticker: str,
    close_time: Any,
    raw_payloads: list[dict[str, Any]],
) -> tuple[str, str]:
    ticker_start = ks_ticker_start_time_utc(event_ticker)
    if ticker_start:
        return ticker_start, "ks_event_ticker_et"
    if series_ticker.upper() == "KXWNBAGAME":
        parsed_close = parse_utc_datetime(close_time)
        if parsed_close is not None:
            return utc_minute_text(parsed_close - timedelta(days=14)), "ks_wnba_close_time_minus_14d"
    return "", ""


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


def start_time_delta_seconds(pm_row: DbOutcome, ks_row: DbOutcome) -> float | None:
    pm_start = parse_utc_datetime(pm_row.start_time_utc)
    ks_start = parse_utc_datetime(ks_row.start_time_utc)
    if pm_start is None or ks_start is None:
        return None
    return abs((pm_start - ks_start).total_seconds())


def timing_compatible(pm_row: DbOutcome, ks_row: DbOutcome) -> bool:
    delta = start_time_delta_seconds(pm_row, ks_row)
    if delta is not None:
        return delta <= START_TIME_TOLERANCE_SECONDS
    return dates_compatible(pm_row, ks_row)


def broad_candidate_timing(pm_row: DbOutcome, ks_row: DbOutcome) -> bool:
    delta = start_time_delta_seconds(pm_row, ks_row)
    if delta is not None:
        return delta <= CANDIDATE_START_TIME_TOLERANCE_SECONDS
    return dates_compatible(pm_row, ks_row)


def is_safe_taxonomy(classification: old_sports_taxonomy.TaxonomyResult, sports: str, market_type: str | None = None) -> tuple[bool, str]:
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
    outcome_keys = {old_sports_taxonomy.normalize_key(outcome) for outcome in outcomes}
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


def pm_market_type_override(classification: old_sports_taxonomy.TaxonomyResult, market_raw: dict[str, Any]) -> str:
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
        team = MLB_ALIAS_TO_KEY.get(old_sports_taxonomy.normalize_key(value))
        return f"baseball:mlb:men:{team}" if team else ""
    if universe in {"nba", "wnba"}:
        return old_sports_taxonomy.entity_key(category_key, universe, gender, value)
    if universe in TENNIS_UNIVERSES:
        key = old_sports_taxonomy.tennis_player_key(value)
        return f"tennis:{universe}:{gender}:{key}" if key else ""
    key = old_sports_taxonomy.normalize_key(value)
    return f"{category_key}:{universe}:{gender}:{key}" if key else ""


ESPORTS_STRIP_WORDS = {"esport", "esports"}


def esports_team_alias_keys(value: str) -> set[str]:
    folded = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    tokens = [token for token in folded.split() if token]
    base = old_sports_taxonomy.normalize_key(value)
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
        return tuple(sorted(prefix + alias for alias in old_sports_taxonomy.tennis_player_alias_keys(value)))
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
        outcomes = [str(outcome) for outcome in old_market_db.parse_json_list(row["outcomes_json"])]
        token_ids = [str(token_id) for token_id in old_market_db.parse_json_list(row["clob_token_ids_json"])]
        try:
            market_raw = old_market_db.json.loads(str(row["market_raw_json"] or "{}"))
        except ValueError:
            market_raw = {}
        event_raw = old_staging_normalize_market_db.parse_json_object(row["event_raw_json"])
        start_time, start_time_source = pm_start_time_utc(market_raw if isinstance(market_raw, dict) else {}, event_raw)
        ok, reason = pm_is_main_binary_market(title, question, outcomes, token_ids)
        if not ok:
            skipped[reason] += 1
            continue
        sources = old_staging_normalize_market_db.pm_sources(conn, event_slug)
        classification_context = old_staging_normalize_market_db.pm_classification_context(
            question,
            market_raw if isinstance(market_raw, dict) else {},
            event_raw,
            event_slug,
        )
        classification = old_sports_taxonomy.classify_pm_event(event_slug, title, sources, classification_context)
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
        slug_event_date = old_sports_taxonomy.event_date_from_pm(event_slug, str(row["start_date"] or ""), title)
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
                    start_time_utc=start_time,
                    start_time_source=start_time_source,
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
        event_date = old_sports_taxonomy.event_date_from_ks(
            event_ticker,
            str(event_rows[0]["close_time"] or ""),
            str(event_rows[0]["title"] or ""),
        )
        raw_payloads = []
        for row in event_rows:
            try:
                payload = old_market_db.json.loads(str(row["raw_json"] or "{}"))
            except ValueError:
                payload = {}
            raw_payloads.append(payload if isinstance(payload, dict) else {})
        event_date_candidates = unique_dates(
            event_ticker,
            event_rows[0]["close_time"],
            *(payload.get("occurrence_datetime") or payload.get("expected_expiration_time") or "" for payload in raw_payloads),
        )
        start_time, start_time_source = ks_start_time_utc(
            str(event_rows[0]["series_ticker"] or ""),
            event_ticker,
            event_rows[0]["close_time"],
            raw_payloads,
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
                    start_time_utc=start_time,
                    start_time_source=start_time_source,
                )
            )
    return rows, skipped


def event_outcomes_text(rows: list[DbOutcome]) -> str:
    seen = []
    for row in rows:
        value = row.outcome_name
        if value and value not in seen:
            seen.append(value)
    return " vs ".join(seen)


def event_name(rows: list[DbOutcome]) -> str:
    return rows[0].match_name if rows else ""


def row_start_time(row: DbOutcome | None, venue: str) -> str:
    if row is None or row.venue != venue:
        return ""
    return row.start_time_utc


def diagnostic_row(status: str, reason: str, row: DbOutcome | None, safe_paired: bool = False) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    pm_event = row.match_name if row and row.venue == "pm" else ""
    ks_event = row.match_name if row and row.venue == "ks" else ""
    pm_outcome = row.outcome_name if row and row.venue == "pm" else ""
    ks_outcome = row.outcome_name if row and row.venue == "ks" else ""
    return {
        "ts_utc": now,
        "category_key": row.category_key if row else "",
        "universe": row.universe if row else "",
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
        "pm_event_name": pm_event,
        "ks_event_name": ks_event,
        "pm_outcomes": pm_outcome,
        "ks_outcomes": ks_outcome,
        "pm_start_time_utc": row_start_time(row, "pm"),
        "ks_start_time_utc": row_start_time(row, "ks"),
        "candidate_side": "",
        "candidate_confidence": "",
        "why_candidate": "",
        "why_not_safe": "",
    }


def pair_diagnostic_row(pair: core.PairedContract) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ts_utc": now,
        "category_key": pair.universe,
        "universe": pair.universe,
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
        "pm_event_name": pair.match_name,
        "ks_event_name": "",
        "pm_outcomes": pair.pm_yes_outcome,
        "ks_outcomes": pair.ks_yes_outcome,
        "pm_start_time_utc": "",
        "ks_start_time_utc": "",
        "candidate_side": "",
        "candidate_confidence": "",
        "why_candidate": "",
        "why_not_safe": "",
    }


def candidate_diagnostic_row(
    *,
    reason: str,
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
    candidate_side: str,
    confidence: str,
    why_candidate: str,
    why_not_safe: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    pm_ref = pm_rows[0]
    ks_ref = ks_rows[0]
    return {
        "ts_utc": now,
        "category_key": pm_ref.category_key,
        "universe": pm_ref.universe,
        "status": "candidate_match",
        "reason": reason,
        "safe_paired": "false",
        "event_date": pm_ref.event_date or ks_ref.event_date,
        "canonical_event_id": pm_ref.canonical_event_id,
        "market_type": pm_ref.market_type,
        "match_name": f"{pm_ref.match_name} <> {ks_ref.match_name}",
        "outcome": event_outcomes_text(pm_rows),
        "pm_event_slug": pm_ref.pm_event_slug,
        "pm_market_id": pm_ref.pm_market_id,
        "pm_token_id": pm_ref.pm_token_id,
        "pm_question": pm_ref.pm_question,
        "ks_series_ticker": ks_ref.ks_series_ticker,
        "ks_event_ticker": ks_ref.ks_event_ticker,
        "ks_market_ticker": ks_ref.ks_market_ticker,
        "ks_title": ks_ref.ks_title,
        "ks_yes_outcome": event_outcomes_text(ks_rows),
        "entity_key": pm_ref.entity_key,
        "outcome_key": "",
        "pm_event_name": event_name(pm_rows),
        "ks_event_name": event_name(ks_rows),
        "pm_outcomes": event_outcomes_text(pm_rows),
        "ks_outcomes": event_outcomes_text(ks_rows),
        "pm_start_time_utc": pm_ref.start_time_utc,
        "ks_start_time_utc": ks_ref.start_time_utc,
        "candidate_side": candidate_side,
        "candidate_confidence": confidence,
        "why_candidate": why_candidate,
        "why_not_safe": why_not_safe,
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


def alias_overlap_count(
    left_rows: list[DbOutcome],
    right_rows: list[DbOutcome],
    overlap_fn,
) -> int:
    matched = set()
    for left in left_rows:
        if any(overlap_fn(left, right) for right in right_rows):
            matched.add(left.outcome_key)
    return len(matched)


def candidate_confidence_from(overlap_count: int, pm_ref: DbOutcome, ks_ref: DbOutcome) -> str:
    delta = start_time_delta_seconds(pm_ref, ks_ref)
    if overlap_count >= 2 and delta is not None and delta <= START_TIME_TOLERANCE_SECONDS:
        return "high"
    if overlap_count >= 1 and (delta is None or delta <= 60 * 60):
        return "medium"
    return "low"


def candidate_timing_text(pm_ref: DbOutcome, ks_ref: DbOutcome) -> str:
    delta = start_time_delta_seconds(pm_ref, ks_ref)
    if delta is None:
        return "start time missing on one side; date/scope is the fallback evidence"
    minutes = delta / 60
    return f"start times are {minutes:.0f} minutes apart"


def candidate_like_event(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
    overlap_fn,
) -> tuple[bool, int, str]:
    pm_ref = pm_rows[0]
    ks_ref = ks_rows[0]
    overlap_count = alias_overlap_count(pm_rows, ks_rows, overlap_fn)
    if overlap_count <= 0:
        return False, overlap_count, ""
    if not broad_candidate_timing(pm_ref, ks_ref):
        return False, overlap_count, ""
    why = (
        f"{overlap_count} participant alias overlap(s); "
        f"{candidate_timing_text(pm_ref, ks_ref)}"
    )
    return True, overlap_count, why


def best_candidate(
    candidates: list[tuple[int, str, list[DbOutcome], str]],
) -> tuple[int, str, list[DbOutcome], str] | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[0]


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


def safe_pair_alias_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
    *,
    spec: AliasPairingSpec,
    group_id_fn,
    scope_fn,
    overlap_fn,
    mapping_fn,
    require_start_time_for_single_overlap: bool = False,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    pairs: list[core.PairedContract] = []

    pm_groups: dict[str, list[DbOutcome]] = defaultdict(list)
    ks_groups: dict[str, list[DbOutcome]] = defaultdict(list)
    for row in pm_rows:
        pm_groups[group_id_fn(row)].append(row)
    for row in ks_rows:
        ks_groups[group_id_fn(row)].append(row)

    clean_pm_groups = {key: rows for key, rows in pm_groups.items() if event_is_binary(rows)}
    clean_ks_groups = {key: rows for key, rows in ks_groups.items() if event_is_binary(rows)}
    for key, rows in pm_groups.items():
        if key and key not in clean_pm_groups:
            message = f"{spec.ambiguous_pm_message}: {key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, rows[0]))
    for key, rows in ks_groups.items():
        if key and key not in clean_ks_groups:
            message = f"{spec.ambiguous_ks_message}: {key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, rows[0]))

    matched_pm_groups: set[str] = set()
    matched_ks_groups: set[str] = set()

    for ks_key, event_ks_rows in sorted(clean_ks_groups.items(), key=lambda item: item[0]):
        representative = event_ks_rows[0]
        candidates = []
        potential_candidates: list[tuple[int, str, list[DbOutcome], str]] = []
        for pm_key, event_pm_rows in clean_pm_groups.items():
            pm_ref = event_pm_rows[0]
            if scope_fn(pm_ref) != scope_fn(representative):
                continue
            is_candidate, overlap_count, _why_candidate = candidate_like_event(event_pm_rows, event_ks_rows, overlap_fn)
            if is_candidate and require_start_time_for_single_overlap and overlap_count < 2:
                delta = start_time_delta_seconds(pm_ref, representative)
                is_candidate = delta is not None and delta <= START_TIME_TOLERANCE_SECONDS
            if not timing_compatible(pm_ref, representative):
                if is_candidate:
                    potential_candidates.append((overlap_count, pm_key, event_pm_rows, "start times are not within safe tolerance"))
                continue
            mapping, reason = mapping_fn(event_pm_rows, event_ks_rows)
            if mapping is None:
                if is_candidate:
                    potential_candidates.append((overlap_count, pm_key, event_pm_rows, reason))
                continue
            candidates.append((pm_key, event_pm_rows, mapping))

        if not candidates:
            candidate = best_candidate(potential_candidates)
            if candidate is not None:
                overlap_count, _pm_key, event_pm_rows, why_not_safe = candidate
                diagnostics.append(
                    candidate_diagnostic_row(
                        reason=spec.reason_code,
                        pm_rows=event_pm_rows,
                        ks_rows=event_ks_rows,
                        candidate_side="PM candidate for KS event",
                        confidence=candidate_confidence_from(overlap_count, event_pm_rows[0], representative),
                        why_candidate=(
                            f"{overlap_count} {spec.candidate_label} alias overlap(s); "
                            f"{candidate_timing_text(event_pm_rows[0], representative)}"
                        ),
                        why_not_safe=why_not_safe,
                    )
                )
            else:
                diagnostics.append(diagnostic_row("unmatched_semantics", spec.missing_pm_reason, representative))
            continue
        if len(candidates) != 1:
            message = f"ambiguous {spec.name} alias match skipped: {representative.semantic_key}"
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
            scope_fn(representative) == scope_fn(ks_rows_for_event[0])
            and timing_compatible(representative, ks_rows_for_event[0])
            and mapping_fn(event_pm_rows, ks_rows_for_event)[0] is not None
            for ks_key, ks_rows_for_event in clean_ks_groups.items()
            if ks_key not in matched_ks_groups
        ):
            diagnostics.append(diagnostic_row("pm_only_inventory", spec.missing_ks_reason, representative))

    return pairs, diagnostics, warnings


def safe_pair_tennis_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    return safe_pair_alias_rows(
        pm_rows,
        ks_rows,
        spec=TENNIS_ALIAS_SPEC,
        group_id_fn=tennis_event_group_id,
        scope_fn=tennis_event_scope,
        overlap_fn=alias_overlap,
        mapping_fn=tennis_alias_mapping,
        require_start_time_for_single_overlap=True,
    )


def safe_pair_esports_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    return safe_pair_alias_rows(
        pm_rows,
        ks_rows,
        spec=ESPORTS_ALIAS_SPEC,
        group_id_fn=alias_event_group_id,
        scope_fn=alias_event_scope,
        overlap_fn=esports_alias_overlap,
        mapping_fn=esports_alias_mapping,
    )


def safe_pair_direct_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    pairs: list[core.PairedContract] = []

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
            if row.event_key not in pm_ambiguous and timing_compatible(row, representative)
        ]
        if semantic_key in pm_semantic_ambiguous:
            message = f"ambiguous PM binary event skipped: {semantic_key}"
            warnings.append(message)
            diagnostics.append(diagnostic_row("ambiguous_match", message, representative))
            continue
        if not event_pm_rows:
            diagnostics.append(diagnostic_row("unmatched_semantics", "no PM event with same universe/gender/start/matchup", representative))
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
        if not any(timing_compatible(row, ks_row) for rows_for_event in ks_by_event.values() for ks_row in rows_for_event if ks_row.semantic_key == row.semantic_key):
            diagnostics.append(diagnostic_row("pm_only_inventory", "no KS event with same universe/gender/start/matchup", row))

    return pairs, diagnostics, warnings


def safe_pair_rows(
    pm_rows: list[DbOutcome],
    ks_rows: list[DbOutcome],
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    pairs: list[core.PairedContract] = []

    stages = (
        safe_pair_tennis_rows(
            [row for row in pm_rows if is_tennis_row(row)],
            [row for row in ks_rows if is_tennis_row(row)],
        ),
        safe_pair_esports_rows(
            [row for row in pm_rows if is_esports_alias_row(row)],
            [row for row in ks_rows if is_esports_alias_row(row)],
        ),
        safe_pair_direct_rows(
            [row for row in pm_rows if not is_tennis_row(row) and not is_esports_alias_row(row)],
            [row for row in ks_rows if not is_tennis_row(row) and not is_esports_alias_row(row)],
        ),
    )
    for stage_pairs, stage_diagnostics, stage_warnings in stages:
        pairs.extend(stage_pairs)
        diagnostics.extend(stage_diagnostics)
        warnings.extend(stage_warnings)

    pairs.sort(key=lambda pair: (pair.universe, pair.event_date, pair.canonical_event_id, pair.pm_yes_outcome))
    return pairs, diagnostics, warnings


def build_pairs_from_db(
    conn,
    *,
    sports: str = "all",
    from_date: str | None = None,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str], dict[str, int]]:
    # Test/manual rebuild helper only. Long-running staging refresh must use
    # refresh_pairs_incremental so historical edge_snapshots keep their pair ids.
    old_market_db.reset_paired_contracts(conn)
    conn.execute("DELETE FROM data_quality_warnings WHERE stage = 'pair_from_db'")
    normalized_counts = old_staging_normalize_market_db.normalize_db(conn, sports)
    pm_rows, pm_skipped = build_pm_outcomes(conn, sports, from_date)
    ks_rows, ks_skipped = build_ks_outcomes(conn, sports, from_date)
    pairs, diagnostics, warnings = safe_pair_rows(pm_rows, ks_rows)

    for pair in pairs:
        old_market_db.upsert_paired_contract(conn, pair)
    for label, skipped in (("pm", pm_skipped), ("ks", ks_skipped)):
        for reason, count in skipped.items():
            if count:
                old_market_db.record_warning(
                    conn,
                    "pair_from_db",
                    f"{label.upper()} rows skipped during DB-only safe pairing: {reason}: {count}",
                    context={"venue": label, "reason": reason, "count": count},
                )
    for warning in warnings:
        old_market_db.record_warning(conn, "pair_from_db", warning)
    conn.commit()
    counts = {
        **normalized_counts,
        "pm_safe_candidates": len(pm_rows),
        "ks_safe_candidates": len(ks_rows),
        "safe_pairs": len(pairs),
    }
    return pairs, diagnostics, warnings, counts


def refresh_scope_universes(sports: str, pairs: list[core.PairedContract]) -> set[str]:
    requested = {part.strip().lower().replace("-", "_") for part in sports.split(",") if part.strip()}
    if not requested or "all" in requested:
        return set(SAFE_UNIVERSES)
    scope = {pair.universe for pair in pairs}
    scope.update(part for part in requested if part in SAFE_UNIVERSES)
    return scope


def disable_stale_safe_pairs(
    conn,
    *,
    current_pair_keys: set[str],
    scope_universes: set[str],
    from_date: str | None,
) -> int:
    if not scope_universes:
        return 0
    placeholders = ",".join("?" for _ in scope_universes)
    params: list[Any] = sorted(scope_universes)
    date_filter = ""
    if from_date:
        date_filter = "AND event_date >= ?"
        params.append(from_date)
    rows = conn.execute(
        f"""
        SELECT paired_contract_id, pair_key
        FROM paired_contracts
        WHERE safe_paired = 1
          AND universe IN ({placeholders})
          {date_filter}
        """,
        tuple(params),
    ).fetchall()
    stale_ids = [int(row["paired_contract_id"]) for row in rows if str(row["pair_key"]) not in current_pair_keys]
    if not stale_ids:
        return 0
    now = old_market_db.utc_now()
    id_placeholders = ",".join("?" for _ in stale_ids)
    conn.execute(
        f"""
        UPDATE paired_contracts
        SET safe_paired = 0,
            last_seen_ts = ?
        WHERE paired_contract_id IN ({id_placeholders})
        """,
        (now, *stale_ids),
    )
    return len(stale_ids)


def refresh_pairs_incremental(
    conn,
    *,
    sports: str = "all",
    from_date: str | None = None,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str], dict[str, int]]:
    normalized_counts = old_staging_normalize_market_db.normalize_db(conn, sports)
    pm_rows, pm_skipped = build_pm_outcomes(conn, sports, from_date)
    ks_rows, ks_skipped = build_ks_outcomes(conn, sports, from_date)
    pairs, diagnostics, warnings = safe_pair_rows(pm_rows, ks_rows)

    current_pair_keys: set[str] = set()
    for pair in pairs:
        current_pair_keys.add(old_market_db.pair_key(pair))
        old_market_db.upsert_paired_contract(conn, pair)
    disabled_pairs = disable_stale_safe_pairs(
        conn,
        current_pair_keys=current_pair_keys,
        scope_universes=refresh_scope_universes(sports, pairs),
        from_date=from_date,
    )
    for label, skipped in (("pm", pm_skipped), ("ks", ks_skipped)):
        for reason, count in skipped.items():
            if count:
                old_market_db.record_warning(
                    conn,
                    "pair_refresh_incremental",
                    f"{label.upper()} rows skipped during DB-only safe pairing refresh: {reason}: {count}",
                    context={"venue": label, "reason": reason, "count": count},
                )
    for warning in warnings:
        old_market_db.record_warning(conn, "pair_refresh_incremental", warning)
    conn.commit()
    counts = {
        **normalized_counts,
        "pm_safe_candidates": len(pm_rows),
        "ks_safe_candidates": len(ks_rows),
        "safe_pairs": len(pairs),
        "upserted_pairs": len(pairs),
        "disabled_pairs": disabled_pairs,
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
    row = old_market_db.pair_to_dict(pair)
    row["safe_paired"] = "true"
    return row


def write_pairing_outputs(
    pairs: list[core.PairedContract],
    diagnostics: list[dict[str, Any]],
    *,
    pairs_output: str | Path,
    diagnostics_output: str | Path,
) -> None:
    core.write_latest_csv([pair_to_output_row(pair) for pair in pairs], PAIR_FIELDS, Path(pairs_output))
    core.write_latest_csv(diagnostics, DIAGNOSTIC_FIELDS, Path(diagnostics_output))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build conservative safe PM/KS binary pairs from the local SQLite DB only.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="all")
    parser.add_argument("--from-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--all-dates", action="store_true")
    parser.add_argument("--pairs-output", default="data/old/old_latest_paired_contracts.csv")
    parser.add_argument("--diagnostics-output", default="data/old/old_latest_pairing_diagnostics.csv")
    parser.add_argument("--csv-only", action="store_true", help="Generate CSV outputs without writing paired_contracts/edge tables.")
    parser.add_argument("--reset", action="store_true", help="Destructively rebuild paired_contracts and edge_snapshots. Not for live refresh loops.")
    parser.add_argument("--allow-destructive-reset", action="store_true", help="Required with --reset; never use from tmux refresh loops.")
    parser.add_argument("--busy-timeout-ms", type=int, default=60000)
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()
    if args.all_dates:
        args.from_date = None
    if args.busy_timeout_ms <= 0:
        parser.error("--busy-timeout-ms must be positive")
    if args.reset and not args.allow_destructive_reset:
        parser.error("--reset is destructive; use refresh incremental mode or pass --allow-destructive-reset for isolated tests only")

    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        conn.execute(f"PRAGMA busy_timeout = {args.busy_timeout_ms}")
        if args.csv_only:
            pairs, diagnostics, warnings, build_counts = build_pairs_csv_only(
                conn,
                sports=args.sports,
                from_date=args.from_date,
            )
        elif args.reset:
            pairs, diagnostics, warnings, build_counts = build_pairs_from_db(
                conn,
                sports=args.sports,
                from_date=args.from_date,
            )
        else:
            pairs, diagnostics, warnings, build_counts = refresh_pairs_incremental(
                conn,
                sports=args.sports,
                from_date=args.from_date,
            )
        write_pairing_outputs(
            pairs,
            diagnostics,
            pairs_output=args.pairs_output,
            diagnostics_output=args.diagnostics_output,
        )
        counts = old_market_db.row_counts(conn)

    if args.show_warnings:
        for warning in warnings:
            print(f"DB pair warning: {warning}", file=sys.stderr)
    print(
        "staging DB pair complete: "
        f"mode={'csv_only' if args.csv_only else 'reset' if args.reset else 'incremental'}; "
        f"pm_safe_candidates={build_counts['pm_safe_candidates']}; "
        f"ks_safe_candidates={build_counts['ks_safe_candidates']}; "
        f"safe_pairs={build_counts['safe_pairs']}; "
        f"disabled_pairs={build_counts.get('disabled_pairs', 0)}; "
        f"pairs_output={args.pairs_output}; diagnostics_output={args.diagnostics_output}"
    )
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
