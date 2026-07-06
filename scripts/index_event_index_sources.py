from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import index_event_index_core as index_core
import index_event_index_db
import index_nba_common
import index_pipeline_core as core
import index_sports_registry
import index_sports_taxonomy
import index_taxonomy
import index_universe_adapters


@dataclass(frozen=True)
class EventIndexSource:
    source_key: str
    registry_category_key: str
    category_key: str
    universe: str
    status: str
    source_type: str
    confidence: str
    source_url: str
    note: str
    fetcher: Callable[[str, str], tuple[list[index_core.IndexedEvent], list[str]]] | None = None
    requires_api_key: bool = False
    api_key_env_var: str = ""
    pricing_summary: str = ""


KBO_SOURCE_URL = "https://www.koreabaseball.com/Schedule/Schedule.aspx"
KBO_API = "https://www.koreabaseball.com"
KBO_SCHEDULE_PATH = "/ws/Schedule.asmx/GetScheduleList"
PANDASCORE_API = "https://api.pandascore.co"
PANDASCORE_CS2_SOURCE_URL = "https://www.pandascore.co/pricing"
BALLDONTLIE_API = "https://api.balldontlie.io"
BALLDONTLIE_ATP_SOURCE_URL = "https://atp.balldontlie.io/"
BALLDONTLIE_WTA_SOURCE_URL = "https://wta.balldontlie.io/"
BALLDONTLIE_MMA_SOURCE_URL = "https://mma.balldontlie.io/"
MLB_SOURCE_URL = "https://statsapi.mlb.com/api/v1/schedule"
ESPN_NBA_SOURCE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_WNBA_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
ESPN_WNBA_SOURCE_URL = f"{ESPN_WNBA_API}/scoreboard"
RIOT_LOL_SOURCE_URL = "https://lolesports.com/en-US"
RIOT_VALORANT_SOURCE_URL = "https://valorantesports.com/en-US"


SOURCES: tuple[EventIndexSource, ...] = (
    EventIndexSource(
        "fifa_world_cup_2026_local_schedule",
        "world_cup",
        "soccer",
        "world_cup",
        "enabled",
        "static_official_fixture",
        "high",
        "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026",
        "Static local fixture list for World Cup group-stage matches.",
        lambda start, end: (worldcup_index_events(start, end), []),
    ),
    EventIndexSource(
        "mlb_stats_api",
        "mlb",
        "baseball",
        "mlb",
        "enabled",
        "official_api",
        "high",
        MLB_SOURCE_URL,
        "MLB Stats API schedule with official game and team IDs.",
        lambda start, end: fetch_mlb_index_events(start, end),
    ),
    EventIndexSource(
        "kbo_official_site",
        "baseball",
        "baseball",
        "kbo",
        "enabled",
        "official_site",
        "medium",
        KBO_SOURCE_URL,
        "KBO official schedule page; deterministic source event IDs are derived from date, time, teams, and venue.",
        lambda start, end: fetch_kbo_index_events(start, end),
    ),
    EventIndexSource(
        "espn_nba_scoreboard",
        "nba",
        "basketball",
        "nba",
        "enabled",
        "vetted_public_api",
        "medium",
        ESPN_NBA_SOURCE_URL,
        "ESPN public scoreboard until an official/free NBA source is added.",
        lambda start, end: fetch_nba_index_events(start, end),
    ),
    EventIndexSource(
        "espn_wnba_scoreboard",
        "basketball",
        "basketball",
        "wnba",
        "enabled",
        "vetted_public_api",
        "medium",
        ESPN_WNBA_SOURCE_URL,
        "ESPN public scoreboard until an official/free WNBA source is added.",
        lambda start, end: fetch_wnba_index_events(start, end),
    ),
    EventIndexSource(
        "riot_lolesports_official_ssr",
        "esports",
        "esports",
        "lol",
        "enabled",
        "official_site",
        "medium",
        RIOT_LOL_SOURCE_URL,
        "Riot LoL Esports official site schedule payload.",
        lambda start, end: fetch_riot_esports_index_events("lol", start, end),
    ),
    EventIndexSource(
        "riot_valorantesports_official_ssr",
        "esports",
        "esports",
        "valorant",
        "enabled",
        "official_site",
        "medium",
        RIOT_VALORANT_SOURCE_URL,
        "Riot Valorant Esports official site schedule payload.",
        lambda start, end: fetch_riot_esports_index_events("valorant", start, end),
    ),
    EventIndexSource(
        "pandascore_cs2_fixtures",
        "esports",
        "esports",
        "cs2",
        "enabled_with_api_key",
        "vetted_provider_api",
        "medium",
        PANDASCORE_CS2_SOURCE_URL,
        "PandaScore CS2 fixture/calendar source.",
        lambda start, end: fetch_pandascore_cs2_index_events(start, end),
        True,
        "INDEX_PANDASCORE_API_KEY",
        "Schedules/context tier is listed as free with 1000 requests/hour.",
    ),
    EventIndexSource(
        "balldontlie_atp_matches",
        "tennis",
        "tennis",
        "atp",
        "enabled_with_api_key",
        "paid_provider_api",
        "medium",
        BALLDONTLIE_ATP_SOURCE_URL,
        "BALLDONTLIE ATP match source; match endpoint requires paid tier/trial.",
        lambda start, end: fetch_balldontlie_tennis_index_events("atp", start, end),
        True,
        "INDEX_BALLDONTLIE_API_KEY",
        "$9.99/mo per sport for match endpoint; 48-hour trial available.",
    ),
    EventIndexSource(
        "balldontlie_wta_matches",
        "tennis",
        "tennis",
        "wta",
        "enabled_with_api_key",
        "paid_provider_api",
        "medium",
        BALLDONTLIE_WTA_SOURCE_URL,
        "BALLDONTLIE WTA match source; match endpoint requires paid tier/trial.",
        lambda start, end: fetch_balldontlie_tennis_index_events("wta", start, end),
        True,
        "INDEX_BALLDONTLIE_API_KEY",
        "$9.99/mo per sport for match endpoint; 48-hour trial available.",
    ),
    EventIndexSource(
        "balldontlie_mma_fights",
        "ufc",
        "mma",
        "ufc",
        "enabled_with_api_key",
        "paid_provider_api",
        "medium",
        BALLDONTLIE_MMA_SOURCE_URL,
        "BALLDONTLIE MMA fight-level source filtered to UFC league.",
        lambda start, end: fetch_balldontlie_mma_index_events(start, end),
        True,
        "INDEX_BALLDONTLIE_API_KEY",
        "$9.99/mo for MMA fight endpoint; 48-hour trial available.",
    ),
)


SOURCE_GAP_CATEGORY_KEYS = {
    "football",
    "soccer",
    "tennis",
    "cricket",
    "baseball",
    "rugby",
    "table_tennis",
    "golf",
    "formula_1",
    "ufc",
    "boxing",
    "pickleball",
    "lacrosse",
    "hockey",
}

SOURCE_GAP_SOURCES: tuple[EventIndexSource, ...] = tuple(
    EventIndexSource(
        f"{category_key}_source_gap",
        category_key,
        index_sports_registry.category_for_key(category_key).parent,
        index_sports_registry.category_for_key(category_key).arb_universe or category_key,
        "source_gap",
        "none",
        "none",
        "",
        "No accepted official/free event-index source is configured for this taxonomy category.",
    )
    for category_key in sorted(SOURCE_GAP_CATEGORY_KEYS)
)

ALL_SOURCES: tuple[EventIndexSource, ...] = (*SOURCES, *SOURCE_GAP_SOURCES)


def default_date_range(from_date: str | None, days: int) -> tuple[str, str]:
    start = date.fromisoformat(from_date) if from_date else date.today()
    end = start + timedelta(days=max(days - 1, 0))
    return start.isoformat(), end.isoformat()


API_SOURCE_TYPES = {"official_api", "vetted_public_api", "vetted_provider_api", "paid_provider_api", "enterprise_provider_api"}
WEB_SOURCE_TYPES = {"official_site", "league_site", "team_site", "tournament_site", "authoritative_sports_media"}
CURATED_SOURCE_TYPES = {"static_official_fixture", "curated_from_authoritative_source"}


def source_matches_modes(source: EventIndexSource, source_modes: set[str] | None = None) -> bool:
    if not source_modes or "all" in source_modes:
        return True
    if source.status == "source_gap":
        return False
    if "api" in source_modes and source.source_type in API_SOURCE_TYPES:
        return True
    if "web" in source_modes and source.source_type in WEB_SOURCE_TYPES:
        return True
    if "curated" in source_modes and source.source_type in CURATED_SOURCE_TYPES:
        return True
    return False


def sources_for_sports(
    sports: str,
    source_keys: set[str] | None = None,
    source_modes: set[str] | None = None,
) -> list[EventIndexSource]:
    selected = []
    for source in ALL_SOURCES:
        if source_keys and source.source_key not in source_keys:
            continue
        if not source_matches_modes(source, source_modes):
            continue
        if index_core.include_sport(source.registry_category_key, source.universe, sports) or index_core.include_sport(source.category_key, source.universe, sports):
            selected.append(source)
    return selected


def sync_index_event_index_sources(
    conn,
    *,
    sports: str = "all",
    from_date: str | None = None,
    thru_date: str | None = None,
    days: int = 14,
    source_keys: set[str] | None = None,
    source_modes: set[str] | None = None,
) -> dict[str, int]:
    index_taxonomy.seed_index_taxonomy(conn)
    start, default_end = default_date_range(from_date, days)
    end = thru_date or default_end
    sources = sources_for_sports(sports, source_keys, source_modes)
    counts = {
        "sources": len(sources),
        "events_seen": 0,
        "events_upserted": 0,
        "source_gaps": 0,
        "missing_api_keys": 0,
        "warnings": 0,
    }
    for source in sources:
        index_event_index_db.upsert_event_index_source_catalog(conn, source)
        run_id = index_event_index_db.begin_event_index_source_run(conn, source)
        if source.status == "source_gap" or source.fetcher is None:
            counts["source_gaps"] += 1
            index_event_index_db.record_warning(
                conn,
                source,
                source.note,
                context={"source_key": source.source_key, "universe": source.universe},
            )
            index_event_index_db.finish_event_index_source_run(conn, run_id, status="source_gap", message=source.note)
            continue
        if source.requires_api_key and not api_key_for_source(source):
            counts["missing_api_keys"] += 1
            message = f"{source.source_key} requires {source.api_key_env_var}"
            index_event_index_db.record_warning(
                conn,
                source,
                message,
                severity="warning",
                context={"source_key": source.source_key, "env_var": source.api_key_env_var},
            )
            index_event_index_db.finish_event_index_source_run(conn, run_id, status="missing_api_key", message=message)
            conn.commit()
            continue
        try:
            events, warnings = source.fetcher(start, end)
        except Exception as exc:  # noqa: BLE001 - source failures should be diagnostics, not process crashes.
            message = f"{source.source_key} failed: {exc}"
            counts["warnings"] += 1
            index_event_index_db.record_warning(
                conn,
                source,
                message,
                severity="error",
                context={"source_key": source.source_key, "universe": source.universe},
            )
            index_event_index_db.finish_event_index_source_run(conn, run_id, status="error", warnings_count=1, message=message)
            continue
        upserted = 0
        for event in events:
            index_event_index_db.upsert_indexed_event(conn, event)
            upserted += 1
        for warning in warnings:
            index_event_index_db.record_warning(
                conn,
                source,
                warning,
                context={"source_key": source.source_key, "universe": source.universe},
            )
        counts["events_seen"] += len(events)
        counts["events_upserted"] += upserted
        counts["warnings"] += len(warnings)
        index_event_index_db.finish_event_index_source_run(
            conn,
            run_id,
            status="ok",
            events_seen=len(events),
            events_upserted=upserted,
            warnings_count=len(warnings),
            message=f"synced {upserted} indexed events",
        )
        conn.commit()
    conn.commit()
    return counts


def api_key_for_source(source: EventIndexSource) -> str:
    return str(os.environ.get(source.api_key_env_var, "") or "").strip() if source.api_key_env_var else ""


def worldcup_index_events(start_date: str, end_date: str) -> list[index_core.IndexedEvent]:
    events = []
    for match in index_universe_adapters.worldcup_schedule():
        if match.event_date < start_date or match.event_date > end_date:
            continue
        participants = (
            participant("soccer", "world_cup", "men", match.team_a, match.team_a, index=0, aliases=index_core.team_aliases(match.team_a)),
            participant("soccer", "world_cup", "men", match.team_b, match.team_b, index=1, aliases=index_core.team_aliases(match.team_b)),
        )
        events.append(
            index_core.IndexedEvent(
                canonical_event_id=match.match_id,
                category_key="soccer",
                universe="world_cup",
                event_date=match.event_date,
                market_type="game_winner",
                competition_gender="men",
                match_name=match.matchup,
                source_key="fifa_world_cup_2026_local_schedule",
                source_event_id=match.match_id,
                source_type="static_official_fixture",
                source_confidence="high",
                source_url="https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026",
                participants=participants,
                raw_payload={"event_date": match.event_date, "team_a": match.team_a, "team_b": match.team_b},
            )
        )
    return events


def fetch_mlb_index_events(start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    games = index_universe_adapters.fetch_mlb_schedule(start_date, end_date)
    events = []
    for game in games:
        participants = (
            mlb_participant(game.away_team, 0, "away"),
            mlb_participant(game.home_team, 1, "home"),
        )
        events.append(
            index_core.IndexedEvent(
                canonical_event_id=index_core.canonical_event_id_from_source("mlb", "mlb_stats_api", str(game.game_pk)),
                category_key="baseball",
                universe="mlb",
                event_date=game.official_date,
                start_time_utc=game.game_date_utc,
                market_type="game_winner",
                competition_gender="men",
                match_name=game.matchup,
                source_key="mlb_stats_api",
                source_event_id=str(game.game_pk),
                source_type="official_api",
                source_confidence="high",
                source_url=MLB_SOURCE_URL,
                participants=participants,
                raw_payload={"game_pk": game.game_pk, "official_date": game.official_date},
            )
        )
    return events, []


def fetch_nba_index_events(start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    games = index_universe_adapters.fetch_nba_schedule(start_date, end_date)
    events = []
    for game in games:
        participants = (
            nba_participant(game.away_team, 0, "away"),
            nba_participant(game.home_team, 1, "home"),
        )
        events.append(
            index_core.IndexedEvent(
                canonical_event_id=index_core.canonical_event_id_from_source("nba", "espn_nba_scoreboard", game.game_id),
                category_key="basketball",
                universe="nba",
                event_date=game.official_date,
                start_time_utc=game.game_date_utc,
                market_type="game_winner",
                competition_gender="men",
                match_name=game.matchup,
                source_key="espn_nba_scoreboard",
                source_event_id=game.game_id,
                source_type="vetted_public_api",
                source_confidence="medium",
                source_url=ESPN_NBA_SOURCE_URL,
                participants=participants,
                raw_payload={"game_id": game.game_id, "official_date": game.official_date},
            )
        )
    return events, []


def fetch_wnba_index_events(start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    events: list[index_core.IndexedEvent] = []
    warnings: list[str] = []
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while current <= end:
        payload = core.get_json(ESPN_WNBA_API, "/scoreboard", {"dates": current.strftime("%Y%m%d"), "limit": 100})
        for event in payload.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            competitors = competition.get("competitors") or []
            parsed = []
            for competitor in competitors:
                raw_team = competitor.get("team") or {}
                team = wnba_team_from_payload(raw_team)
                if team is None:
                    continue
                parsed.append((str(competitor.get("homeAway") or ""), team))
            if len(parsed) != 2:
                warnings.append(f"WNBA ESPN event has unmappable teams: {event.get('id')}")
                continue
            parsed.sort(key=lambda item: 0 if item[0] == "away" else 1)
            participants = tuple(
                wnba_participant(team, index, role)
                for index, (role, team) in enumerate(parsed)
            )
            source_id = str(event.get("id") or "")
            events.append(
                index_core.IndexedEvent(
                    canonical_event_id=index_core.canonical_event_id_from_source("wnba", "espn_wnba_scoreboard", source_id),
                    category_key="basketball",
                    universe="wnba",
                    event_date=current.isoformat(),
                    start_time_utc=str(event.get("date") or ""),
                    market_type="game_winner",
                    competition_gender="women",
                    match_name=f"{participants[0].display_name} vs {participants[1].display_name}",
                    source_key="espn_wnba_scoreboard",
                    source_event_id=source_id,
                    source_type="vetted_public_api",
                    source_confidence="medium",
                    source_url=ESPN_WNBA_SOURCE_URL,
                    participants=participants,  # type: ignore[arg-type]
                    raw_payload={"game_id": source_id, "official_date": current.isoformat()},
                )
            )
        current += timedelta(days=1)
    return events, warnings


def fetch_riot_esports_index_events(universe: str, start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    matches, warnings = index_universe_adapters.fetch_official_esports_schedule(universe, start_date)
    events: list[index_core.IndexedEvent] = []
    source_key = "riot_lolesports_official_ssr" if universe == "lol" else "riot_valorantesports_official_ssr"
    source_url = RIOT_LOL_SOURCE_URL if universe == "lol" else RIOT_VALORANT_SOURCE_URL
    for match in matches:
        if match.event_date > end_date:
            continue
        participants = tuple(
            esports_participant(universe, team, index)
            for index, team in enumerate(match.teams)
        )
        events.append(
            index_core.IndexedEvent(
                canonical_event_id=index_core.canonical_event_id_from_source(universe, source_key, match.match_id),
                category_key="esports",
                universe=universe,
                event_date=match.event_date,
                start_time_utc=match.start_time_utc,
                market_type="match_winner",
                competition_gender="open",
                match_name=match.matchup,
                source_key=source_key,
                source_event_id=match.match_id,
                source_type="official_site",
                source_confidence="medium",
                source_url=source_url,
                participants=participants,  # type: ignore[arg-type]
                raw_payload={"match_id": match.match_id, "event_date": match.event_date, "format": match.match_format},
            )
        )
    return events, warnings


class TableRowTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_tr = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._in_tr = True
            self._current_row = []
        elif self._in_tr and tag.lower() in {"td", "th"}:
            self._in_cell = True
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._in_cell:
            text = re.sub(r"\s+", " ", " ".join(self._current_text)).strip()
            self._current_row.append(text)
            self._in_cell = False
            self._current_text = []
        elif lowered == "tr" and self._in_tr:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._in_tr = False
            self._current_row = []


KBO_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "LG Twins": ("LG", "엘지", "LG 트윈스", "Twins"),
    "Hanwha Eagles": ("Hanwha", "한화", "한화 이글스", "Eagles"),
    "SSG Landers": ("SSG", "SSG 랜더스", "Landers"),
    "Samsung Lions": ("Samsung", "삼성", "삼성 라이온즈", "Lions"),
    "NC Dinos": ("NC", "NC 다이노스", "Dinos"),
    "KT Wiz": ("KT", "kt", "KT 위즈", "kt wiz", "Wiz"),
    "Lotte Giants": ("Lotte", "롯데", "롯데 자이언츠", "Giants"),
    "KIA Tigers": ("KIA", "Kia", "KIA 타이거즈", "Tigers"),
    "Doosan Bears": ("Doosan", "두산", "두산 베어스", "Bears"),
    "Kiwoom Heroes": ("Kiwoom", "키움", "키움 히어로즈", "Heroes"),
}
KBO_ALIAS_TO_TEAM = {
    index_core.alias_key(alias): team
    for team, aliases in KBO_TEAM_ALIASES.items()
    for alias in (team, *aliases)
    if index_core.alias_key(alias)
}
KBO_TEAM_ALIAS_ROWS = tuple(
    (team, alias, index_core.alias_key(alias))
    for team, aliases in KBO_TEAM_ALIASES.items()
    for alias in (team, *aliases)
)


def fetch_kbo_index_events(start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    months: set[tuple[int, int]] = set()
    current = date(start.year, start.month, 1)
    while current <= end:
        months.add((current.year, current.month))
        current = date(current.year + (1 if current.month == 12 else 0), 1 if current.month == 12 else current.month + 1, 1)

    events_by_id: dict[str, index_core.IndexedEvent] = {}
    warnings: list[str] = []
    for year, month in sorted(months):
        payload = core.post_form_json(
            KBO_API,
            KBO_SCHEDULE_PATH,
            {
                "leId": 1,
                "srIdList": "0",
                "seasonId": str(year),
                "gameMonth": f"{month:02d}",
                "teamId": "",
            },
            headers={"Referer": KBO_SOURCE_URL},
        )
        parsed_events, parsed_warnings = parse_kbo_schedule_payload(payload, default_year=year)
        warnings.extend(parsed_warnings)
        for event in parsed_events:
            if start_date <= event.event_date <= end_date:
                events_by_id[event.canonical_event_id] = event
    if not events_by_id:
        warnings.append(f"KBO official schedule returned no parsed games for {start_date}..{end_date}")
    return list(events_by_id.values()), warnings


def parse_kbo_schedule_payload(payload: dict[str, Any], *, default_year: int) -> tuple[list[index_core.IndexedEvent], list[str]]:
    events: list[index_core.IndexedEvent] = []
    warnings: list[str] = []
    current_date = ""
    for wrapper in payload.get("rows") or []:
        cells = wrapper.get("row") or []
        raw_cells = [str((cell or {}).get("Text") or "") for cell in cells if isinstance(cell, dict)]
        text_cells = [html_text(cell) for cell in raw_cells]
        row_date = kbo_date_from_cells(text_cells, default_year) or kbo_date_from_game_link(raw_cells)
        if row_date:
            current_date = row_date
        if not current_date:
            continue
        game_time = kbo_time_from_cells(text_cells)
        play_texts = [
            str(cell.get("Text") or "")
            for cell in cells
            if isinstance(cell, dict) and str(cell.get("Class") or "") == "play"
        ]
        teams = kbo_teams_from_cells(play_texts or raw_cells)
        if not game_time or len(teams) != 2:
            continue
        venue = kbo_venue_from_cells(text_cells, teams)
        source_event_id = kbo_game_id_from_cells(raw_cells)
        events.append(kbo_index_event(current_date, game_time, teams[0], teams[1], venue, raw_cells, source_event_id=source_event_id))
    if not events and payload.get("rows"):
        warnings.append("KBO official schedule payload had rows but no parsable games")
    return events, warnings


def parse_kbo_schedule_html(html: str, *, default_year: int) -> tuple[list[index_core.IndexedEvent], list[str]]:
    parser = TableRowTextParser()
    parser.feed(html)
    events: list[index_core.IndexedEvent] = []
    warnings: list[str] = []
    current_date = ""
    for row in parser.rows:
        parsed_date = kbo_date_from_cells(row, default_year)
        if parsed_date:
            current_date = parsed_date
        if not current_date:
            continue
        game_time = kbo_time_from_cells(row)
        teams = kbo_teams_from_cells(row)
        if not game_time or len(teams) != 2:
            continue
        venue = kbo_venue_from_cells(row, teams)
        event = kbo_index_event(current_date, game_time, teams[0], teams[1], venue, row)
        events.append(event)
    return events, warnings


def html_text(value: str) -> str:
    parser = TableRowTextParser()
    parser.feed(f"<table><tr><td>{value}</td></tr></table>")
    if parser.rows and parser.rows[0]:
        return parser.rows[0][0]
    return re.sub(r"<[^>]+>", " ", str(value or "")).strip()


def kbo_date_from_cells(cells: list[str], default_year: int) -> str:
    for cell in cells:
        match = re.search(r"(?:(20\d{2})[./-])?(\d{1,2})[./-](\d{1,2})", cell)
        if match:
            year = int(match.group(1) or default_year)
            return f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def kbo_date_from_game_link(cells: list[str]) -> str:
    for cell in cells:
        match = re.search(r"gameDate=(20\d{6})", cell)
        if match:
            value = match.group(1)
            return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return ""


def kbo_time_from_cells(cells: list[str]) -> str:
    for cell in cells:
        match = re.search(r"\b(\d{1,2}):([0-5]\d)\b", cell)
        if match:
            return f"{int(match.group(1)):02d}:{match.group(2)}"
    return ""


def kbo_teams_from_cells(cells: list[str]) -> tuple[str, ...]:
    teams: list[str] = []
    for cell in cells:
        cell_key = index_core.alias_key(cell)
        for team, alias, alias_key in KBO_TEAM_ALIAS_ROWS:
            alias_matches = (alias_key and alias_key in cell_key) or (not alias_key and alias and alias in cell)
            if alias_matches and team not in teams:
                teams.append(team)
        if len(teams) >= 2:
            break
    return tuple(teams[:2])


def kbo_venue_from_cells(cells: list[str], teams: tuple[str, ...]) -> str:
    team_aliases = {alias for team in teams for alias in (team, *KBO_TEAM_ALIASES.get(team, ()))}
    for cell in reversed(cells):
        if cell.strip() in {"", "-", "vs"}:
            continue
        key = index_core.alias_key(cell)
        if not cell.strip() or re.fullmatch(r"\d{1,2}:\d{2}", cell):
            continue
        if kbo_date_from_cells([cell], 2026):
            continue
        if any(
            (alias_key and alias_key in key) or (not alias_key and alias and alias in cell)
            for alias in team_aliases
            for alias_key in (index_core.alias_key(alias),)
        ):
            continue
        if key and key in {"preview", "gamecenter", "highlight", "tv", "radio", "note", "result"}:
            continue
        return cell
    return ""


def kbo_game_id_from_cells(cells: list[str]) -> str:
    for cell in cells:
        match = re.search(r"gameId=([^&'\" >]+)", cell)
        if match:
            return match.group(1)
    return ""


def kbo_index_event(
    event_date: str,
    game_time: str,
    away_team: str,
    home_team: str,
    venue: str,
    raw_row: list[str],
    *,
    source_event_id: str = "",
) -> index_core.IndexedEvent:
    local_dt = datetime.fromisoformat(f"{event_date}T{game_time}:00").replace(tzinfo=timezone(timedelta(hours=9)))
    start_time_utc = local_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_event_id = source_event_id or f"{event_date}:{game_time}:{index_core.alias_key(away_team)}:{index_core.alias_key(home_team)}:{index_core.alias_key(venue)}"
    participants = (
        kbo_participant(away_team, 0, "away"),
        kbo_participant(home_team, 1, "home"),
    )
    return index_core.IndexedEvent(
        canonical_event_id=index_core.canonical_event_id_from_source("kbo", "kbo_official_site", source_event_id),
        category_key="baseball",
        universe="kbo",
        sport_key="baseball",
        competition_key="kbo",
        event_date=event_date,
        source_local_date=event_date,
        start_time_utc=start_time_utc,
        market_type="game_winner",
        competition_gender="men",
        match_name=f"{away_team} @ {home_team}",
        event_name=f"{away_team} @ {home_team}",
        source_key="kbo_official_site",
        source_event_id=source_event_id,
        source_type="official_site",
        source_confidence="medium",
        source_url=KBO_SOURCE_URL,
        venue_name=venue,
        venue_country="KR",
        participants=participants,
        raw_payload={"row": raw_row, "local_time": f"{event_date}T{game_time}:00+09:00"},
    )


def kbo_participant(team_name: str, index: int, role: str) -> index_core.IndexedParticipant:
    return participant(
        "baseball",
        "kbo",
        "men",
        f"kbo_team_{index_core.alias_key(team_name)}",
        team_name,
        index=index,
        role=role,
        aliases=index_core.team_aliases(team_name, *KBO_TEAM_ALIASES.get(team_name, ())),
    )


def fetch_pandascore_cs2_index_events(start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    api_key = str(os.environ.get("INDEX_PANDASCORE_API_KEY", "") or "").strip()
    matches = fetch_pandascore_matches("/csgo/matches", start_date, end_date, api_key)
    events: list[index_core.IndexedEvent] = []
    warnings: list[str] = []
    for raw in matches:
        event = pandascore_cs2_index_event(raw)
        if event is None:
            warnings.append(f"PandaScore CS2 match rejected: {raw.get('id')}")
            continue
        if start_date <= event.event_date <= end_date:
            events.append(event)
    return events, warnings


def fetch_pandascore_matches(path: str, start_date: str, end_date: str, api_key: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    page = 1
    while page <= 100:
        payload = core.get_json(
            PANDASCORE_API,
            path,
            {
                "range[begin_at]": f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
                "per_page": 100,
                "page": page,
                "sort": "begin_at",
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if not isinstance(payload, list) or not payload:
            break
        matches.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
        page += 1
    return matches


def pandascore_cs2_index_event(raw: dict[str, Any]) -> index_core.IndexedEvent | None:
    opponents = raw.get("opponents") or []
    parsed = []
    for item in opponents:
        opponent = (item or {}).get("opponent") or {}
        team_id = str(opponent.get("id") or "")
        name = str(opponent.get("name") or "").strip()
        acronym = str(opponent.get("acronym") or "").strip()
        if not team_id or not name or index_core.alias_key(name) == "tbd":
            continue
        parsed.append((team_id, name, acronym))
    begin_at = str(raw.get("begin_at") or "")
    event_date = iso_date(begin_at)
    source_id = str(raw.get("id") or "")
    if len(parsed) != 2 or not event_date or not source_id:
        return None
    tournament = raw.get("tournament") or {}
    league = raw.get("league") or {}
    participants = tuple(
        participant(
            "esports",
            "cs2",
            "open",
            f"pandascore_team_{team_id}",
            name,
            index=index,
            aliases=index_core.team_aliases(name, acronym),
        )
        for index, (team_id, name, acronym) in enumerate(parsed)
    )
    return index_core.IndexedEvent(
        canonical_event_id=index_core.canonical_event_id_from_source("cs2", "pandascore_cs2_fixtures", source_id),
        category_key="esports",
        universe="cs2",
        sport_key="esports",
        competition_key="cs2",
        event_date=event_date,
        start_time_utc=begin_at,
        market_type="match_winner",
        competition_gender="open",
        match_name=f"{participants[0].display_name} vs {participants[1].display_name}",
        event_name=f"{participants[0].display_name} vs {participants[1].display_name}",
        source_key="pandascore_cs2_fixtures",
        source_event_id=source_id,
        source_type="vetted_provider_api",
        source_confidence="medium",
        source_url=PANDASCORE_CS2_SOURCE_URL,
        venue_name=str(tournament.get("name") or league.get("name") or ""),
        participants=participants,  # type: ignore[arg-type]
        raw_payload=raw,
    )


def fetch_balldontlie_tennis_index_events(competition_key: str, start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    api_key = str(os.environ.get("INDEX_BALLDONTLIE_API_KEY", "") or "").strip()
    source_key = f"balldontlie_{competition_key}_matches"
    raw_matches = []
    for season in range(date.fromisoformat(start_date).year, date.fromisoformat(end_date).year + 1):
        raw_matches.extend(fetch_balldontlie_paginated(f"/{competition_key}/v1/matches", {"season": season}, api_key))
    events: list[index_core.IndexedEvent] = []
    warnings: list[str] = []
    for raw in raw_matches:
        event = balldontlie_tennis_index_event(competition_key, source_key, raw)
        if event is None:
            warnings.append(f"BALLDONTLIE {competition_key.upper()} match rejected: {raw.get('id')}")
            continue
        if start_date <= event.event_date <= end_date:
            events.append(event)
    return dedupe_events(events), warnings


def fetch_balldontlie_mma_index_events(start_date: str, end_date: str) -> tuple[list[index_core.IndexedEvent], list[str]]:
    api_key = str(os.environ.get("INDEX_BALLDONTLIE_API_KEY", "") or "").strip()
    raw_events = []
    for year in range(date.fromisoformat(start_date).year, date.fromisoformat(end_date).year + 1):
        raw_events.extend(fetch_balldontlie_paginated("/mma/v1/events", {"year": year}, api_key))
    ufc_event_ids = [
        int(raw["id"])
        for raw in raw_events
        if start_date <= iso_date(str(raw.get("date") or "")) <= end_date
        and index_core.alias_key(((raw.get("league") or {}).get("abbreviation") or (raw.get("league") or {}).get("name") or "")) == "ufc"
        and raw.get("id") is not None
    ]
    raw_fights: list[dict[str, Any]] = []
    for index in range(0, len(ufc_event_ids), 50):
        raw_fights.extend(fetch_balldontlie_paginated("/mma/v1/fights", {"event_ids[]": ufc_event_ids[index : index + 50]}, api_key))
    events: list[index_core.IndexedEvent] = []
    warnings: list[str] = []
    for raw in raw_fights:
        event = balldontlie_mma_index_event(raw)
        if event is None:
            warnings.append(f"BALLDONTLIE MMA fight rejected: {raw.get('id')}")
            continue
        if start_date <= event.event_date <= end_date:
            events.append(event)
    return dedupe_events(events), warnings


def fetch_balldontlie_paginated(path: str, params: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    for _page in range(200):
        page_params = {"per_page": 100, **params}
        if cursor is not None:
            page_params["cursor"] = cursor
        payload = core.get_json(
            BALLDONTLIE_API,
            path,
            page_params,
            headers={"Authorization": api_key},
        )
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list) or not data:
            break
        rows.extend(item for item in data if isinstance(item, dict))
        meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
        cursor = meta.get("next_cursor")
        if cursor in (None, ""):
            break
    return rows


def balldontlie_tennis_index_event(competition_key: str, source_key: str, raw: dict[str, Any]) -> index_core.IndexedEvent | None:
    player_one = raw.get("player1") or {}
    player_two = raw.get("player2") or {}
    source_id = str(raw.get("id") or "")
    start_time = str(raw.get("scheduled_time") or raw.get("start_time") or raw.get("date") or "")
    event_date = iso_date(start_time)
    if not source_id or not event_date or not valid_person(player_one) or not valid_person(player_two):
        return None
    gender = "men" if competition_key == "atp" else "women"
    tournament = raw.get("tournament") or {}
    participants = (
        tennis_participant(competition_key, gender, player_one, 0),
        tennis_participant(competition_key, gender, player_two, 1),
    )
    return index_core.IndexedEvent(
        canonical_event_id=index_core.canonical_event_id_from_source(competition_key, source_key, source_id),
        category_key="tennis",
        universe=competition_key,
        sport_key="tennis",
        competition_key=competition_key,
        event_date=event_date,
        start_time_utc=start_time,
        market_type="match_winner",
        competition_gender=gender,
        match_name=f"{participants[0].display_name} vs {participants[1].display_name}",
        event_name=f"{participants[0].display_name} vs {participants[1].display_name}",
        source_key=source_key,
        source_event_id=source_id,
        source_type="paid_provider_api",
        source_confidence="medium",
        source_url=BALLDONTLIE_ATP_SOURCE_URL if competition_key == "atp" else BALLDONTLIE_WTA_SOURCE_URL,
        venue_name=str(tournament.get("name") or ""),
        venue_city=str(tournament.get("location") or ""),
        participants=participants,
        raw_payload=raw,
    )


def tennis_participant(competition_key: str, gender: str, raw_player: dict[str, Any], index: int) -> index_core.IndexedParticipant:
    full_name = str(raw_player.get("full_name") or " ".join(str(raw_player.get(key) or "") for key in ("first_name", "last_name"))).strip()
    source_id = str(raw_player.get("id") or index_core.alias_key(full_name))
    aliases = [full_name, str(raw_player.get("last_name") or ""), str(raw_player.get("first_name") or "")]
    country_code = str(raw_player.get("country_code") or "")
    if country_code:
        aliases.append(f"{full_name} {country_code}")
    return participant(
        "tennis",
        competition_key,
        gender,
        f"{competition_key}_player_{source_id}",
        full_name,
        index=index,
        aliases=index_core.team_aliases(full_name, *aliases),
    )


def balldontlie_mma_index_event(raw: dict[str, Any]) -> index_core.IndexedEvent | None:
    source_id = str(raw.get("id") or "")
    event = raw.get("event") or {}
    league = event.get("league") or {}
    if index_core.alias_key(str(league.get("abbreviation") or league.get("name") or "")) != "ufc":
        return None
    fighter_one = raw.get("fighter1") or {}
    fighter_two = raw.get("fighter2") or {}
    start_time = str(event.get("date") or "")
    event_date = iso_date(start_time)
    if not source_id or not event_date or not valid_person(fighter_one, name_key="name") or not valid_person(fighter_two, name_key="name"):
        return None
    gender = "women" if index_core.alias_key(((raw.get("weight_class") or {}).get("gender") or "")) == "female" else "men"
    participants = (
        fighter_participant(fighter_one, gender, 0),
        fighter_participant(fighter_two, gender, 1),
    )
    return index_core.IndexedEvent(
        canonical_event_id=index_core.canonical_event_id_from_source("ufc", "balldontlie_mma_fights", source_id),
        category_key="mma",
        universe="ufc",
        sport_key="mma",
        competition_key="ufc",
        event_date=event_date,
        start_time_utc=start_time,
        market_type="fighter_winner",
        competition_gender=gender,
        match_name=f"{participants[0].display_name} vs {participants[1].display_name}",
        event_name=f"{participants[0].display_name} vs {participants[1].display_name} - {event.get('short_name') or event.get('name') or ''}",
        source_key="balldontlie_mma_fights",
        source_event_id=source_id,
        source_type="paid_provider_api",
        source_confidence="medium",
        source_url=BALLDONTLIE_MMA_SOURCE_URL,
        venue_name=str(event.get("venue_name") or ""),
        venue_city=str(event.get("venue_city") or ""),
        venue_region=str(event.get("venue_state") or ""),
        venue_country=str(event.get("venue_country") or ""),
        participants=participants,
        raw_payload=raw,
    )


def fighter_participant(raw_fighter: dict[str, Any], gender: str, index: int) -> index_core.IndexedParticipant:
    name = str(raw_fighter.get("name") or "").strip()
    source_id = str(raw_fighter.get("id") or index_core.alias_key(name))
    aliases = [name, str(raw_fighter.get("first_name") or ""), str(raw_fighter.get("last_name") or ""), str(raw_fighter.get("nickname") or "")]
    return participant(
        "mma",
        "ufc",
        gender,
        f"ufc_fighter_{source_id}",
        name,
        index=index,
        aliases=index_core.team_aliases(name, *aliases),
    )


def valid_person(raw: dict[str, Any], *, name_key: str = "full_name") -> bool:
    name = str(raw.get(name_key) or raw.get("name") or "").strip()
    return bool(raw.get("id") and name and index_core.alias_key(name) != "tbd")


def iso_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
        return match.group(1) if match else ""


def dedupe_events(events: list[index_core.IndexedEvent]) -> list[index_core.IndexedEvent]:
    by_id = {event.canonical_event_id: event for event in events}
    return list(by_id.values())


def participant(
    category_key: str,
    universe: str,
    gender: str,
    source_id: str,
    display_name: str,
    *,
    index: int,
    role: str = "",
    aliases: tuple[str, ...] = (),
) -> index_core.IndexedParticipant:
    return index_core.IndexedParticipant(
        participant_key=index_core.participant_key(category_key, universe, gender, source_id),
        source_participant_id=str(source_id),
        display_name=display_name,
        aliases=aliases,
        role=role,
        order=index,
    )


def mlb_participant(team: index_universe_adapters.MlbTeam, index: int, role: str) -> index_core.IndexedParticipant:
    aliases = index_core.team_aliases(team.name, team.abbreviation, team.file_code)
    return participant("baseball", "mlb", "men", f"mlb_team_{team.id}", team.name, index=index, role=role, aliases=aliases)


def nba_participant(team: index_nba_common.NbaTeam, index: int, role: str) -> index_core.IndexedParticipant:
    aliases = index_core.team_aliases(team.name, team.abbreviation, *team.aliases)
    return participant("basketball", "nba", "men", f"nba_team_{team.id}", team.name, index=index, role=role, aliases=aliases)


def wnba_participant(team: index_sports_taxonomy.WnbaTeam, index: int, role: str) -> index_core.IndexedParticipant:
    aliases = index_core.team_aliases(team.name, team.key, *team.aliases)
    return participant("basketball", "wnba", "women", f"wnba_team_{team.key}", team.name, index=index, role=role, aliases=aliases)


def esports_participant(universe: str, team: index_universe_adapters.OfficialEsportsTeam, index: int) -> index_core.IndexedParticipant:
    aliases = index_core.team_aliases(team.name, team.code)
    return participant("esports", universe, "open", team.id, team.name, index=index, aliases=aliases)


def wnba_team_from_payload(raw_team: dict[str, Any]) -> index_sports_taxonomy.WnbaTeam | None:
    values = [
        str(raw_team.get("displayName") or ""),
        str(raw_team.get("shortDisplayName") or ""),
        str(raw_team.get("name") or ""),
        str(raw_team.get("abbreviation") or ""),
    ]
    alias_map = {
        index_core.alias_key(alias): team
        for team in index_sports_taxonomy.WNBA_TEAMS
        for alias in (team.key, team.name, *team.aliases)
    }
    for value in values:
        team = alias_map.get(index_core.alias_key(value))
        if team is not None:
            return team
    return None


def registry_coverage() -> dict[str, list[EventIndexSource]]:
    by_category: dict[str, list[EventIndexSource]] = {category.key: [] for category in index_sports_registry.inventory_categories()}
    for source in ALL_SOURCES:
        by_category.setdefault(source.registry_category_key, []).append(source)
    return by_category
