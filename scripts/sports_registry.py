from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SportsCategory:
    key: str
    label: str
    tag_slugs: tuple[str, ...]
    parent: str
    market_focus: str
    arb_universe: str = ""
    arb_enabled: bool = False
    kalshi_series_ticker: str = ""
    schedule_source: str = ""
    risk_level: str = "inventory_only"
    notes: str = ""
    sport_codes: tuple[str, ...] = ()
    tag_ids: tuple[int, ...] = ()
    series: tuple[str, ...] = ()
    kalshi_candidate_series: tuple[str, ...] = ()
    resolution_source: str = ""
    adapter_name: str = ""
    arb_status: str = "inventory_only"

    @property
    def display_name(self) -> str:
        return self.label


SPORTS_CATEGORIES: tuple[SportsCategory, ...] = (
    SportsCategory(
        key="world_cup",
        label="World Cup",
        tag_slugs=("world-cup",),
        parent="soccer",
        market_focus="3-way moneyline",
        arb_universe="soccer",
        arb_enabled=True,
        kalshi_series_ticker="KXWCGAME",
        kalshi_candidate_series=("KXWCGAME",),
        schedule_source="fifa_world_cup_2026_local_schedule",
        risk_level="paired",
        notes="Pairs Team A win, Draw/Tie, and Team B win through the local World Cup schedule.",
        sport_codes=("fifawc",),
        adapter_name="world_cup",
        arb_status="paired",
    ),
    SportsCategory(
        key="mlb",
        label="MLB",
        tag_slugs=("mlb",),
        parent="baseball",
        market_focus="game winner",
        arb_universe="mlb",
        arb_enabled=True,
        kalshi_series_ticker="KXMLBGAME",
        kalshi_candidate_series=("KXMLBGAME",),
        schedule_source="mlb_stats_api",
        risk_level="paired",
        notes="Pairs through MLB Stats API schedule and official team ids.",
        sport_codes=("mlb",),
        adapter_name="mlb",
        arb_status="paired",
    ),
    SportsCategory(
        key="nba",
        label="NBA",
        tag_slugs=("nba",),
        parent="basketball",
        market_focus="game winner",
        arb_universe="nba",
        arb_enabled=True,
        kalshi_series_ticker="KXNBAGAME",
        kalshi_candidate_series=("KXNBAGAME",),
        schedule_source="espn_scoreboard",
        risk_level="paired",
        notes="Pairs through ESPN scoreboard schedule and NBA team metadata.",
        sport_codes=("nba",),
        adapter_name="nba",
        arb_status="paired",
    ),
    SportsCategory(
        key="ufc",
        label="UFC",
        tag_slugs=("ufc",),
        parent="combat",
        market_focus="fighter winner",
        kalshi_series_ticker="KXUFC",
        kalshi_candidate_series=("KXUFC", "KXUFCGAME"),
        notes="Inventory first; Kalshi series and official fight-card adapter are not enabled yet.",
        sport_codes=("ufc",),
        adapter_name="ufc",
    ),
    SportsCategory(
        key="football",
        label="Football",
        tag_slugs=("football", "nfl", "cfb", "cfl"),
        parent="football",
        market_focus="game winner",
        kalshi_series_ticker="KXNFLGAME",
        kalshi_candidate_series=("KXNFLGAME", "KXNCAAFGAME", "KXCFBGAME"),
        notes="Inventory first; NFL/CFB/CFL schedule adapters are pending.",
        sport_codes=("nfl", "cfb", "cfl"),
        adapter_name="football",
    ),
    SportsCategory(
        key="soccer",
        label="Soccer",
        tag_slugs=("soccer",),
        parent="soccer",
        market_focus="3-way moneyline",
        kalshi_series_ticker="KXSOCCERGAME",
        kalshi_candidate_series=("KXWCGAME", "KXSOCCERGAME"),
        notes="Inventory first for non-World-Cup leagues; generic soccer pairing is pending league schedules.",
        sport_codes=("epl", "lal", "seriea", "bundesliga", "mls", "ucl"),
        adapter_name="soccer",
    ),
    SportsCategory(
        key="tennis",
        label="Tennis",
        tag_slugs=("tennis",),
        parent="tennis",
        market_focus="match winner",
        kalshi_series_ticker="KXTENNIS",
        kalshi_candidate_series=("KXTENNIS", "KXTENNISGAME"),
        notes="Inventory first; ATP/WTA/ITF schedule adapter is pending.",
        sport_codes=("atp", "wta"),
        adapter_name="tennis",
    ),
    SportsCategory(
        key="cricket",
        label="Cricket",
        tag_slugs=("cricket",),
        parent="cricket",
        market_focus="match winner",
        kalshi_series_ticker="KXCRICKET",
        kalshi_candidate_series=("KXCRICKET", "KXCRICKETGAME"),
        notes="Inventory first; cricket schedule and Kalshi mapping are pending.",
        sport_codes=("ipl", "odi", "t20", "test"),
        adapter_name="cricket",
    ),
    SportsCategory(
        key="basketball",
        label="Basketball",
        tag_slugs=("basketball", "wnba"),
        parent="basketball",
        market_focus="game winner",
        kalshi_series_ticker="KXWNBA",
        kalshi_candidate_series=("KXWNBA", "KXNBAGAME", "KXNCAABGAME"),
        notes="Inventory first outside NBA; WNBA and international league adapters are pending.",
        sport_codes=("wnba", "ncaab"),
        adapter_name="basketball",
    ),
    SportsCategory(
        key="baseball",
        label="Baseball",
        tag_slugs=("baseball", "kbo"),
        parent="baseball",
        market_focus="game winner",
        kalshi_series_ticker="KXBASEBALLGAME",
        kalshi_candidate_series=("KXMLBGAME", "KXBASEBALLGAME"),
        notes="Inventory first outside MLB; KBO adapter is pending.",
        sport_codes=("kbo",),
        adapter_name="baseball",
    ),
    SportsCategory(
        key="rugby",
        label="Rugby",
        tag_slugs=("rugby",),
        parent="rugby",
        market_focus="match winner",
        kalshi_series_ticker="KXRUGBY",
        kalshi_candidate_series=("KXRUGBY", "KXRUGBYGAME"),
        notes="Inventory first; league schedule adapters are pending.",
        sport_codes=("rugby",),
        adapter_name="rugby",
    ),
    SportsCategory(
        key="table_tennis",
        label="Table Tennis",
        tag_slugs=("table-tennis",),
        parent="table_tennis",
        market_focus="match winner",
        kalshi_series_ticker="KXTTGAME",
        kalshi_candidate_series=("KXTTGAME", "KXTABLETENNIS"),
        notes="Inventory first; player schedule adapter is pending.",
        sport_codes=("wtt",),
        adapter_name="table_tennis",
    ),
    SportsCategory(
        key="golf",
        label="Golf",
        tag_slugs=("golf",),
        parent="golf",
        market_focus="tournament winner",
        kalshi_series_ticker="KXGOLF",
        kalshi_candidate_series=("KXGOLF",),
        notes="Inventory first; multi-runner futures only pair when venue propositions are identical.",
        sport_codes=("golf",),
        adapter_name="golf",
    ),
    SportsCategory(
        key="formula_1",
        label="Formula 1",
        tag_slugs=(),
        parent="formula_1",
        market_focus="race or championship winner",
        arb_universe="formula_1",
        arb_enabled=True,
        kalshi_series_ticker="KXF1",
        kalshi_candidate_series=("KXF1",),
        schedule_source="date_entity_semantic_match",
        risk_level="paired",
        notes="Pairs only exact F1 Drivers Championship futures through semantic outcome matching.",
        sport_codes=("f1",),
        tag_ids=(435,),
        series=("11635",),
        resolution_source="https://www.formula1.com/",
        adapter_name="formula_1",
        arb_status="paired",
    ),
    SportsCategory(
        key="boxing",
        label="Boxing",
        tag_slugs=("boxing",),
        parent="combat",
        market_focus="fighter winner",
        kalshi_series_ticker="KXBOXING",
        kalshi_candidate_series=("KXBOXING", "KXBOXINGGAME"),
        notes="Inventory first; official bout-card adapter is pending.",
        sport_codes=("boxing",),
        adapter_name="boxing",
    ),
    SportsCategory(
        key="pickleball",
        label="Pickleball",
        tag_slugs=("pickleball",),
        parent="pickleball",
        market_focus="match winner",
        kalshi_series_ticker="KXPICKLEBALL",
        kalshi_candidate_series=("KXPICKLEBALL", "KXPICKLEBALLGAME"),
        notes="Inventory first; player schedule adapter is pending.",
        sport_codes=("pickleball",),
        adapter_name="pickleball",
    ),
    SportsCategory(
        key="lacrosse",
        label="Lacrosse",
        tag_slugs=("lacrosse", "pll"),
        parent="lacrosse",
        market_focus="game winner",
        kalshi_series_ticker="KXLACROSSE",
        kalshi_candidate_series=("KXLACROSSE", "KXLACROSSEGAME"),
        notes="Inventory first; PLL/WLL schedule adapters are pending.",
        sport_codes=("pll",),
        adapter_name="lacrosse",
    ),
    SportsCategory(
        key="hockey",
        label="Hockey",
        tag_slugs=("hockey", "nhl"),
        parent="hockey",
        market_focus="game winner",
        kalshi_series_ticker="KXNHLGAME",
        kalshi_candidate_series=("KXNHLGAME",),
        notes="Inventory first; NHL adapter is pending.",
        sport_codes=("nhl",),
        adapter_name="hockey",
    ),
    SportsCategory(
        key="esports",
        label="Esports",
        tag_slugs=("esports", "cs2", "lol", "valorant"),
        parent="esports",
        market_focus="match winner",
        kalshi_series_ticker="KXLOLGAME",
        kalshi_candidate_series=("KXLOLGAME", "KXCS2GAME", "KXVALORANTGAME"),
        notes="Inventory first in all-sports mode; existing esports arb remains opt-in by explicit universe.",
        sport_codes=("lol", "val", "cs2", "dota2", "sc2"),
        adapter_name="esports",
    ),
)


CATEGORY_BY_KEY = {category.key: category for category in SPORTS_CATEGORIES}
ADAPTER_ALIASES = {
    "worldcup": "world_cup",
    "world-cup": "world_cup",
    "wc": "world_cup",
    "soccer_world_cup": "world_cup",
    "f1": "formula_1",
    "formula-1": "formula_1",
    "table-tennis": "table_tennis",
}


def inventory_categories() -> tuple[SportsCategory, ...]:
    return SPORTS_CATEGORIES


def category_for_key(key: str) -> SportsCategory:
    normalized = normalize_sport_key(key)
    return CATEGORY_BY_KEY[normalized]


def normalize_sport_key(key: str) -> str:
    cleaned = key.strip().lower().replace("-", "_")
    return ADAPTER_ALIASES.get(cleaned, cleaned)


def adapter_keys() -> tuple[str, ...]:
    return tuple(category.key for category in SPORTS_CATEGORIES)


def adapter_keys_with_legacy_expansion(values: list[str]) -> list[str]:
    keys: list[str] = []
    for value in values:
        normalized = normalize_sport_key(value)
        if normalized == "soccer":
            for soccer_key in ("world_cup", "soccer"):
                if soccer_key not in keys:
                    keys.append(soccer_key)
            continue
        if normalized not in CATEGORY_BY_KEY:
            raise KeyError(normalized)
        if normalized not in keys:
            keys.append(normalized)
    return keys


def enabled_arb_universes() -> tuple[str, ...]:
    seen: set[str] = set()
    universes: list[str] = []
    for category in SPORTS_CATEGORIES:
        if not category.arb_enabled or not category.arb_universe:
            continue
        if category.arb_universe in seen:
            continue
        seen.add(category.arb_universe)
        universes.append(category.arb_universe)
    return tuple(universes)


def sports_all_requested(value: str) -> bool:
    requested = [part.strip().lower() for part in value.split(",") if part.strip()]
    return "all" in requested
