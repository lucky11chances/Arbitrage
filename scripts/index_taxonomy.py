from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import index_event_index_db
import index_pipeline_core as core


@dataclass(frozen=True)
class IndexSport:
    sport_key: str
    display_name: str
    status: str = "active"
    notes: str = ""


@dataclass(frozen=True)
class IndexCompetition:
    sport_key: str
    competition_key: str
    display_name: str
    country: str = ""
    gender: str = "open"
    level: str = ""
    status: str = "active"
    notes: str = ""


@dataclass(frozen=True)
class PlatformCategory:
    platform: str
    platform_key_type: str
    platform_key: str
    display_name: str = ""
    parent_key: str = ""
    observed_event_count: int = 0
    active_event_count: int = 0
    raw_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class PlatformCategoryMap:
    platform: str
    platform_key_type: str
    platform_key: str
    sport_key: str
    competition_key: str
    mapping_status: str
    confidence: str
    notes: str = ""


@dataclass(frozen=True)
class CompetitionSource:
    sport_key: str
    competition_key: str
    source_key: str
    source_type: str
    source_url: str = ""
    requires_api_key: bool = False
    confidence: str = ""
    source_status: str = ""
    pricing_summary: str = ""
    notes: str = ""


LOCAL_SPORTS: tuple[IndexSport, ...] = (
    IndexSport("baseball", "Baseball"),
    IndexSport("basketball", "Basketball"),
    IndexSport("boxing", "Boxing"),
    IndexSport("cricket", "Cricket"),
    IndexSport("esports", "Esports"),
    IndexSport("football", "Football"),
    IndexSport("golf", "Golf"),
    IndexSport("hockey", "Hockey"),
    IndexSport("lacrosse", "Lacrosse"),
    IndexSport("mma", "MMA"),
    IndexSport("motor_sports", "Motorsports"),
    IndexSport("pickleball", "Pickleball"),
    IndexSport("rugby", "Rugby"),
    IndexSport("soccer", "Soccer"),
    IndexSport("table_tennis", "Table Tennis"),
    IndexSport("tennis", "Tennis"),
)


LOCAL_COMPETITIONS: tuple[IndexCompetition, ...] = (
    IndexCompetition("baseball", "mlb", "MLB", "US/CA", "men", "pro", "active", "Official source: MLB Stats API."),
    IndexCompetition("baseball", "kbo", "KBO", "KR", "men", "pro", "active", "PM has KBO tags; event source pending."),
    IndexCompetition("baseball", "ncaa_baseball", "NCAA Baseball", "US", "men", "college", "active", "Candidate paid/free providers pending."),
    IndexCompetition("baseball", "generic_baseball", "Generic Baseball", "", "open", "generic", "active", "Broad platform bucket; not enough for event-level identity."),
    IndexCompetition("basketball", "nba", "NBA", "US/CA", "men", "pro", "active", "Current source: ESPN scoreboard until official/free source is added."),
    IndexCompetition("basketball", "wnba", "WNBA", "US", "women", "pro", "active", "Current source: ESPN scoreboard until official/free source is added."),
    IndexCompetition("basketball", "ncaa_mbb", "NCAA Men's Basketball", "US", "men", "college", "active", "Kalshi series exists; source pending."),
    IndexCompetition("basketball", "ncaa_wbb", "NCAA Women's Basketball", "US", "women", "college", "active", "Source pending."),
    IndexCompetition("basketball", "euroleague", "EuroLeague", "EU", "men", "pro", "active", "Source pending."),
    IndexCompetition("basketball", "generic_basketball", "Generic Basketball", "", "open", "generic", "active", "Broad platform bucket; not enough for event-level identity."),
    IndexCompetition("boxing", "boxing", "Boxing", "", "open", "pro", "active", "Bout-card source pending."),
    IndexCompetition("cricket", "international_cricket", "International Cricket", "", "men", "international", "active", "PM tag observed; source pending."),
    IndexCompetition("cricket", "ipl", "IPL", "IN", "men", "pro", "active", "Source pending."),
    IndexCompetition("cricket", "t20_blast", "T20 Blast", "GB", "men", "pro", "active", "PM tag observed; source pending."),
    IndexCompetition("cricket", "generic_cricket", "Generic Cricket", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("esports", "lol", "League of Legends", "", "open", "pro", "active", "Official Riot site source configured."),
    IndexCompetition("esports", "valorant", "Valorant", "", "open", "pro", "active", "Official Riot site source configured."),
    IndexCompetition("esports", "cs2", "Counter-Strike 2", "", "open", "pro", "active", "PandaScore candidate; official/free source pending."),
    IndexCompetition("esports", "dota2", "Dota 2", "", "open", "pro", "active", "PandaScore candidate; source pending."),
    IndexCompetition("esports", "generic_esports", "Generic Esports", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("football", "nfl", "NFL", "US", "men", "pro", "active", "Kalshi series exists; source pending."),
    IndexCompetition("football", "ncaa_football", "NCAA Football", "US", "men", "college", "active", "Kalshi series exists; source pending."),
    IndexCompetition("football", "cfl", "CFL", "CA", "men", "pro", "active", "Source pending."),
    IndexCompetition("football", "generic_football", "Generic Football", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("golf", "pga", "PGA", "US", "men", "pro", "active", "Tournament source pending."),
    IndexCompetition("golf", "generic_golf", "Generic Golf", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("hockey", "nhl", "NHL", "US/CA", "men", "pro", "active", "Source pending."),
    IndexCompetition("hockey", "generic_hockey", "Generic Hockey", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("lacrosse", "pll", "PLL", "US", "men", "pro", "active", "Source pending."),
    IndexCompetition("lacrosse", "wll", "Women's Lacrosse League", "US", "women", "pro", "active", "PM tag observed; source pending."),
    IndexCompetition("lacrosse", "generic_lacrosse", "Generic Lacrosse", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("mma", "ufc", "UFC", "", "open", "pro", "active", "Bout-card source pending."),
    IndexCompetition("mma", "mma", "MMA", "", "open", "pro", "active", "Generic MMA bucket; source pending."),
    IndexCompetition("motor_sports", "f1", "Formula 1", "", "open", "pro", "active", "Race calendar source pending."),
    IndexCompetition("motor_sports", "indycar", "IndyCar", "US", "open", "pro", "active", "PM tag observed; source pending."),
    IndexCompetition("motor_sports", "generic_motorsports", "Generic Motorsports", "", "open", "generic", "active", "PM motorsports/racing tags observed."),
    IndexCompetition("pickleball", "generic_pickleball", "Generic Pickleball", "", "open", "generic", "active", "Source pending."),
    IndexCompetition("rugby", "generic_rugby", "Generic Rugby", "", "open", "generic", "active", "Source pending."),
    IndexCompetition("soccer", "world_cup", "FIFA World Cup", "", "men", "international", "active", "Static local FIFA fixture source configured."),
    IndexCompetition("soccer", "mls", "MLS", "US/CA", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "epl", "English Premier League", "GB", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "ucl", "UEFA Champions League", "EU", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "la_liga", "La Liga", "ES", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "serie_a", "Serie A", "IT", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "bundesliga", "Bundesliga", "DE", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "ligue_1", "Ligue 1", "FR", "men", "pro", "active", "Candidate soccer providers pending."),
    IndexCompetition("soccer", "generic_soccer", "Generic Soccer", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("table_tennis", "wtt_mens_singles", "WTT Men's Singles", "", "men", "pro", "active", "Source pending."),
    IndexCompetition("table_tennis", "wtt_womens_singles", "WTT Women's Singles", "", "women", "pro", "active", "Source pending."),
    IndexCompetition("table_tennis", "generic_table_tennis", "Generic Table Tennis", "", "open", "generic", "active", "Broad platform bucket."),
    IndexCompetition("tennis", "atp", "ATP", "", "men", "pro", "active", "Source pending."),
    IndexCompetition("tennis", "atp_challenger", "ATP Challenger", "", "men", "pro", "active", "Source pending."),
    IndexCompetition("tennis", "wta", "WTA", "", "women", "pro", "active", "Source pending."),
    IndexCompetition("tennis", "itf_men", "ITF Men", "", "men", "pro", "active", "Source pending."),
    IndexCompetition("tennis", "itf_women", "ITF Women", "", "women", "pro", "active", "Source pending."),
    IndexCompetition("tennis", "generic_tennis", "Generic Tennis", "", "open", "generic", "active", "Broad platform bucket."),
)


PM_PLATFORM_CATEGORIES: tuple[PlatformCategory, ...] = (
    PlatformCategory("pm", "tag_slug", "baseball", "Baseball"),
    PlatformCategory("pm", "tag_slug", "mlb", "MLB", "baseball"),
    PlatformCategory("pm", "series_slug", "mlb", "MLB", "baseball"),
    PlatformCategory("pm", "tag_slug", "kbo", "KBO", "baseball"),
    PlatformCategory("pm", "series_slug", "kbo", "KBO", "baseball"),
    PlatformCategory("pm", "tag_slug", "basketball", "Basketball"),
    PlatformCategory("pm", "tag_slug", "nba", "NBA", "basketball"),
    PlatformCategory("pm", "tag_slug", "wnba", "WNBA", "basketball"),
    PlatformCategory("pm", "series_slug", "wnba", "WNBA", "basketball"),
    PlatformCategory("pm", "tag_slug", "football", "Football"),
    PlatformCategory("pm", "tag_slug", "nfl", "NFL", "football"),
    PlatformCategory("pm", "tag_slug", "cfb", "College Football", "football"),
    PlatformCategory("pm", "tag_slug", "cfl", "CFL", "football"),
    PlatformCategory("pm", "tag_slug", "soccer", "Soccer"),
    PlatformCategory("pm", "tag_slug", "world-cup", "World Cup", "soccer"),
    PlatformCategory("pm", "tag_slug", "fifa-world-cup", "FIFA World Cup", "soccer"),
    PlatformCategory("pm", "tag_slug", "2026-fifa-world-cup", "2026 FIFA World Cup", "soccer"),
    PlatformCategory("pm", "tag_slug", "tennis", "Tennis"),
    PlatformCategory("pm", "series_slug", "atp", "ATP", "tennis"),
    PlatformCategory("pm", "series_slug", "wta", "WTA", "tennis"),
    PlatformCategory("pm", "series_slug", "itf", "ITF", "tennis"),
    PlatformCategory("pm", "tag_slug", "cricket", "Cricket"),
    PlatformCategory("pm", "tag_slug", "international-cricket", "International Cricket", "cricket"),
    PlatformCategory("pm", "series_slug", "international-cricket", "International Cricket", "cricket"),
    PlatformCategory("pm", "tag_slug", "t20-blast", "T20 Blast", "cricket"),
    PlatformCategory("pm", "series_slug", "t20-blast", "T20 Blast", "cricket"),
    PlatformCategory("pm", "tag_slug", "esports", "Esports"),
    PlatformCategory("pm", "tag_slug", "counter-strike-2", "Counter-Strike 2", "esports"),
    PlatformCategory("pm", "tag_slug", "counter-strike", "Counter-Strike", "esports"),
    PlatformCategory("pm", "series_slug", "counter-strike", "Counter-Strike", "esports"),
    PlatformCategory("pm", "tag_slug", "valorant", "Valorant", "esports"),
    PlatformCategory("pm", "series_slug", "valorant", "Valorant", "esports"),
    PlatformCategory("pm", "tag_slug", "lol", "LoL", "esports"),
    PlatformCategory("pm", "tag_slug", "league-of-legends", "League of Legends", "esports"),
    PlatformCategory("pm", "tag_slug", "ufc", "UFC", "mma"),
    PlatformCategory("pm", "tag_slug", "boxing", "Boxing", "boxing"),
    PlatformCategory("pm", "tag_slug", "formula1", "Formula 1", "motor_sports"),
    PlatformCategory("pm", "tag_slug", "f1", "Formula 1", "motor_sports"),
    PlatformCategory("pm", "tag_slug", "grand-prix", "Grand Prix", "motor_sports"),
    PlatformCategory("pm", "tag_slug", "motorsports", "Motorsports", "motor_sports"),
    PlatformCategory("pm", "tag_slug", "racing", "Racing", "motor_sports"),
    PlatformCategory("pm", "tag_slug", "indycar", "IndyCar", "motor_sports"),
    PlatformCategory("pm", "tag_slug", "table-tennis", "Table Tennis", "table_tennis"),
    PlatformCategory("pm", "tag_slug", "ping-pong", "Ping Pong", "table_tennis"),
    PlatformCategory("pm", "series_slug", "wtt-mens-singles", "WTT Men's Singles", "table_tennis"),
    PlatformCategory("pm", "series_slug", "wtt-womens-singles", "WTT Women's Singles", "table_tennis"),
    PlatformCategory("pm", "tag_slug", "wttms", "WTT Men's Singles", "table_tennis"),
    PlatformCategory("pm", "tag_slug", "wttws", "WTT Women's Singles", "table_tennis"),
    PlatformCategory("pm", "tag_slug", "hockey", "Hockey"),
    PlatformCategory("pm", "tag_slug", "nhl", "NHL", "hockey"),
    PlatformCategory("pm", "tag_slug", "ice-hockey", "Ice Hockey", "hockey"),
    PlatformCategory("pm", "tag_slug", "golf", "Golf"),
    PlatformCategory("pm", "tag_slug", "pga", "PGA", "golf"),
    PlatformCategory("pm", "tag_slug", "pga-tour", "PGA Tour", "golf"),
    PlatformCategory("pm", "series_slug", "pga-tour", "PGA Tour", "golf"),
    PlatformCategory("pm", "tag_slug", "liv-golf", "LIV Golf", "golf"),
    PlatformCategory("pm", "tag_slug", "rugby", "Rugby"),
    PlatformCategory("pm", "tag_slug", "top-14", "Top 14 Rugby", "rugby"),
    PlatformCategory("pm", "tag_slug", "rugby-top-14", "Top 14 Rugby", "rugby"),
    PlatformCategory("pm", "series_slug", "rugby-top-14", "Top 14 Rugby", "rugby"),
    PlatformCategory("pm", "tag_slug", "pickleball", "Pickleball"),
    PlatformCategory("pm", "tag_slug", "major-league-pickleball", "Major League Pickleball", "pickleball"),
    PlatformCategory("pm", "tag_slug", "mlp", "Major League Pickleball", "pickleball"),
    PlatformCategory("pm", "series_slug", "mlp", "Major League Pickleball", "pickleball"),
    PlatformCategory("pm", "tag_slug", "lacrosse", "Lacrosse"),
    PlatformCategory("pm", "tag_slug", "pll", "PLL", "lacrosse"),
    PlatformCategory("pm", "series_slug", "pll", "PLL", "lacrosse"),
    PlatformCategory("pm", "tag_slug", "premier-lacrosse-league", "PLL", "lacrosse"),
    PlatformCategory("pm", "tag_slug", "wll", "Women's Lacrosse League", "lacrosse"),
    PlatformCategory("pm", "series_slug", "wll", "Women's Lacrosse League", "lacrosse"),
    PlatformCategory("pm", "tag_slug", "womens-lacrosse-league", "Women's Lacrosse League", "lacrosse"),
)


KS_PLATFORM_CATEGORIES: tuple[PlatformCategory, ...] = (
    PlatformCategory("ks", "series_ticker", "KXMLBGAME", "MLB Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXBASEBALLGAME", "Baseball Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXNBAGAME", "NBA Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXWNBAGAME", "WNBA Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXWNBA", "WNBA", "sports"),
    PlatformCategory("ks", "series_ticker", "KXNCAABGAME", "NCAA Basketball Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXNFLGAME", "NFL Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXNCAAFGAME", "NCAA Football Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXCFBGAME", "College Football Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXWCGAME", "World Cup Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXSOCCERGAME", "Soccer Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXATPMATCH", "ATP Matches", "sports"),
    PlatformCategory("ks", "series_ticker", "KXATPCHALLENGERMATCH", "ATP Challenger Matches", "sports"),
    PlatformCategory("ks", "series_ticker", "KXWTAMATCH", "WTA Matches", "sports"),
    PlatformCategory("ks", "series_ticker", "KXITFMATCH", "ITF Men's Matches", "sports"),
    PlatformCategory("ks", "series_ticker", "KXITFWMATCH", "ITF Women's Matches", "sports"),
    PlatformCategory("ks", "series_ticker", "KXCRICKET", "Cricket", "sports"),
    PlatformCategory("ks", "series_ticker", "KXCRICKETGAME", "Cricket Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXLOLGAME", "LoL Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXVALORANTGAME", "Valorant Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXCS2GAME", "CS2 Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXUFCFIGHT", "UFC Fights", "sports"),
    PlatformCategory("ks", "series_ticker", "KXBOXING", "Boxing", "sports"),
    PlatformCategory("ks", "series_ticker", "KXBOXINGGAME", "Boxing Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXF1", "Formula 1", "sports"),
    PlatformCategory("ks", "series_ticker", "KXNHLGAME", "NHL Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXGOLF", "Golf", "sports"),
    PlatformCategory("ks", "series_ticker", "KXRUGBY", "Rugby", "sports"),
    PlatformCategory("ks", "series_ticker", "KXRUGBYGAME", "Rugby Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXTTGAME", "Table Tennis Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXTABLETENNIS", "Table Tennis", "sports"),
    PlatformCategory("ks", "series_ticker", "KXPICKLEBALL", "Pickleball", "sports"),
    PlatformCategory("ks", "series_ticker", "KXPICKLEBALLGAME", "Pickleball Games", "sports"),
    PlatformCategory("ks", "series_ticker", "KXLACROSSE", "Lacrosse", "sports"),
    PlatformCategory("ks", "series_ticker", "KXLACROSSEGAME", "Lacrosse Games", "sports"),
)


PLATFORM_CATEGORY_MAPS: tuple[PlatformCategoryMap, ...] = (
    PlatformCategoryMap("pm", "tag_slug", "baseball", "baseball", "generic_baseball", "broad", "medium", "Broad PM baseball tag."),
    PlatformCategoryMap("pm", "tag_slug", "mlb", "baseball", "mlb", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "mlb", "baseball", "mlb", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "kbo", "baseball", "kbo", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "kbo", "baseball", "kbo", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "basketball", "basketball", "generic_basketball", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "nba", "basketball", "nba", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "wnba", "basketball", "wnba", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "wnba", "basketball", "wnba", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "football", "football", "generic_football", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "nfl", "football", "nfl", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "cfb", "football", "ncaa_football", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "cfl", "football", "cfl", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "soccer", "soccer", "generic_soccer", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "world-cup", "soccer", "world_cup", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "fifa-world-cup", "soccer", "world_cup", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "2026-fifa-world-cup", "soccer", "world_cup", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "tennis", "tennis", "generic_tennis", "broad", "medium"),
    PlatformCategoryMap("pm", "series_slug", "atp", "tennis", "atp", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "wta", "tennis", "wta", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "itf", "tennis", "generic_tennis", "broad", "medium", "PM ITF series does not split men/women here."),
    PlatformCategoryMap("pm", "tag_slug", "cricket", "cricket", "generic_cricket", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "international-cricket", "cricket", "international_cricket", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "international-cricket", "cricket", "international_cricket", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "t20-blast", "cricket", "t20_blast", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "t20-blast", "cricket", "t20_blast", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "esports", "esports", "generic_esports", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "counter-strike-2", "esports", "cs2", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "counter-strike", "esports", "cs2", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "counter-strike", "esports", "cs2", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "valorant", "esports", "valorant", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "valorant", "esports", "valorant", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "lol", "esports", "lol", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "league-of-legends", "esports", "lol", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "ufc", "mma", "ufc", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "boxing", "boxing", "boxing", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "formula1", "motor_sports", "f1", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "f1", "motor_sports", "f1", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "grand-prix", "motor_sports", "f1", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "motorsports", "motor_sports", "generic_motorsports", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "racing", "motor_sports", "generic_motorsports", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "indycar", "motor_sports", "indycar", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "table-tennis", "table_tennis", "generic_table_tennis", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "ping-pong", "table_tennis", "generic_table_tennis", "broad", "medium"),
    PlatformCategoryMap("pm", "series_slug", "wtt-mens-singles", "table_tennis", "wtt_mens_singles", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "wtt-womens-singles", "table_tennis", "wtt_womens_singles", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "wttms", "table_tennis", "wtt_mens_singles", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "wttws", "table_tennis", "wtt_womens_singles", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "hockey", "hockey", "generic_hockey", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "nhl", "hockey", "nhl", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "ice-hockey", "hockey", "generic_hockey", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "golf", "golf", "generic_golf", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "pga", "golf", "pga", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "pga-tour", "golf", "pga", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "pga-tour", "golf", "pga", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "liv-golf", "golf", "generic_golf", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "rugby", "rugby", "generic_rugby", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "top-14", "rugby", "generic_rugby", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "rugby-top-14", "rugby", "generic_rugby", "broad", "medium"),
    PlatformCategoryMap("pm", "series_slug", "rugby-top-14", "rugby", "generic_rugby", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "pickleball", "pickleball", "generic_pickleball", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "major-league-pickleball", "pickleball", "generic_pickleball", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "mlp", "pickleball", "generic_pickleball", "broad", "medium"),
    PlatformCategoryMap("pm", "series_slug", "mlp", "pickleball", "generic_pickleball", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "lacrosse", "lacrosse", "generic_lacrosse", "broad", "medium"),
    PlatformCategoryMap("pm", "tag_slug", "pll", "lacrosse", "pll", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "pll", "lacrosse", "pll", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "premier-lacrosse-league", "lacrosse", "pll", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "wll", "lacrosse", "wll", "mapped", "high"),
    PlatformCategoryMap("pm", "series_slug", "wll", "lacrosse", "wll", "mapped", "high"),
    PlatformCategoryMap("pm", "tag_slug", "womens-lacrosse-league", "lacrosse", "wll", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXMLBGAME", "baseball", "mlb", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXBASEBALLGAME", "baseball", "generic_baseball", "ambiguous", "medium", "Generic baseball series may include non-MLB competitions."),
    PlatformCategoryMap("ks", "series_ticker", "KXNBAGAME", "basketball", "nba", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXWNBAGAME", "basketball", "wnba", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXWNBA", "basketball", "wnba", "mapped", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXNCAABGAME", "basketball", "ncaa_mbb", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXNFLGAME", "football", "nfl", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXNCAAFGAME", "football", "ncaa_football", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXCFBGAME", "football", "ncaa_football", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXWCGAME", "soccer", "world_cup", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXSOCCERGAME", "soccer", "generic_soccer", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXATPMATCH", "tennis", "atp", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXATPCHALLENGERMATCH", "tennis", "atp_challenger", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXWTAMATCH", "tennis", "wta", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXITFMATCH", "tennis", "itf_men", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXITFWMATCH", "tennis", "itf_women", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXCRICKET", "cricket", "generic_cricket", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXCRICKETGAME", "cricket", "generic_cricket", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXLOLGAME", "esports", "lol", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXVALORANTGAME", "esports", "valorant", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXCS2GAME", "esports", "cs2", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXUFCFIGHT", "mma", "ufc", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXBOXING", "boxing", "boxing", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXBOXINGGAME", "boxing", "boxing", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXF1", "motor_sports", "f1", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXNHLGAME", "hockey", "nhl", "mapped", "high"),
    PlatformCategoryMap("ks", "series_ticker", "KXGOLF", "golf", "generic_golf", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXRUGBY", "rugby", "generic_rugby", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXRUGBYGAME", "rugby", "generic_rugby", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXTTGAME", "table_tennis", "generic_table_tennis", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXTABLETENNIS", "table_tennis", "generic_table_tennis", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXPICKLEBALL", "pickleball", "generic_pickleball", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXPICKLEBALLGAME", "pickleball", "generic_pickleball", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXLACROSSE", "lacrosse", "generic_lacrosse", "ambiguous", "medium"),
    PlatformCategoryMap("ks", "series_ticker", "KXLACROSSEGAME", "lacrosse", "generic_lacrosse", "ambiguous", "medium"),
)


ENABLED_SOURCES: tuple[CompetitionSource, ...] = (
    CompetitionSource("baseball", "mlb", "mlb_stats_api", "official_api", "https://statsapi.mlb.com/api/v1/schedule", False, "high", "enabled", "", "Official MLB schedule API."),
    CompetitionSource("baseball", "kbo", "kbo_official_site", "official_site", "https://www.koreabaseball.com/Schedule/Schedule.aspx", False, "medium", "enabled", "", "KBO official schedule page; deterministic event IDs use date, time, teams, and venue."),
    CompetitionSource("basketball", "nba", "espn_nba_scoreboard", "vetted_public_api", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard", False, "medium", "enabled", "", "Public ESPN scoreboard until official/free source is added."),
    CompetitionSource("basketball", "wnba", "espn_wnba_scoreboard", "vetted_public_api", "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard", False, "medium", "enabled", "", "Public ESPN scoreboard until official/free source is added."),
    CompetitionSource("soccer", "world_cup", "fifa_world_cup_2026_local_schedule", "static_official_fixture", "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026", False, "high", "enabled", "", "Static local FIFA fixture table."),
    CompetitionSource("esports", "lol", "riot_lolesports_official_ssr", "official_site", "https://lolesports.com/en-US", False, "medium", "enabled", "", "Riot LoL official site schedule payload."),
    CompetitionSource("esports", "valorant", "riot_valorantesports_official_ssr", "official_site", "https://valorantesports.com/en-US", False, "medium", "enabled", "", "Riot Valorant official site schedule payload."),
    CompetitionSource("esports", "cs2", "pandascore_cs2_fixtures", "vetted_provider_api", "https://www.pandascore.co/pricing", True, "medium", "enabled_with_api_key", "Schedules/context tier is listed as free with 1000 requests/hour.", "PandaScore CS2 fixture/calendar source gated by INDEX_PANDASCORE_API_KEY."),
    CompetitionSource("tennis", "atp", "balldontlie_atp_matches", "paid_provider_api", "https://atp.balldontlie.io/", True, "medium", "enabled_with_api_key", "$9.99/mo per sport for match endpoint; 48-hour trial available.", "BALLDONTLIE ATP match source gated by INDEX_BALLDONTLIE_API_KEY."),
    CompetitionSource("tennis", "wta", "balldontlie_wta_matches", "paid_provider_api", "https://wta.balldontlie.io/", True, "medium", "enabled_with_api_key", "$9.99/mo per sport for match endpoint; 48-hour trial available.", "BALLDONTLIE WTA match source gated by INDEX_BALLDONTLIE_API_KEY."),
    CompetitionSource("mma", "ufc", "balldontlie_mma_fights", "paid_provider_api", "https://mma.balldontlie.io/", True, "medium", "enabled_with_api_key", "$9.99/mo for MMA fight endpoint; 48-hour trial available.", "BALLDONTLIE MMA fight source filtered to UFC."),
)


PAID_CANDIDATE_SOURCES: tuple[CompetitionSource, ...] = (
    CompetitionSource("baseball", "mlb", "balldontlie_mlb", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost broad provider candidate."),
    CompetitionSource("baseball", "kbo", "sportsdataio_sportradar_kbo", "enterprise_provider_api", "https://sportsdata.io/; https://sportradar.com/media-tech/data-content/sports-data-api/", True, "medium", "candidate_paid", "Sales/custom quote", "KBO schedule candidate; no accepted free official adapter is enabled."),
    CompetitionSource("baseball", "ncaa_baseball", "balldontlie_college_baseball", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost college baseball candidate."),
    CompetitionSource("basketball", "nba", "balldontlie_nba", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost broad provider candidate."),
    CompetitionSource("basketball", "wnba", "balldontlie_wnba", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost broad provider candidate."),
    CompetitionSource("basketball", "ncaa_mbb", "balldontlie_ncaamb", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost college basketball candidate."),
    CompetitionSource("basketball", "ncaa_wbb", "balldontlie_ncaaw", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost college basketball candidate."),
    CompetitionSource("football", "nfl", "balldontlie_nfl", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost football candidate."),
    CompetitionSource("football", "ncaa_football", "balldontlie_ncaaf", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Low-cost college football candidate."),
    CompetitionSource("esports", "cs2", "pandascore_cs2", "paid_provider_api", "https://www.pandascore.co/pricing", True, "medium", "candidate_paid", "Free schedules/context tier; historical/live paid tiers", "Esports-specialized source candidate."),
    CompetitionSource("esports", "lol", "pandascore_lol", "paid_provider_api", "https://www.pandascore.co/pricing", True, "medium", "candidate_paid", "Free schedules/context tier; historical/live paid tiers", "Esports fallback candidate."),
    CompetitionSource("esports", "valorant", "pandascore_valorant", "paid_provider_api", "https://www.pandascore.co/pricing", True, "medium", "candidate_paid", "Free schedules/context tier; historical/live paid tiers", "Esports fallback candidate."),
    CompetitionSource("esports", "dota2", "pandascore_dota2", "paid_provider_api", "https://www.pandascore.co/pricing", True, "medium", "candidate_paid", "Free schedules/context tier; historical/live paid tiers", "Esports-specialized source candidate."),
    CompetitionSource("soccer", "epl", "football_data_org_epl", "paid_provider_api", "https://www.football-data.org/pricing", True, "medium", "candidate_paid", "Free tier plus paid plans from EUR 12/mo", "Soccer schedule candidate."),
    CompetitionSource("soccer", "ucl", "football_data_org_ucl", "paid_provider_api", "https://www.football-data.org/pricing", True, "medium", "candidate_paid", "Free tier plus paid plans from EUR 12/mo", "Soccer schedule candidate."),
    CompetitionSource("soccer", "mls", "api_football_mls", "paid_provider_api", "https://www.api-football.com/pricing", True, "medium", "candidate_paid", "Free 100 requests/day; paid monthly tiers", "Soccer schedule candidate."),
    CompetitionSource("tennis", "atp", "balldontlie_atp", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Tennis candidate."),
    CompetitionSource("tennis", "atp_challenger", "sportsdataio_sportradar_atp_challenger", "enterprise_provider_api", "https://sportsdata.io/; https://sportradar.com/media-tech/data-content/sports-data-api/", True, "medium", "candidate_paid", "Sales/custom quote", "ATP Challenger schedule candidate; no accepted free official adapter is enabled."),
    CompetitionSource("tennis", "wta", "balldontlie_wta", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "Tennis candidate."),
    CompetitionSource("tennis", "itf_men", "sportsdataio_sportradar_itf_men", "enterprise_provider_api", "https://sportsdata.io/; https://sportradar.com/media-tech/data-content/sports-data-api/", True, "medium", "candidate_paid", "Sales/custom quote", "ITF men's schedule candidate; no accepted free official adapter is enabled."),
    CompetitionSource("tennis", "itf_women", "sportsdataio_sportradar_itf_women", "enterprise_provider_api", "https://sportsdata.io/; https://sportradar.com/media-tech/data-content/sports-data-api/", True, "medium", "candidate_paid", "Sales/custom quote", "ITF women's schedule candidate; no accepted free official adapter is enabled."),
    CompetitionSource("motor_sports", "f1", "balldontlie_f1", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "F1 candidate."),
    CompetitionSource("mma", "ufc", "balldontlie_mma", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "MMA/UFC candidate."),
    CompetitionSource("boxing", "boxing", "sportsdataio_sportradar_boxing", "enterprise_provider_api", "https://sportsdata.io/; https://sportradar.com/media-tech/data-content/sports-data-api/", True, "medium", "candidate_paid", "Sales/custom quote", "Boxing bout-card candidate; no accepted free official adapter is enabled."),
    CompetitionSource("hockey", "nhl", "balldontlie_nhl", "paid_provider_api", "https://www.balldontlie.io/", True, "medium", "candidate_paid", "$9.99-$39.99/mo per sport or $299.99/mo all sports", "NHL candidate."),
)


ENTERPRISE_CANDIDATE_SOURCES: tuple[CompetitionSource, ...] = tuple(
    CompetitionSource(
        competition.sport_key,
        competition.competition_key,
        "sportsdataio_or_sportradar_enterprise",
        "enterprise_provider_api",
        "https://sportsdata.io/; https://sportradar.com/media-tech/data-content/sports-data-api/",
        True,
        "high",
        "candidate_paid",
        "Sales/custom quote",
        "Enterprise-grade broad coverage candidate; not enabled by default.",
    )
    for competition in LOCAL_COMPETITIONS
    if competition.level != "generic"
)


def competition_key_set() -> set[tuple[str, str]]:
    return {(competition.sport_key, competition.competition_key) for competition in LOCAL_COMPETITIONS}


def source_gap_sources() -> tuple[CompetitionSource, ...]:
    enabled_keys = {
        (source.sport_key, source.competition_key)
        for source in ENABLED_SOURCES
        if source.source_status in {"enabled", "enabled_with_api_key"}
    }
    gaps: list[CompetitionSource] = []
    for competition in LOCAL_COMPETITIONS:
        key = (competition.sport_key, competition.competition_key)
        if key in enabled_keys:
            continue
        gaps.append(
            CompetitionSource(
                competition.sport_key,
                competition.competition_key,
                f"source_gap_{competition.sport_key}_{competition.competition_key}",
                "none",
                "",
                False,
                "none",
                "source_gap",
                "",
                "No accepted official/free event source is enabled for this competition.",
            )
        )
    return tuple(gaps)


def taxonomy_sources() -> tuple[CompetitionSource, ...]:
    return (*ENABLED_SOURCES, *PAID_CANDIDATE_SOURCES, *ENTERPRISE_CANDIDATE_SOURCES, *source_gap_sources())


def prune_stale_index_taxonomy(conn) -> dict[str, int]:
    allowed_sports = {sport.sport_key for sport in LOCAL_SPORTS}
    allowed_competitions = competition_key_set()
    allowed_categories = {
        (category.platform, category.platform_key_type, category.platform_key)
        for category in (*PM_PLATFORM_CATEGORIES, *KS_PLATFORM_CATEGORIES)
    }
    allowed_maps = {
        (mapping.platform, mapping.platform_key_type, mapping.platform_key, mapping.sport_key, mapping.competition_key)
        for mapping in PLATFORM_CATEGORY_MAPS
    }
    allowed_sources = {
        (source.sport_key, source.competition_key, source.source_key)
        for source in taxonomy_sources()
    }
    counts = {
        "pruned_sports": 0,
        "pruned_competitions": 0,
        "pruned_platform_categories": 0,
        "pruned_platform_maps": 0,
        "pruned_competition_sources": 0,
    }

    for row in conn.execute(
        "SELECT sport_key, competition_key, source_key FROM index_competition_sources"
    ).fetchall():
        key = (row["sport_key"], row["competition_key"], row["source_key"])
        if key in allowed_sources and (row["sport_key"], row["competition_key"]) in allowed_competitions:
            continue
        conn.execute(
            "DELETE FROM index_competition_sources WHERE sport_key = ? AND competition_key = ? AND source_key = ?",
            key,
        )
        counts["pruned_competition_sources"] += 1

    for row in conn.execute(
        """
        SELECT platform, platform_key_type, platform_key, sport_key, competition_key
        FROM index_platform_category_map
        """
    ).fetchall():
        key = (row["platform"], row["platform_key_type"], row["platform_key"], row["sport_key"], row["competition_key"])
        if key in allowed_maps and (row["sport_key"], row["competition_key"]) in allowed_competitions:
            continue
        conn.execute(
            """
            DELETE FROM index_platform_category_map
            WHERE platform = ? AND platform_key_type = ? AND platform_key = ?
              AND sport_key = ? AND competition_key = ?
            """,
            key,
        )
        counts["pruned_platform_maps"] += 1

    for row in conn.execute(
        "SELECT platform, platform_key_type, platform_key FROM index_platform_categories"
    ).fetchall():
        key = (row["platform"], row["platform_key_type"], row["platform_key"])
        if key in allowed_categories:
            continue
        conn.execute(
            "DELETE FROM index_platform_categories WHERE platform = ? AND platform_key_type = ? AND platform_key = ?",
            key,
        )
        counts["pruned_platform_categories"] += 1

    for row in conn.execute("SELECT sport_key, competition_key FROM index_competitions").fetchall():
        key = (row["sport_key"], row["competition_key"])
        if key in allowed_competitions:
            continue
        conn.execute(
            "DELETE FROM index_competitions WHERE sport_key = ? AND competition_key = ?",
            key,
        )
        counts["pruned_competitions"] += 1

    for row in conn.execute("SELECT sport_key FROM index_sports").fetchall():
        if row["sport_key"] in allowed_sports:
            continue
        conn.execute("DELETE FROM index_sports WHERE sport_key = ?", (row["sport_key"],))
        counts["pruned_sports"] += 1

    return counts


def seed_index_taxonomy(conn) -> dict[str, int]:
    counts = {
        "sports": 0,
        "competitions": 0,
        "platform_categories": 0,
        "platform_maps": 0,
        "competition_sources": 0,
        "pruned_sports": 0,
        "pruned_competitions": 0,
        "pruned_platform_categories": 0,
        "pruned_platform_maps": 0,
        "pruned_competition_sources": 0,
    }
    counts.update(prune_stale_index_taxonomy(conn))
    for sport in LOCAL_SPORTS:
        index_event_index_db.upsert_index_sport(conn, sport)
        counts["sports"] += 1
    for competition in LOCAL_COMPETITIONS:
        index_event_index_db.upsert_index_competition(conn, competition)
        counts["competitions"] += 1
    for category in (*PM_PLATFORM_CATEGORIES, *KS_PLATFORM_CATEGORIES):
        index_event_index_db.upsert_index_platform_category(conn, category)
        counts["platform_categories"] += 1
    for mapping in PLATFORM_CATEGORY_MAPS:
        index_event_index_db.upsert_index_platform_category_map(conn, mapping)
        counts["platform_maps"] += 1
    for source in taxonomy_sources():
        index_event_index_db.upsert_index_competition_source(conn, source)
        counts["competition_sources"] += 1
    conn.commit()
    return counts


def mapping_by_platform_key() -> dict[tuple[str, str, str], tuple[PlatformCategoryMap, ...]]:
    by_key: dict[tuple[str, str, str], list[PlatformCategoryMap]] = {}
    for mapping in PLATFORM_CATEGORY_MAPS:
        by_key.setdefault((mapping.platform, mapping.platform_key_type, mapping.platform_key), []).append(mapping)
    return {key: tuple(values) for key, values in by_key.items()}


def is_known_true_sports_category(
    category: PlatformCategory,
    known_maps: dict[tuple[str, str, str], tuple[PlatformCategoryMap, ...]],
) -> bool:
    mappings = known_maps.get((category.platform, category.platform_key_type, category.platform_key), ())
    return any(
        mapping.mapping_status != "ignored"
        and (mapping.sport_key, mapping.competition_key) in competition_key_set()
        for mapping in mappings
    )


def parse_pm_tag(raw: dict[str, Any]) -> PlatformCategory | None:
    slug = str(raw.get("slug") or raw.get("id") or "").strip()
    if not slug:
        return None
    display_name = str(raw.get("label") or raw.get("name") or raw.get("title") or slug)
    parent = str(raw.get("parentSlug") or raw.get("parent_slug") or raw.get("parent") or "")
    return PlatformCategory(
        "pm",
        "tag_slug",
        slug,
        display_name,
        parent,
        int(raw.get("event_count") or raw.get("eventsCount") or 0),
        int(raw.get("active_event_count") or raw.get("activeEventsCount") or 0),
        raw,
    )


def parse_ks_series(raw: dict[str, Any]) -> PlatformCategory | None:
    ticker = str(raw.get("ticker") or "").strip()
    if not ticker:
        return None
    display_name = str(raw.get("title") or raw.get("name") or ticker)
    parent = str(raw.get("category") or "")
    return PlatformCategory("ks", "series_ticker", ticker, display_name, parent, raw_payload=raw)


def discover_pm_platform_categories(limit: int = 500, max_pages: int = 10) -> list[PlatformCategory]:
    categories: list[PlatformCategory] = []
    seen: set[str] = set()
    offset = 0
    for _page in range(max_pages):
        payload = core.get_json(core.PM_GAMMA, "/tags", {"limit": limit, "offset": offset})
        raw_tags = payload.get("tags", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_tags, list) or not raw_tags:
            break
        for raw in raw_tags:
            if not isinstance(raw, dict):
                continue
            category = parse_pm_tag(raw)
            if category is None or category.platform_key in seen:
                continue
            seen.add(category.platform_key)
            categories.append(category)
        if len(raw_tags) < limit:
            break
        offset += len(raw_tags)
    return categories


def discover_ks_platform_categories() -> list[PlatformCategory]:
    categories: list[PlatformCategory] = []
    payloads = []
    for params in (
        {"category": "sports", "include_product_metadata": "true"},
        {"category": "Sports", "include_product_metadata": "true"},
        {"include_product_metadata": "true"},
    ):
        try:
            payloads.append(core.get_json(core.KALSHI_API, "/series", params))
        except Exception:  # noqa: BLE001 - discovery is best-effort diagnostics.
            continue
    seen: set[str] = set()
    for payload in payloads:
        raw_series = payload.get("series", []) if isinstance(payload, dict) else []
        for raw in raw_series:
            if not isinstance(raw, dict):
                continue
            category = parse_ks_series(raw)
            if category is None or category.platform_key in seen:
                continue
            seen.add(category.platform_key)
            categories.append(category)
    return categories


def upsert_discovered_platform_categories(conn, categories: list[PlatformCategory]) -> int:
    known_maps = mapping_by_platform_key()
    count = 0
    for category in categories:
        if not is_known_true_sports_category(category, known_maps):
            continue
        index_event_index_db.upsert_index_platform_category(conn, category)
        count += 1
        for mapping in known_maps.get((category.platform, category.platform_key_type, category.platform_key), ()):
            index_event_index_db.upsert_index_platform_category_map(conn, mapping)
    conn.commit()
    return count


def sync_index_taxonomy(conn, *, discover_pm: bool = False, discover_ks: bool = False) -> dict[str, int]:
    counts = seed_index_taxonomy(conn)
    counts["discovered_pm_categories"] = 0
    counts["discovered_ks_categories"] = 0
    if discover_pm:
        counts["discovered_pm_categories"] = upsert_discovered_platform_categories(conn, discover_pm_platform_categories())
    if discover_ks:
        counts["discovered_ks_categories"] = upsert_discovered_platform_categories(conn, discover_ks_platform_categories())
    return counts


def taxonomy_report(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT row_kind, sport_key, competition_key, competition_name, competition_status,
               pm_categories, ks_categories, source_keys, source_statuses, coverage_status
        FROM index_taxonomy_full_join
        ORDER BY row_kind, sport_key, competition_key, competition_name
        """
    ).fetchall()
    return [dict(row) for row in rows]
