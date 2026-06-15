from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import nba_common
import pipeline_core as core


MLB_STATS_API = "https://statsapi.mlb.com/api/v1"
ESPN_NBA_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
RIOT_LOL_ESPORTS_URL = "https://lolesports.com/en-US"
RIOT_VALORANT_ESPORTS_URL = "https://valorantesports.com/en-US"
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
MLB_PM_SLUG_RE = re.compile(r"^mlb-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})$")
NBA_PM_SLUG_RE = re.compile(r"^nba-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})$")
MLB_KS_EVENT_RE = re.compile(r"^KXMLBGAME-(\d{2})([A-Z]{3})(\d{2})\d{4}[A-Z]+$")
ESPORTS_KS_DATE_RE = re.compile(r"^KX(?:LOL|CS2|VALORANT)GAME-(\d{2})([A-Z]{3})(\d{2})\d{4}")
SLUG_DATE_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})$")
PM_WORLDCUP_GAME_RE = re.compile(r"^fifwc-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})$")
BO_FORMAT_RE = re.compile(r"\b(BO\d+)\b", re.IGNORECASE)
ESPORTS_TITLE_RE = re.compile(
    r"^(?P<label>Valorant|Counter-Strike|LoL|League of Legends): "
    r"(?P<team_a>.+?) vs\.? (?P<team_b>.+?) \((?P<fmt>BO\d+)\)",
    re.IGNORECASE,
)
KALSHI_ESPORTS_TITLE_RE = re.compile(
    r"win the (?P<team_a>.+?) vs\.? (?P<team_b>.+?) "
    r"(?P<label>League of Legends|CS2|Valorant) match",
    re.IGNORECASE,
)
TEAM_ALIASES = {
    "apogeeesports": "betclicapogeeesports",
    "betclicapogeeesports": "betclicapogeeesports",
    "betboom": "betboomteam",
    "betboomteam": "betboomteam",
    "dynamoesports": "dynamo",
    "shopifyrebellion": "shopifyrebellionblack",
    "shopifyrebellionblack": "shopifyrebellionblack",
    "xlggaming": "xilaigaming",
}
ESPORTS_CONFIG = {
    "cs2": {
        "series_ticker": "KXCS2GAME",
        "pm_tags": ("esports", "cs2"),
        "pm_labels": {"counter-strike"},
        "kalshi_label": "cs2",
    },
    "lol": {
        "series_ticker": "KXLOLGAME",
        "pm_tags": ("esports", "lol"),
        "pm_labels": {"lol", "league of legends"},
        "kalshi_label": "league of legends",
    },
    "valorant": {
        "series_ticker": "KXVALORANTGAME",
        "pm_tags": ("esports", "valorant"),
        "pm_labels": {"valorant"},
        "kalshi_label": "valorant",
    },
}


@dataclass(frozen=True)
class MlbTeam:
    id: int
    name: str
    abbreviation: str
    file_code: str


@dataclass(frozen=True)
class MlbGame:
    game_pk: int
    official_date: str
    game_date_utc: str
    away_team: MlbTeam
    home_team: MlbTeam

    @property
    def team_ids(self) -> frozenset[int]:
        return frozenset({self.away_team.id, self.home_team.id})

    @property
    def matchup(self) -> str:
        return f"{self.away_team.name} @ {self.home_team.name}"


@dataclass(frozen=True)
class NbaGame:
    source: str
    game_id: str
    official_date: str
    game_date_utc: str
    away_team: nba_common.NbaTeam
    home_team: nba_common.NbaTeam

    @property
    def team_ids(self) -> frozenset[int]:
        return frozenset({self.away_team.id, self.home_team.id})

    @property
    def matchup(self) -> str:
        return f"{self.away_team.name} @ {self.home_team.name}"


@dataclass(frozen=True)
class OfficialEsportsTeam:
    id: str
    name: str
    code: str


@dataclass(frozen=True)
class OfficialEsportsMatch:
    universe: str
    match_id: str
    event_date: str
    start_time_utc: str
    league_name: str
    tournament_name: str
    block_name: str
    match_format: str
    source: str
    teams: tuple[OfficialEsportsTeam, OfficialEsportsTeam]

    @property
    def matchup(self) -> str:
        suffix = " ".join(part for part in [self.league_name, self.block_name] if part).strip()
        if suffix:
            return f"{self.teams[0].name} vs {self.teams[1].name} - {suffix}"
        return f"{self.teams[0].name} vs {self.teams[1].name}"


def pair_binary_universe(universe: str, pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    if universe == "mlb":
        return pair_mlb(pm_limit, ks_limit, from_date)
    if universe == "nba":
        return pair_nba(pm_limit, ks_limit, from_date)
    if universe in ESPORTS_CONFIG:
        return pair_esports(universe, pm_limit, ks_limit, from_date)
    raise ValueError(f"unsupported binary universe: {universe}")


def discover_pm_mlb(limit: int, from_date: str | None) -> list[core.PMBinaryMarket]:
    games = []
    for event in core.fetch_polymarket_events(("mlb",), limit, ("true",)):
        slug = str(event.get("slug") or "")
        title = str(event.get("title") or "")
        match = MLB_PM_SLUG_RE.match(slug)
        if not match or " vs. " not in title:
            continue
        event_date = match.group(1)
        if from_date and event_date < from_date:
            continue
        parsed = pm_market_from_event(event, event_date)
        if parsed is not None:
            games.append(parsed)
    return games


def discover_pm_nba(limit: int, from_date: str | None) -> list[core.PMBinaryMarket]:
    games = []
    for event in core.fetch_polymarket_events(("nba",), limit, ("true",)):
        slug = str(event.get("slug") or "")
        title = str(event.get("title") or "")
        match = NBA_PM_SLUG_RE.match(slug)
        if not match or not (" vs. " in title or " at " in title or " @ " in title):
            continue
        event_date = match.group(1)
        if from_date and event_date < from_date:
            continue
        parsed = pm_market_from_event(event, event_date)
        if parsed is not None:
            games.append(parsed)
    return games


def discover_pm_esports(universe: str, limit: int, from_date: str | None) -> list[core.PMBinaryMarket]:
    config = ESPORTS_CONFIG[universe]
    games = []
    for event in core.fetch_polymarket_events(config["pm_tags"], limit):
        slug = str(event.get("slug") or "")
        title = str(event.get("title") or "")
        match = ESPORTS_TITLE_RE.match(title)
        if not match or match.group("label").lower() not in config["pm_labels"]:
            continue
        event_date = parse_slug_date(slug)
        if not event_date or (from_date and event_date < from_date):
            continue
        parsed = pm_market_from_event(event, event_date, parse_bo_format(title))
        if parsed is not None:
            games.append(parsed)
    games.sort(key=lambda game: (game.event_date, game.event_title))
    return games


def pm_market_from_event(event: dict[str, Any], event_date: str, match_format: str = "") -> core.PMBinaryMarket | None:
    market = core.find_pm_binary_market(event)
    if market is None:
        return None
    outcomes = core.parse_json_list(market.get("outcomes"))
    token_ids = core.parse_json_list(market.get("clobTokenIds"))
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
        match_format=match_format,
    )


def discover_ks_binary_events(
    universe: str,
    series_ticker: str,
    limit: int,
    date_parser,
    title_team_parser=None,
) -> list[core.KSEvent]:
    by_event: dict[str, list[core.KSMarket]] = defaultdict(list)
    titles: dict[str, str] = {}
    for raw_market in core.fetch_kalshi_series_markets(series_ticker, limit):
        event_ticker = str(raw_market.get("event_ticker") or "")
        ticker = str(raw_market.get("ticker") or "")
        yes_outcome = str(raw_market.get("yes_sub_title") or "")
        if not event_ticker or not ticker or not yes_outcome:
            continue
        by_event[event_ticker].append(
            core.KSMarket(
                ticker=ticker,
                yes_outcome=yes_outcome,
                yes_bid=raw_market.get("yes_bid_dollars"),
                yes_ask=raw_market.get("yes_ask_dollars"),
                close_time=str(raw_market.get("close_time") or ""),
            )
        )
        titles[event_ticker] = clean_ks_title(str(raw_market.get("title") or ""))

    events: list[core.KSEvent] = []
    for event_ticker, markets in by_event.items():
        if len(markets) != 2:
            continue
        event_date = date_parser(event_ticker)
        if event_date is None:
            continue
        title = titles.get(event_ticker, "")
        team_a, team_b = title_team_parser(title, markets) if title_team_parser else (markets[0].yes_outcome, markets[1].yes_outcome)
        events.append(
            core.KSEvent(
                universe=universe,
                event_ticker=event_ticker,
                title=title,
                event_date=event_date,
                team_a=team_a,
                team_b=team_b,
                match_format=parse_bo_format(title),
                markets=markets,
            )
        )
        if len(events) >= limit:
            break
    events.sort(key=lambda event: (event.event_date, event.event_ticker))
    return events


def pair_mlb(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    pm_games = discover_pm_mlb(pm_limit, from_date)
    ks_games = discover_ks_binary_events("mlb", "KXMLBGAME", ks_limit, parse_mlb_ks_date)
    schedule_dates = {game.event_date for game in pm_games}
    schedule_dates.update(game.event_date for game in ks_games)
    if not schedule_dates:
        return [], ["no candidate MLB dates found from PM or Kalshi"]

    schedule = fetch_mlb_schedule(min(schedule_dates), max(schedule_dates))
    official_by_date_team_set = {(game.official_date, game.team_ids): game for game in schedule}
    team_by_name = {normalize_plain(team.name): team for game in schedule for team in [game.away_team, game.home_team]}
    team_by_abbr = {
        team.abbreviation.upper(): team
        for game in schedule
        for team in [game.away_team, game.home_team]
    }

    warnings: list[str] = []
    pm_index: dict[tuple[int, int], tuple[core.PMBinaryMarket, str, str]] = {}
    for pm_game in pm_games:
        pm_team_a = team_by_name.get(normalize_plain(pm_game.outcome_a))
        pm_team_b = team_by_name.get(normalize_plain(pm_game.outcome_b))
        if pm_team_a is None or pm_team_b is None:
            warnings.append(f"PM team name not in MLB official schedule: {pm_game.event_slug}")
            continue
        official = official_by_date_team_set.get((pm_game.event_date, frozenset({pm_team_a.id, pm_team_b.id})))
        if official is None:
            warnings.append(f"PM MLB game not found in official schedule: {pm_game.event_slug}")
            continue
        pm_index[(official.game_pk, pm_team_a.id)] = (pm_game, pm_game.outcome_a, pm_game.token_a)
        pm_index[(official.game_pk, pm_team_b.id)] = (pm_game, pm_game.outcome_b, pm_game.token_b)

    pairs: list[core.PairedContract] = []
    for ks_game in ks_games:
        ks_teams = [team_by_abbr.get(market_team_abbr(market.ticker)) for market in ks_game.markets]
        if any(team is None for team in ks_teams):
            warnings.append(f"KS MLB team/date not mappable: {ks_game.event_ticker}")
            continue
        official = official_by_date_team_set.get((ks_game.event_date, frozenset(team.id for team in ks_teams if team)))
        if official is None:
            warnings.append(f"KS MLB game not found in official schedule: {ks_game.event_ticker}")
            continue
        for market, team in zip(ks_game.markets, ks_teams):
            if team is None:
                continue
            pm_match = pm_index.get((official.game_pk, team.id))
            if pm_match is None:
                warnings.append(f"KS MLB matched official game but no PM market: {ks_game.event_ticker} {team.name}")
                continue
            pm_game, pm_yes, pm_token = pm_match
            pairs.append(
                core.PairedContract(
                    universe="mlb",
                    category="sports",
                    match_name=official.matchup,
                    event_date=official.official_date,
                    canonical_event_id=f"mlb:{official.game_pk}",
                    market_type="game_winner",
                    pm_yes_outcome=pm_yes,
                    pm_event_slug=pm_game.event_slug,
                    pm_market_id=pm_game.market_id,
                    pm_token_id=pm_token,
                    ks_yes_outcome=market.yes_outcome,
                    ks_event_ticker=ks_game.event_ticker,
                    ks_market_ticker=market.ticker,
                    schedule_source="mlb_stats_api",
                )
            )
    return pairs, warnings


def fetch_mlb_schedule(start_date: str, end_date: str) -> list[MlbGame]:
    payload = core.get_json(
        MLB_STATS_API,
        "/schedule",
        {"sportId": 1, "startDate": start_date, "endDate": end_date, "hydrate": "team"},
    )
    games = []
    for date_block in payload.get("dates", []):
        for raw_game in date_block.get("games", []):
            away = raw_game["teams"]["away"]["team"]
            home = raw_game["teams"]["home"]["team"]
            games.append(
                MlbGame(
                    game_pk=int(raw_game["gamePk"]),
                    official_date=str(raw_game["officialDate"]),
                    game_date_utc=str(raw_game["gameDate"]),
                    away_team=mlb_team_from_payload(away),
                    home_team=mlb_team_from_payload(home),
                )
            )
    return games


def mlb_team_from_payload(team: dict[str, Any]) -> MlbTeam:
    return MlbTeam(
        id=int(team["id"]),
        name=str(team["name"]),
        abbreviation=str(team.get("abbreviation") or ""),
        file_code=str(team.get("fileCode") or ""),
    )


def pair_nba(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    pm_games = discover_pm_nba(pm_limit, from_date)
    ks_games = discover_ks_binary_events("nba", "KXNBAGAME", ks_limit, nba_common.parse_kalshi_event_date)
    schedule_dates = {game.event_date for game in pm_games}
    schedule_dates.update(game.event_date for game in ks_games)
    if not schedule_dates:
        return [], ["no active NBA game markets found on PM or Kalshi"]

    schedule = fetch_nba_schedule(min(schedule_dates), max(schedule_dates))
    official_by_date_team_set = {(game.official_date, game.team_ids): game for game in schedule}

    warnings: list[str] = []
    pm_index: dict[tuple[str, int], tuple[core.PMBinaryMarket, str, str]] = {}
    for pm_game in pm_games:
        pm_team_a = nba_common.team_from_name(pm_game.outcome_a)
        pm_team_b = nba_common.team_from_name(pm_game.outcome_b)
        if pm_team_a is None or pm_team_b is None:
            warnings.append(f"PM NBA team name not mappable: {pm_game.event_slug}")
            continue
        official = official_by_date_team_set.get((pm_game.event_date, frozenset({pm_team_a.id, pm_team_b.id})))
        if official is None:
            warnings.append(f"PM NBA game not found in schedule: {pm_game.event_slug}")
            continue
        pm_index[(official.game_id, pm_team_a.id)] = (pm_game, pm_game.outcome_a, pm_game.token_a)
        pm_index[(official.game_id, pm_team_b.id)] = (pm_game, pm_game.outcome_b, pm_game.token_b)

    pairs: list[core.PairedContract] = []
    for ks_game in ks_games:
        ks_teams = [nba_common.team_from_abbr(market_team_abbr(market.ticker)) for market in ks_game.markets]
        if any(team is None for team in ks_teams):
            warnings.append(f"KS NBA team/date not mappable: {ks_game.event_ticker}")
            continue
        official = official_by_date_team_set.get((ks_game.event_date, frozenset(team.id for team in ks_teams if team)))
        if official is None:
            warnings.append(f"KS NBA game not found in schedule: {ks_game.event_ticker}")
            continue
        for market, team in zip(ks_game.markets, ks_teams):
            if team is None:
                continue
            pm_match = pm_index.get((official.game_id, team.id))
            if pm_match is None:
                warnings.append(f"KS NBA matched schedule but no PM market: {ks_game.event_ticker} {team.name}")
                continue
            pm_game, pm_yes, pm_token = pm_match
            pairs.append(
                core.PairedContract(
                    universe="nba",
                    category="sports",
                    match_name=official.matchup,
                    event_date=official.official_date,
                    canonical_event_id=f"nba:{official.game_id}",
                    market_type="game_winner",
                    pm_yes_outcome=pm_yes,
                    pm_event_slug=pm_game.event_slug,
                    pm_market_id=pm_game.market_id,
                    pm_token_id=pm_token,
                    ks_yes_outcome=market.yes_outcome,
                    ks_event_ticker=ks_game.event_ticker,
                    ks_market_ticker=market.ticker,
                    schedule_source=official.source,
                )
            )
    return pairs, warnings


def fetch_nba_schedule(start_date: str, end_date: str) -> list[NbaGame]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    games: list[NbaGame] = []
    current = start
    while current <= end:
        games.extend(fetch_nba_schedule_day(current))
        current += timedelta(days=1)
    return games


def fetch_nba_schedule_day(day: date) -> list[NbaGame]:
    payload = core.get_json(ESPN_NBA_API, "/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 100})
    games = []
    for event in payload.get("events", []):
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        away_team = None
        home_team = None
        for competitor in competitors:
            raw_team = competitor.get("team") or {}
            team = nba_common.team_from_name(str(raw_team.get("displayName") or "")) or nba_common.team_from_abbr(
                str(raw_team.get("abbreviation") or "")
            )
            if team is None:
                continue
            if competitor.get("homeAway") == "away":
                away_team = team
            elif competitor.get("homeAway") == "home":
                home_team = team
        if away_team is None or home_team is None:
            continue
        games.append(
            NbaGame(
                source="espn_scoreboard",
                game_id=str(event.get("id") or ""),
                official_date=day.isoformat(),
                game_date_utc=str(event.get("date") or ""),
                away_team=away_team,
                home_team=home_team,
            )
        )
    return games


def pair_esports(universe: str, pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    config = ESPORTS_CONFIG[universe]
    if universe == "cs2":
        return [], [
            "CS2 skipped: no centralized Valve/official organizer schedule adapter is configured; "
            "name-only PM/Kalshi pairing is disabled."
        ]

    official_matches, warnings = fetch_official_esports_schedule(universe, from_date)
    if not official_matches:
        warnings.append(f"{universe} skipped: no usable official Riot schedule matches found")
        return [], warnings

    pm_games = discover_pm_esports(universe, pm_limit, from_date)
    ks_games = discover_ks_binary_events(
        universe,
        config["series_ticker"],
        ks_limit,
        parse_esports_ks_date,
        parse_kalshi_esports_title_teams,
    )

    pm_by_official_id: dict[str, core.PMBinaryMarket] = {}
    for pm_game in pm_games:
        official = find_official_esports_match(
            official_matches,
            pm_game.event_date,
            pm_game.outcome_a,
            pm_game.outcome_b,
            pm_game.match_format,
        )
        if official is None:
            warnings.append(f"{universe} PM not in official Riot schedule, skipped: {pm_game.event_slug}")
            continue
        if official.match_id in pm_by_official_id:
            warnings.append(f"{universe} duplicate PM market for official match, skipped: {pm_game.event_slug}")
            continue
        pm_by_official_id[official.match_id] = pm_game

    pairs: list[core.PairedContract] = []
    for ks_game in ks_games:
        official = find_official_esports_match(
            official_matches,
            ks_game.event_date,
            ks_game.team_a,
            ks_game.team_b,
            ks_game.match_format,
        )
        if official is None:
            warnings.append(f"{universe} KS not in official Riot schedule, skipped: {ks_game.event_ticker}")
            continue
        pm_game = pm_by_official_id.get(official.match_id)
        if pm_game is None:
            warnings.append(f"{universe} KS matched official schedule but no PM market: {ks_game.event_ticker}")
            continue

        pm_official_a = official_team_for_platform_name(official, pm_game.outcome_a)
        pm_official_b = official_team_for_platform_name(official, pm_game.outcome_b)
        if pm_official_a is None or pm_official_b is None or pm_official_a.id == pm_official_b.id:
            warnings.append(f"{universe} PM team direction not mappable through official schedule: {pm_game.event_slug}")
            continue

        pm_tokens = {
            pm_official_a.id: (pm_game.outcome_a, pm_game.token_a),
            pm_official_b.id: (pm_game.outcome_b, pm_game.token_b),
        }
        for market in ks_game.markets:
            official_team = official_team_for_platform_name(official, market.yes_outcome)
            if official_team is None:
                warnings.append(f"{universe} KS team direction not mappable through official schedule: {ks_game.event_ticker}")
                continue
            pm_match = pm_tokens.get(official_team.id)
            if pm_match is None:
                warnings.append(f"{universe} PM/KS team direction mismatch: {ks_game.event_ticker} {market.yes_outcome}")
                continue
            pm_yes, pm_token = pm_match
            pairs.append(
                core.PairedContract(
                    universe=universe,
                    category="esports",
                    match_name=official.matchup,
                    event_date=official.event_date,
                    canonical_event_id=f"{universe}:{official.match_id}",
                    market_type="match_winner",
                    pm_yes_outcome=pm_yes,
                    pm_event_slug=pm_game.event_slug,
                    pm_market_id=pm_game.market_id,
                    pm_token_id=pm_token,
                    ks_yes_outcome=market.yes_outcome,
                    ks_event_ticker=ks_game.event_ticker,
                    ks_market_ticker=market.ticker,
                    match_format=official.match_format,
                    schedule_source=official.source,
                )
            )
    return pairs, warnings


def fetch_official_esports_schedule(universe: str, from_date: str | None) -> tuple[list[OfficialEsportsMatch], list[str]]:
    if universe == "lol":
        url = RIOT_LOL_ESPORTS_URL
        source = "riot_lolesports_official_ssr"
    elif universe == "valorant":
        url = RIOT_VALORANT_ESPORTS_URL
        source = "riot_valorantesports_official_ssr"
    else:
        raise ValueError(f"unsupported official esports schedule universe: {universe}")

    html = core.get_text(url)
    matches: list[OfficialEsportsMatch] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for event in extract_riot_event_matches(html):
        parsed = official_esports_match_from_event(universe, event, source)
        if parsed is None:
            continue
        if from_date and parsed.event_date < from_date:
            continue
        if parsed.match_id in seen_ids:
            continue
        seen_ids.add(parsed.match_id)
        matches.append(parsed)
    matches.sort(key=lambda match: (match.event_date, match.start_time_utc, match.match_id))
    if not matches:
        warnings.append(f"{universe} official Riot schedule returned no non-TBD matches from {from_date or 'all dates'}")
    return matches, warnings


def extract_riot_event_matches(html: str) -> list[dict[str, Any]]:
    marker = '{"__typename":"EventMatch"'
    events: list[dict[str, Any]] = []
    start = 0
    while True:
        index = html.find(marker, start)
        if index < 0:
            break
        end = balanced_json_object_end(html, index)
        if end is None:
            break
        raw = html[index:end]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            start = index + len(marker)
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
        start = end
    return events


def balanced_json_object_end(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def official_esports_match_from_event(
    universe: str,
    event: dict[str, Any],
    source: str,
) -> OfficialEsportsMatch | None:
    if event.get("type") != "match":
        return None
    start_time = str(event.get("startTime") or "")
    event_date = iso_utc_date(start_time)
    teams = event.get("matchTeams") or []
    if not event_date or len(teams) != 2:
        return None
    parsed_teams = tuple(official_esports_team_from_payload(team) for team in teams)
    if len(parsed_teams) != 2 or any(team is None for team in parsed_teams):
        return None
    official_teams = tuple(team for team in parsed_teams if team is not None)
    if any(normalize_team(team.name) == "tbd" or normalize_team(team.code) == "tbd" for team in official_teams):
        return None
    strategy = ((event.get("match") or {}).get("strategy") or {})
    match_format = f"BO{strategy.get('count')}" if strategy.get("type") == "bestOf" and strategy.get("count") else ""
    league = event.get("league") or {}
    tournament = event.get("tournament") or {}
    return OfficialEsportsMatch(
        universe=universe,
        match_id=str(event.get("id") or (event.get("match") or {}).get("id") or ""),
        event_date=event_date,
        start_time_utc=start_time,
        league_name=str(league.get("name") or ""),
        tournament_name=str(tournament.get("name") or ""),
        block_name=str(event.get("blockName") or ""),
        match_format=match_format,
        source=source,
        teams=official_teams,  # type: ignore[arg-type]
    )


def official_esports_team_from_payload(team: dict[str, Any]) -> OfficialEsportsTeam | None:
    team_id = str(team.get("id") or "")
    name = str(team.get("name") or "")
    code = str(team.get("code") or "")
    if not team_id or not name:
        return None
    return OfficialEsportsTeam(id=team_id, name=name, code=code)


def iso_utc_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return ""


def find_official_esports_match(
    matches: list[OfficialEsportsMatch],
    event_date: str,
    team_a: str,
    team_b: str,
    match_format: str,
) -> OfficialEsportsMatch | None:
    candidates = []
    for official in matches:
        if official.event_date != event_date:
            continue
        if match_format and official.match_format and official.match_format != match_format:
            continue
        team_one = official_team_for_platform_name(official, team_a)
        team_two = official_team_for_platform_name(official, team_b)
        if team_one is None or team_two is None or team_one.id == team_two.id:
            continue
        candidates.append(official)
    return candidates[0] if len(candidates) == 1 else None


def official_team_for_platform_name(match: OfficialEsportsMatch, platform_name: str) -> OfficialEsportsTeam | None:
    normalized = normalize_team(platform_name)
    for team in match.teams:
        if normalized in official_team_aliases(team):
            return team
    return None


def official_team_aliases(team: OfficialEsportsTeam) -> set[str]:
    aliases = {normalize_team(team.name)}
    if team.code:
        code = normalize_team(team.code)
        aliases.update({code, f"{code}gaming", f"{code}esports", f"{code}academy"})
    words = re.split(r"\s+", team.name.strip())
    variants = {team.name}
    suffixes = (" Esports", " Gaming", " Academy", " Global Academy")
    for suffix in suffixes:
        if team.name.endswith(suffix):
            variants.add(team.name[: -len(suffix)])
        else:
            variants.add(f"{team.name}{suffix}")
    for sponsor_prefix in ("VISA ", "KIWOOM "):
        if team.name.startswith(sponsor_prefix):
            variants.add(team.name[len(sponsor_prefix) :])
    variants.add(team.name.replace(" Esports Academy", " Academy"))
    if len(words) > 1:
        variants.add(" ".join(words[:-1]))
        first_word = words[0]
        if normalize_plain(first_word) not in {"team", "visa", "kiwoom"}:
            variants.update(
                {
                    first_word,
                    f"{first_word} Team",
                    f"{first_word} Gaming",
                    f"{first_word} Esports",
                    f"{first_word} Academy",
                }
            )
    aliases.update(normalize_team(variant) for variant in variants if variant)
    return aliases


def build_worldcup_soccer_rows(pm_limit: int, ks_limit: int) -> list[dict[str, Any]]:
    ts_utc = datetime.now(timezone.utc).isoformat()
    return build_pm_soccer_rows(ts_utc, pm_limit) + build_ks_worldcup_rows(ts_utc, ks_limit)


def build_pm_soccer_rows(ts_utc: str, limit: int) -> list[dict[str, Any]]:
    rows = []
    for event in core.fetch_polymarket_events(("soccer",), limit):
        slug = str(event.get("slug") or "")
        match = PM_WORLDCUP_GAME_RE.match(slug)
        if not match:
            continue
        markets = active_pm_markets(event)
        labels = pm_soccer_labels(markets)
        tie = has_tie(labels)
        binary = len(markets) == 2 and not tie
        rows.append(
            {
                "ts_utc": ts_utc,
                "source": "polymarket",
                "universe": "worldcup_soccer",
                "event_id": slug,
                "title": str(event.get("title") or ""),
                "event_date": match.group(1),
                "market_count": len(markets),
                "outcomes": " | ".join(labels),
                "is_binary_candidate": binary,
                "skip_binary_arb": not binary,
                "reason": soccer_skip_reason(len(markets), tie),
            }
        )
    rows.sort(key=lambda row: (row["event_date"], row["event_id"]))
    return rows


def build_ks_worldcup_rows(ts_utc: str, limit: int) -> list[dict[str, Any]]:
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    titles: dict[str, str] = {}
    raw_markets = core.get_json(
        core.KALSHI_API,
        "/markets",
        {"series_ticker": "KXWCGAME", "status": "open", "limit": min(max(limit, 1), 200)},
    ).get("markets", [])
    for market in raw_markets:
        event_ticker = str(market.get("event_ticker") or "")
        if not event_ticker:
            continue
        by_event[event_ticker].append(market)
        titles[event_ticker] = clean_ks_title(str(market.get("title") or ""))

    rows = []
    for event_ticker, markets in by_event.items():
        labels = [str(market.get("yes_sub_title") or "") for market in markets]
        tie = has_tie(labels)
        binary = len(markets) == 2 and not tie
        rows.append(
            {
                "ts_utc": ts_utc,
                "source": "kalshi",
                "universe": "worldcup_soccer",
                "event_id": event_ticker,
                "title": titles.get(event_ticker, ""),
                "event_date": "",
                "market_count": len(markets),
                "outcomes": " | ".join(label for label in labels if label),
                "is_binary_candidate": binary,
                "skip_binary_arb": not binary,
                "reason": soccer_skip_reason(len(markets), tie),
            }
        )
    rows.sort(key=lambda row: row["event_id"])
    return rows


def parse_mlb_ks_date(event_ticker: str) -> str | None:
    match = MLB_KS_EVENT_RE.match(event_ticker)
    if not match:
        return None
    return date_from_yy_mon_day(match.group(1), match.group(2), match.group(3))


def parse_esports_ks_date(event_ticker: str) -> str | None:
    match = ESPORTS_KS_DATE_RE.match(event_ticker)
    if not match:
        return None
    return date_from_yy_mon_day(match.group(1), match.group(2), match.group(3))


def date_from_yy_mon_day(year_s: str, month_s: str, day_s: str) -> str | None:
    month = MONTHS.get(month_s)
    if month is None:
        return None
    try:
        return date(2000 + int(year_s), month, int(day_s)).isoformat()
    except ValueError:
        return None


def parse_slug_date(slug: str) -> str:
    match = SLUG_DATE_RE.search(slug)
    return match.group(1) if match else ""


def parse_bo_format(text: str) -> str:
    match = BO_FORMAT_RE.search(text)
    return match.group(1).upper() if match else ""


def parse_kalshi_esports_title_teams(title: str, markets: list[core.KSMarket]) -> tuple[str, str]:
    match = KALSHI_ESPORTS_TITLE_RE.search(title)
    if match:
        return match.group("team_a"), match.group("team_b")
    return markets[0].yes_outcome, markets[1].yes_outcome


def market_team_abbr(market_ticker: str) -> str:
    return market_ticker.rsplit("-", 1)[-1].upper()


def normalize_plain(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def normalize_team(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    compact = re.sub(r"[^a-z0-9]+", "", folded.lower().replace("&", " and "))
    return TEAM_ALIASES.get(compact, compact)


def team_key(team_a: str, team_b: str) -> frozenset[str]:
    return frozenset({normalize_team(team_a), normalize_team(team_b)})


def canonical_esports_id(universe: str, event_date: str, team_a: str, team_b: str) -> str:
    teams = sorted(team_key(team_a, team_b))
    return f"{universe}:{event_date}:{teams[0]}:{teams[1]}"


def clean_ks_title(title: str) -> str:
    return title.removesuffix(" Winner?")


def active_pm_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [market for market in event.get("markets") or [] if market.get("active") and not market.get("closed")]


def pm_soccer_labels(markets: list[dict[str, Any]]) -> list[str]:
    labels = []
    for market in markets:
        outcomes = core.parse_json_list(market.get("outcomes"))
        if len(outcomes) == 2 and outcomes != ["Yes", "No"]:
            labels.extend(str(outcome) for outcome in outcomes)
        else:
            labels.append(str(market.get("question") or market.get("groupItemTitle") or ""))
    return [label for label in labels if label]


def has_tie(labels: list[str]) -> bool:
    return any(label.strip().lower() in {"tie", "draw"} or " tie" in label.lower() or " draw" in label.lower() for label in labels)


def soccer_skip_reason(market_count: int, tie: bool) -> str:
    if tie:
        return "3-way market includes Tie/draw; binary Yes/No arb formula does not apply."
    if market_count != 2:
        return "Market is not a 2-outcome binary event."
    return ""
