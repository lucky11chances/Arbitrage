from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pipeline_core as core
import sports_pairing
import sports_registry
import universe_adapters
from sports_adapters.base import SportAdapter


ESPN_WNBA_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
WNBA_PM_SLUG_RE = re.compile(r"^wnba-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})$")
WNBA_KS_EVENT_RE = re.compile(r"^KXWNBAGAME-(\d{2})([A-Z]{3})(\d{2})[A-Z]+$")
SCHEDULE_SOURCE = "espn_wnba_scoreboard"


@dataclass(frozen=True)
class WnbaTeam:
    key: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class WnbaGame:
    game_id: str
    official_date: str
    game_date_utc: str
    away_team: WnbaTeam
    home_team: WnbaTeam

    @property
    def team_keys(self) -> frozenset[str]:
        return frozenset({self.away_team.key, self.home_team.key})

    @property
    def matchup(self) -> str:
        return f"{self.away_team.name} @ {self.home_team.name}"


WNBA_TEAMS = (
    WnbaTeam("atlantadream", "Atlanta Dream", ("atlanta", "atl", "atlanta dream", "dream")),
    WnbaTeam("chicagosky", "Chicago Sky", ("chicago", "chi", "chicago sky", "sky")),
    WnbaTeam("connecticutsun", "Connecticut Sun", ("connecticut", "conn", "con", "connecticut sun", "sun")),
    WnbaTeam("dallaswings", "Dallas Wings", ("dallas", "dal", "dallas wings", "wings")),
    WnbaTeam("goldenstatevalkyries", "Golden State Valkyries", ("golden state", "gs", "gsv", "golden state valkyries", "valkyries")),
    WnbaTeam("indianafever", "Indiana Fever", ("indiana", "ind", "indiana fever", "fever")),
    WnbaTeam("lasvegasaces", "Las Vegas Aces", ("las vegas", "lv", "lva", "las vegas aces", "aces")),
    WnbaTeam("losangelessparks", "Los Angeles Sparks", ("los angeles", "la", "los angeles sparks", "sparks")),
    WnbaTeam("minnesotalynx", "Minnesota Lynx", ("minnesota", "min", "minnesota lynx", "lynx")),
    WnbaTeam("newyorkliberty", "New York Liberty", ("new york", "ny", "nyl", "new york liberty", "liberty")),
    WnbaTeam("phoenixmercury", "Phoenix Mercury", ("phoenix", "phx", "phoenix mercury", "mercury")),
    WnbaTeam("portlandfire", "Portland Fire", ("portland", "pdx", "por", "portland fire", "portlandfire", "fire")),
    WnbaTeam("seattlestorm", "Seattle Storm", ("seattle", "sea", "seattle storm", "storm")),
    WnbaTeam("torontotempo", "Toronto Tempo", ("toronto", "tor", "toronto tempo", "tempo")),
    WnbaTeam("washingtonmystics", "Washington Mystics", ("washington", "was", "wsh", "washington mystics", "mystics")),
)


TEAM_BY_ALIAS = {
    sports_pairing.normalize_key(alias): team
    for team in WNBA_TEAMS
    for alias in (team.name, team.key, *team.aliases)
}


def team_from_name(value: str) -> WnbaTeam | None:
    return TEAM_BY_ALIAS.get(sports_pairing.normalize_key(value))


def team_from_abbr(value: str) -> WnbaTeam | None:
    return team_from_name(value)


def parse_wnba_ks_date(event_ticker: str) -> str | None:
    match = WNBA_KS_EVENT_RE.match(event_ticker)
    if not match:
        return None
    return universe_adapters.date_from_yy_mon_day(match.group(1), match.group(2), match.group(3))


def discover_pm_wnba(limit: int, from_date: str | None) -> list[core.PMBinaryMarket]:
    games: list[core.PMBinaryMarket] = []
    for event in core.fetch_polymarket_events(("wnba",), limit, ("true",)):
        slug = str(event.get("slug") or "")
        match = WNBA_PM_SLUG_RE.match(slug)
        if not match:
            continue
        event_date = match.group(1)
        if from_date and event_date < from_date:
            continue
        parsed = pm_moneyline_from_event(event, event_date)
        if parsed is not None:
            games.append(parsed)
    games.sort(key=lambda game: (game.event_date, game.event_slug))
    return games


def pm_moneyline_from_event(event: dict[str, Any], event_date: str) -> core.PMBinaryMarket | None:
    market = core.find_pm_binary_market(event)
    if market is None:
        return None
    outcomes = core.parse_json_list(market.get("outcomes"))
    token_ids = core.parse_json_list(market.get("clobTokenIds"))
    if len(outcomes) != 2 or len(token_ids) != 2:
        return None
    return core.PMBinaryMarket(
        event_date=event_date,
        event_slug=str(event.get("slug") or ""),
        event_title=str(event.get("title") or ""),
        market_id=str(market.get("id") or ""),
        market_question=str(market.get("question") or ""),
        outcome_a=str(outcomes[0]),
        outcome_b=str(outcomes[1]),
        token_a=str(token_ids[0]),
        token_b=str(token_ids[1]),
    )


def discover_ks_wnba(limit: int) -> list[core.KSEvent]:
    return universe_adapters.discover_ks_binary_events("basketball", "KXWNBAGAME", limit, parse_wnba_ks_date)


def fetch_wnba_schedule(start_date: str, end_date: str) -> list[WnbaGame]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    games: list[WnbaGame] = []
    current = start
    while current <= end:
        games.extend(fetch_wnba_schedule_day(current))
        current += timedelta(days=1)
    return games


def fetch_wnba_schedule_day(day: date) -> list[WnbaGame]:
    payload = core.get_json(ESPN_WNBA_API, "/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 100})
    games: list[WnbaGame] = []
    for event in payload.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        away_team = None
        home_team = None
        for competitor in competitors:
            raw_team = competitor.get("team") or {}
            team = team_from_name(str(raw_team.get("displayName") or "")) or team_from_abbr(str(raw_team.get("abbreviation") or ""))
            if team is None:
                continue
            if competitor.get("homeAway") == "away":
                away_team = team
            elif competitor.get("homeAway") == "home":
                home_team = team
        if away_team is None or home_team is None:
            continue
        games.append(
            WnbaGame(
                game_id=str(event.get("id") or ""),
                official_date=day.isoformat(),
                game_date_utc=str(event.get("date") or ""),
                away_team=away_team,
                home_team=home_team,
            )
        )
    return games


def pair_wnba(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    pm_games = discover_pm_wnba(pm_limit, from_date)
    ks_games = discover_ks_wnba(ks_limit)
    schedule_dates = {game.event_date for game in pm_games}
    schedule_dates.update(game.event_date for game in ks_games)
    if not schedule_dates:
        return [], ["no active WNBA game markets found on PM or Kalshi"]

    schedule = fetch_wnba_schedule(min(schedule_dates), max(schedule_dates))
    official_by_date_team_set = {(game.official_date, game.team_keys): game for game in schedule}

    warnings: list[str] = []
    pm_index: dict[tuple[str, str], tuple[core.PMBinaryMarket, str, str]] = {}
    for pm_game in pm_games:
        pm_team_a = team_from_name(pm_game.outcome_a)
        pm_team_b = team_from_name(pm_game.outcome_b)
        if pm_team_a is None or pm_team_b is None:
            warnings.append(f"PM WNBA team name not mappable: {pm_game.event_slug}")
            continue
        official = official_by_date_team_set.get((pm_game.event_date, frozenset({pm_team_a.key, pm_team_b.key})))
        if official is None:
            warnings.append(f"PM WNBA game not found in schedule: {pm_game.event_slug}")
            continue
        pm_index[(official.game_id, pm_team_a.key)] = (pm_game, pm_game.outcome_a, pm_game.token_a)
        pm_index[(official.game_id, pm_team_b.key)] = (pm_game, pm_game.outcome_b, pm_game.token_b)

    pairs: list[core.PairedContract] = []
    for ks_game in ks_games:
        ks_teams = [team_from_abbr(universe_adapters.market_team_abbr(market.ticker)) for market in ks_game.markets]
        if any(team is None for team in ks_teams):
            warnings.append(f"KS WNBA team/date not mappable: {ks_game.event_ticker}")
            continue
        official = official_by_date_team_set.get((ks_game.event_date, frozenset(team.key for team in ks_teams if team)))
        if official is None:
            warnings.append(f"KS WNBA game not found in schedule: {ks_game.event_ticker}")
            continue
        for market, team in zip(ks_game.markets, ks_teams):
            if team is None:
                continue
            pm_match = pm_index.get((official.game_id, team.key))
            if pm_match is None:
                warnings.append(f"KS WNBA matched schedule but no PM market: {ks_game.event_ticker} {team.name}")
                continue
            pm_game, pm_yes, pm_token = pm_match
            pairs.append(
                core.PairedContract(
                    universe="wnba",
                    category="sports",
                    match_name=official.matchup,
                    event_date=official.official_date,
                    canonical_event_id=f"wnba:{official.game_id}",
                    market_type="game_winner",
                    pm_yes_outcome=pm_yes,
                    pm_event_slug=pm_game.event_slug,
                    pm_market_id=pm_game.market_id,
                    pm_token_id=pm_token,
                    ks_yes_outcome=market.yes_outcome,
                    ks_event_ticker=ks_game.event_ticker,
                    ks_market_ticker=market.ticker,
                    schedule_source=SCHEDULE_SOURCE,
                )
            )
    return pairs, warnings


def build_basketball_diagnostics(
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    pairs, warnings = pair_wnba(pm_limit, ks_limit, from_date)
    rows = [
        sports_pairing.pair_status_row("basketball", pair, "paired_existing", "WNBA game pairer")
        for pair in pairs
    ]
    if not rows:
        rows = [sports_pairing.empty_status("basketball", "unmatched_semantics", "WNBA pairer returned no safe pairs")]
    return rows, warnings


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("basketball"),
    pairer=pair_wnba,
    diagnostics_builder=build_basketball_diagnostics,
)
