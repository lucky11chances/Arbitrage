from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class NbaTeam:
    id: int
    name: str
    abbreviation: str
    aliases: tuple[str, ...]


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


TEAMS = [
    NbaTeam(1610612737, "Atlanta Hawks", "ATL", ("atlanta", "hawks", "atlanta hawks", "atl")),
    NbaTeam(1610612738, "Boston Celtics", "BOS", ("boston", "celtics", "boston celtics", "bos")),
    NbaTeam(1610612751, "Brooklyn Nets", "BKN", ("brooklyn", "nets", "brooklyn nets", "bkn")),
    NbaTeam(1610612766, "Charlotte Hornets", "CHA", ("charlotte", "hornets", "charlotte hornets", "cha")),
    NbaTeam(1610612741, "Chicago Bulls", "CHI", ("chicago", "bulls", "chicago bulls", "chi")),
    NbaTeam(1610612739, "Cleveland Cavaliers", "CLE", ("cleveland", "cavaliers", "cleveland cavaliers", "cavs", "cle")),
    NbaTeam(1610612742, "Dallas Mavericks", "DAL", ("dallas", "mavericks", "dallas mavericks", "mavs", "dal")),
    NbaTeam(1610612743, "Denver Nuggets", "DEN", ("denver", "nuggets", "denver nuggets", "den")),
    NbaTeam(1610612765, "Detroit Pistons", "DET", ("detroit", "pistons", "detroit pistons", "det")),
    NbaTeam(1610612744, "Golden State Warriors", "GSW", ("golden state", "warriors", "golden state warriors", "gsw", "gs")),
    NbaTeam(1610612745, "Houston Rockets", "HOU", ("houston", "rockets", "houston rockets", "hou")),
    NbaTeam(1610612754, "Indiana Pacers", "IND", ("indiana", "pacers", "indiana pacers", "ind")),
    NbaTeam(1610612746, "LA Clippers", "LAC", ("la clippers", "los angeles clippers", "clippers", "lac")),
    NbaTeam(1610612747, "Los Angeles Lakers", "LAL", ("los angeles lakers", "la lakers", "lakers", "lal")),
    NbaTeam(1610612763, "Memphis Grizzlies", "MEM", ("memphis", "grizzlies", "memphis grizzlies", "mem")),
    NbaTeam(1610612748, "Miami Heat", "MIA", ("miami", "heat", "miami heat", "mia")),
    NbaTeam(1610612749, "Milwaukee Bucks", "MIL", ("milwaukee", "bucks", "milwaukee bucks", "mil")),
    NbaTeam(1610612750, "Minnesota Timberwolves", "MIN", ("minnesota", "timberwolves", "wolves", "minnesota timberwolves", "min")),
    NbaTeam(1610612740, "New Orleans Pelicans", "NOP", ("new orleans", "pelicans", "new orleans pelicans", "nop", "no")),
    NbaTeam(1610612752, "New York Knicks", "NYK", ("new york", "new york knicks", "knicks", "nyk", "ny")),
    NbaTeam(1610612760, "Oklahoma City Thunder", "OKC", ("oklahoma city", "thunder", "oklahoma city thunder", "okc")),
    NbaTeam(1610612753, "Orlando Magic", "ORL", ("orlando", "magic", "orlando magic", "orl")),
    NbaTeam(1610612755, "Philadelphia 76ers", "PHI", ("philadelphia", "76ers", "sixers", "philadelphia 76ers", "phi")),
    NbaTeam(1610612756, "Phoenix Suns", "PHX", ("phoenix", "suns", "phoenix suns", "phx")),
    NbaTeam(1610612757, "Portland Trail Blazers", "POR", ("portland", "trail blazers", "blazers", "portland trail blazers", "por")),
    NbaTeam(1610612758, "Sacramento Kings", "SAC", ("sacramento", "kings", "sacramento kings", "sac")),
    NbaTeam(1610612759, "San Antonio Spurs", "SAS", ("san antonio", "spurs", "san antonio spurs", "sas", "sa")),
    NbaTeam(1610612761, "Toronto Raptors", "TOR", ("toronto", "raptors", "toronto raptors", "tor")),
    NbaTeam(1610612762, "Utah Jazz", "UTA", ("utah", "jazz", "utah jazz", "uta")),
    NbaTeam(1610612764, "Washington Wizards", "WAS", ("washington", "wizards", "washington wizards", "was", "wsh")),
]

TEAM_BY_ABBR = {team.abbreviation: team for team in TEAMS}
TEAM_BY_ALIAS = {
    normalize_name(alias): team
    for team in TEAMS
    for alias in (team.name, team.abbreviation, *team.aliases)
}


def team_from_name(name: str) -> NbaTeam | None:
    return TEAM_BY_ALIAS.get(normalize_name(name))


def team_from_abbr(abbr: str) -> NbaTeam | None:
    upper = abbr.upper()
    if upper == "NY":
        upper = "NYK"
    if upper == "SA":
        upper = "SAS"
    if upper == "NO":
        upper = "NOP"
    return TEAM_BY_ABBR.get(upper)


def parse_kalshi_event_date(event_ticker: str) -> str | None:
    match = re.match(r"^KXNBAGAME-(\d{2})([A-Z]{3})(\d{2})\d{4}[A-Z]+$", event_ticker)
    if not match:
        return None
    months = {
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
    month = months.get(match.group(2))
    if month is None:
        return None
    return date(2000 + int(match.group(1)), month, int(match.group(3))).isoformat()


def market_team_abbr(market_ticker: str) -> str:
    return market_ticker.rsplit("-", 1)[-1].upper()
