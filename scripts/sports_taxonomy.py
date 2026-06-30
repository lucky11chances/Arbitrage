from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import nba_common


ALLOWED_GENDERS = {"men", "women", "mixed", "open", "unknown"}
TENNIS_BIRTH_YEAR_RE = re.compile(r"\(\s*(?:b\.?|born)\s*\d{4}\s*\)", re.IGNORECASE)
TENNIS_SUFFIX_RE = re.compile(r"\b(?:jr|sr|ii|iii|iv)\.?\b", re.IGNORECASE)
TENNIS_GIVEN_NAME_ALIASES = {
    "alexander": ("aleksandr",),
    "aleksandr": ("alexander",),
}
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
MATCHUP_DELIMITER_RE = re.compile(r"\b(?:vs\.?|v\.?|at|@)\b|[A-Za-z0-9]\s*[-\u2013\u2014]\s*[A-Za-z0-9]", re.IGNORECASE)


@dataclass(frozen=True)
class TaxonomyResult:
    category_key: str
    universe: str
    market_type: str
    competition_gender: str
    gender_source: str
    gender_confidence: str
    warning_message: str = ""


@dataclass(frozen=True)
class WnbaTeam:
    key: str
    name: str
    aliases: tuple[str, ...]


WNBA_TEAMS = (
    WnbaTeam("atlantadream", "Atlanta Dream", ("atlanta", "atl", "dream", "atlanta dream")),
    WnbaTeam("chicagosky", "Chicago Sky", ("chicago", "chi", "sky", "chicago sky")),
    WnbaTeam("connecticutsun", "Connecticut Sun", ("connecticut", "conn", "con", "sun", "connecticut sun")),
    WnbaTeam("dallaswings", "Dallas Wings", ("dallas", "dal", "wings", "dallas wings")),
    WnbaTeam("goldenstatevalkyries", "Golden State Valkyries", ("golden state", "gs", "gsv", "valkyries", "golden state valkyries")),
    WnbaTeam("indianafever", "Indiana Fever", ("indiana", "ind", "fever", "indiana fever")),
    WnbaTeam("lasvegasaces", "Las Vegas Aces", ("las vegas", "lv", "lva", "aces", "las vegas aces")),
    WnbaTeam("losangelessparks", "Los Angeles Sparks", ("los angeles", "la", "sparks", "los angeles sparks")),
    WnbaTeam("minnesotalynx", "Minnesota Lynx", ("minnesota", "min", "lynx", "minnesota lynx")),
    WnbaTeam("newyorkliberty", "New York Liberty", ("new york", "ny", "nyl", "liberty", "new york liberty")),
    WnbaTeam("phoenixmercury", "Phoenix Mercury", ("phoenix", "phx", "mercury", "phoenix mercury")),
    WnbaTeam("portlandfire", "Portland Fire", ("portland", "pdx", "por", "fire", "portland fire", "portlandfire")),
    WnbaTeam("seattlestorm", "Seattle Storm", ("seattle", "sea", "storm", "seattle storm")),
    WnbaTeam("torontotempo", "Toronto Tempo", ("toronto", "tor", "tempo", "toronto tempo")),
    WnbaTeam("washingtonmystics", "Washington Mystics", ("washington", "was", "wsh", "mystics", "washington mystics")),
)
WNBA_BY_ALIAS = {
    "basketball:wnba:women:" + re.sub(r"[^a-z0-9]+", "", alias.lower()): team.key
    for team in WNBA_TEAMS
    for alias in (team.key, team.name, *team.aliases)
}


KS_SERIES_TAXONOMY = {
    "KXWNBAGAME": ("basketball", "wnba", "game_winner", "women", "ks_series_ticker", "high"),
    "KXWNBA": ("basketball", "wnba", "tournament_winner", "women", "ks_series_ticker", "high"),
    "KXNBAGAME": ("basketball", "nba", "game_winner", "men", "ks_series_ticker", "high"),
    "KXMLBGAME": ("baseball", "mlb", "game_winner", "men", "ks_series_ticker", "high"),
    "KXBASEBALLGAME": ("baseball", "baseball", "game_winner", "unknown", "ks_series_ticker", "low"),
    "KXWCGAME": ("soccer", "world_cup", "3_way_moneyline", "men", "ks_series_ticker", "high"),
    "KXNFLGAME": ("football", "nfl", "game_winner", "men", "ks_series_ticker", "high"),
    "KXNCAAFGAME": ("football", "cfb", "game_winner", "men", "ks_series_ticker", "high"),
    "KXCFBGAME": ("football", "cfb", "game_winner", "men", "ks_series_ticker", "high"),
    "KXATPMATCH": ("tennis", "atp", "match_winner", "men", "ks_series_ticker", "high"),
    "KXATPCHALLENGERMATCH": ("tennis", "atp_challenger", "match_winner", "men", "ks_series_ticker", "high"),
    "KXWTAMATCH": ("tennis", "wta", "match_winner", "women", "ks_series_ticker", "high"),
    "KXITFMATCH": ("tennis", "itf_men", "match_winner", "men", "ks_series_ticker", "high"),
    "KXITFWMATCH": ("tennis", "itf_women", "match_winner", "women", "ks_series_ticker", "high"),
    "KXUFCFIGHT": ("combat", "ufc", "fighter_winner", "unknown", "ks_series_ticker", "low"),
    "KXBOXING": ("combat", "boxing", "fighter_winner", "unknown", "ks_series_ticker", "low"),
    "KXBOXINGGAME": ("combat", "boxing", "fighter_winner", "unknown", "ks_series_ticker", "low"),
    "KXCS2GAME": ("esports", "cs2", "match_winner", "open", "ks_series_ticker", "high"),
    "KXLOLGAME": ("esports", "lol", "match_winner", "open", "ks_series_ticker", "high"),
    "KXVALORANTGAME": ("esports", "valorant", "match_winner", "open", "ks_series_ticker", "high"),
    "KXF1": ("formula_1", "f1", "race_or_championship_winner", "open", "ks_series_ticker", "medium"),
}


PM_SOURCE_TAXONOMY = {
    "wnba": ("basketball", "wnba", "women", "pm_source", "high"),
    "nba": ("basketball", "nba", "men", "pm_source", "high"),
    "mlb": ("baseball", "mlb", "men", "pm_source", "high"),
    "kbo": ("baseball", "kbo", "men", "pm_source", "medium"),
    "world-cup": ("soccer", "world_cup", "men", "pm_source", "high"),
    "nfl": ("football", "nfl", "men", "pm_source", "high"),
    "cfb": ("football", "cfb", "men", "pm_source", "high"),
    "cfl": ("football", "cfl", "men", "pm_source", "medium"),
    "ufc": ("combat", "ufc", "unknown", "pm_source", "low"),
    "boxing": ("combat", "boxing", "unknown", "pm_source", "low"),
    "cs2": ("esports", "cs2", "open", "pm_source", "high"),
    "lol": ("esports", "lol", "open", "pm_source", "high"),
    "valorant": ("esports", "valorant", "open", "pm_source", "high"),
}


GENERIC_PM_SOURCE_TAXONOMY = {
    "basketball": ("basketball", "basketball", "unknown"),
    "baseball": ("baseball", "baseball", "unknown"),
    "soccer": ("soccer", "soccer", "unknown"),
    "football": ("football", "football", "unknown"),
    "tennis": ("tennis", "tennis", "unknown"),
    "cricket": ("cricket", "cricket", "unknown"),
    "golf": ("golf", "golf", "open"),
    "hockey": ("hockey", "hockey", "men"),
    "nhl": ("hockey", "nhl", "men"),
    "rugby": ("rugby", "rugby", "unknown"),
    "pickleball": ("pickleball", "pickleball", "unknown"),
    "lacrosse": ("lacrosse", "lacrosse", "unknown"),
    "pll": ("lacrosse", "pll", "men"),
    "esports": ("esports", "esports", "open"),
    "tag_id:435": ("formula_1", "f1", "open"),
}


def normalize_key(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def tennis_player_key(value: str) -> str:
    cleaned = TENNIS_BIRTH_YEAR_RE.sub("", value)
    cleaned = TENNIS_SUFFIX_RE.sub("", cleaned)
    return normalize_key(cleaned)


def tennis_name_tokens(value: str) -> list[str]:
    cleaned = TENNIS_BIRTH_YEAR_RE.sub("", value)
    cleaned = TENNIS_SUFFIX_RE.sub("", cleaned)
    folded = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    return [token for token in re.findall(r"[a-z0-9]+", folded.lower()) if token]


def tennis_player_alias_keys(value: str) -> set[str]:
    tokens = tennis_name_tokens(value)
    if not tokens:
        return set()

    aliases = {normalize_key(" ".join(tokens))}
    if len(tokens) == 1:
        aliases.add(tokens[0])
        return aliases

    first = tokens[0]
    last = tokens[-1]
    given_variants = {first, *TENNIS_GIVEN_NAME_ALIASES.get(first, ())}
    surname_variants = {last}
    if len(tokens) >= 3:
        surname_variants.add("".join(tokens[-2:]))

    for given in given_variants:
        for surname in surname_variants:
            aliases.add(f"{given}{surname}")
            aliases.add(f"{given[:1]}{surname}")
    for surname in surname_variants:
        aliases.add(surname)
    return {alias for alias in aliases if alias}


def source_values(sources: list[str] | tuple[str, ...]) -> set[str]:
    values: set[str] = set()
    for source in sources:
        raw = str(source or "").strip().lower()
        if not raw:
            continue
        values.add(raw)
        if ":" in raw:
            values.add(raw.split(":", 1)[1])
    return values


def classify_pm_event(event_slug: str, title: str, sources: list[str] | tuple[str, ...], market_question: str = "") -> TaxonomyResult:
    slug = event_slug.lower()
    text = f"{event_slug} {title} {market_question}".lower()
    values = source_values(sources)
    candidates: list[tuple[int, TaxonomyResult]] = []

    for value, spec in PM_SOURCE_TAXONOMY.items():
        if value in values:
            category, universe, gender, gender_source, confidence = spec
            candidates.append((90, result(category, universe, infer_pm_market_type(category, universe, slug, title), gender, gender_source, confidence)))

    if slug.startswith("wnba-") or " wnba" in f" {text}":
        candidates.append((95, result("basketball", "wnba", infer_pm_market_type("basketball", "wnba", slug, title), "women", "pm_slug_title", "high")))
    if slug.startswith("nba-") or " nba" in f" {text}":
        candidates.append((95, result("basketball", "nba", infer_pm_market_type("basketball", "nba", slug, title), "men", "pm_slug_title", "high")))
    if slug.startswith("cs2-") or " cs2" in f" {text}" or "counter-strike" in text or "counter strike" in text:
        candidates.append((95, result("esports", "cs2", infer_pm_market_type("esports", "cs2", slug, title), "open", "pm_slug_title", "high")))
    if slug.startswith("valorant-") or " valorant" in f" {text}":
        candidates.append((95, result("esports", "valorant", infer_pm_market_type("esports", "valorant", slug, title), "open", "pm_slug_title", "high")))
    if slug.startswith("lol-") or " league of legends" in text:
        candidates.append((95, result("esports", "lol", infer_pm_market_type("esports", "lol", slug, title), "open", "pm_slug_title", "high")))

    tennis = classify_pm_tennis(slug, title, market_question)
    if tennis is not None:
        candidates.append((100, tennis))

    if candidates:
        high = [candidate for priority, candidate in candidates if priority >= 90]
        genders = {candidate.competition_gender for candidate in high if candidate.competition_gender in {"men", "women"}}
        if len(genders) > 1:
            return result("unknown", "unknown", "unknown", "unknown", "pm_gender_conflict", "low", "PM gender signals conflict")
        return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]

    for value, spec in GENERIC_PM_SOURCE_TAXONOMY.items():
        if value in values:
            category, universe, gender = spec
            return result(category, universe, infer_pm_market_type(category, universe, slug, title), gender, "pm_source", "medium" if gender != "unknown" else "low")

    if "women" in text or "women's" in text or "womens" in text:
        return result("unknown", "unknown", "unknown", "women", "pm_title", "medium")
    if "men" in text or "men's" in text or "mens" in text:
        return result("unknown", "unknown", "unknown", "men", "pm_title", "medium")
    return result("unknown", "unknown", "unknown", "unknown", "pm_unknown", "low")


def classify_pm_tennis(slug: str, title: str, context: str = "") -> TaxonomyResult | None:
    text = f"{slug} {title} {context}".lower()
    title_text = f"{slug} {title}".lower()
    if slug.startswith("itf-") and ("itf women" in text or "women's itf" in text or "womens itf" in text):
        return result("tennis", "itf_women", "match_winner", "women", "pm_tennis_signal", "high")
    if slug.startswith("itf-") and ("itf men" in text or "men's itf" in text or "mens itf" in text):
        return result("tennis", "itf_men", "match_winner", "men", "pm_tennis_signal", "high")
    if slug.startswith("atp-") and ("atp challenger" in text or "challenger" in title_text):
        return result("tennis", "atp_challenger", infer_pm_market_type("tennis", "atp_challenger", slug, title), "men", "pm_tennis_signal", "high")
    if slug.startswith("wta-") or " wta" in f" {text}" or "women's" in text or "womens" in text or "women " in text:
        return result("tennis", "wta", infer_pm_market_type("tennis", "wta", slug, title), "women", "pm_tennis_signal", "high")
    if slug.startswith("atp-") or " atp" in f" {text}":
        return result("tennis", "atp", infer_pm_market_type("tennis", "atp", slug, title), "men", "pm_tennis_signal", "high")
    if re.search(r"\bW\d{2,3}\b", title):
        return result("tennis", "itf_women", "match_winner", "women", "pm_tennis_signal", "high")
    if re.search(r"\bM\d{2,3}\b", title):
        return result("tennis", "itf_men", "match_winner", "men", "pm_tennis_signal", "high")
    return None


def classify_ks_market(series_ticker: str, market_ticker: str, title: str) -> TaxonomyResult:
    series = series_ticker.upper()
    base = KS_SERIES_TAXONOMY.get(series)
    if base is None:
        base_result = result("unknown", "unknown", "unknown", "unknown", "ks_series_unknown", "low")
    else:
        base_result = result(*base)

    title_gender = gender_from_title(title)
    if title_gender and base_result.competition_gender in {"men", "women"} and title_gender != base_result.competition_gender:
        return result(
            base_result.category_key,
            base_result.universe,
            base_result.market_type,
            "unknown",
            "ks_gender_conflict",
            "low",
            f"KS series/title gender conflict: {series_ticker} {market_ticker}",
        )
    if title_gender and base_result.competition_gender == "unknown":
        return result(
            base_result.category_key,
            base_result.universe,
            base_result.market_type,
            title_gender,
            "ks_title",
            "medium",
        )
    return base_result


def gender_from_title(title: str) -> str:
    lowered = title.lower()
    if re.search(r"\bwomen(?:'s|s)?\b", lowered) or re.search(r"\bfemale\b", lowered):
        return "women"
    if re.search(r"\bmen(?:'s|s)?\b", lowered) or re.search(r"\bmale\b", lowered):
        return "men"
    if re.search(r"\bW\d{2,3}\b", title):
        return "women"
    if re.search(r"\bM\d{2,3}\b", title):
        return "men"
    return ""


def infer_pm_market_type(category_key: str, universe: str, slug: str, title: str) -> str:
    lowered = title.lower()
    if category_key in {"basketball", "baseball", "football", "hockey"}:
        if re.search(r"-\d{4}-\d{2}-\d{2}$", slug) and (" vs" in lowered or " at " in lowered or " @ " in lowered):
            return "game_winner"
    if category_key == "tennis" and MATCHUP_DELIMITER_RE.search(title):
        return "match_winner"
    if category_key == "esports" and MATCHUP_DELIMITER_RE.search(title):
        return "match_winner"
    if category_key == "combat" and MATCHUP_DELIMITER_RE.search(title):
        return "fighter_winner"
    if category_key in {"pickleball", "table_tennis"} and MATCHUP_DELIMITER_RE.search(title):
        return "match_winner"
    if universe == "world_cup" and re.search(r"-\d{4}-\d{2}-\d{2}$", slug):
        return "3_way_moneyline"
    if "champion" in lowered or "winner" in lowered:
        return "tournament_winner"
    return "unknown"


def event_date_from_pm(event_slug: str, start_date: str = "", title: str = "") -> str:
    return date_from_text(event_slug, start_date, title)


def event_date_from_ks(market_ticker: str, close_time: str = "", title: str = "") -> str:
    parsed = date_from_kalshi_ticker(market_ticker)
    if parsed:
        return parsed
    return date_from_text(close_time, title)


def date_from_text(*values: str) -> str:
    for value in values:
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(value or ""))
        if match:
            return match.group(1)
    return ""


def date_from_kalshi_ticker(value: str) -> str:
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", value.upper())
    if not match:
        return ""
    month = MONTHS.get(match.group(2))
    if month is None:
        return ""
    return date(2000 + int(match.group(1)), month, int(match.group(3))).isoformat()


def entity_key(category_key: str, universe: str, competition_gender: str, value: str) -> str:
    base = f"{category_key}:{universe}:{competition_gender}:"
    normalized = tennis_player_key(value) if category_key == "tennis" else normalize_key(value)
    if not normalized:
        return ""
    if universe == "wnba" and competition_gender == "women":
        return base + WNBA_BY_ALIAS.get(base + normalized, normalized)
    if universe == "nba" and competition_gender == "men":
        team = nba_common.team_from_name(value)
        return base + (str(team.id) if team is not None else normalized)
    return base + normalized


def result(
    category_key: str,
    universe: str,
    market_type: str,
    competition_gender: str,
    gender_source: str,
    gender_confidence: str,
    warning_message: str = "",
) -> TaxonomyResult:
    gender = competition_gender if competition_gender in ALLOWED_GENDERS else "unknown"
    return TaxonomyResult(
        category_key=category_key or "unknown",
        universe=universe or "unknown",
        market_type=market_type or "unknown",
        competition_gender=gender,
        gender_source=gender_source,
        gender_confidence=gender_confidence or "low",
        warning_message=warning_message,
    )
