from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable


ACTIVE_STATUSES = {"", "open", "active"}
DISALLOWED_OUTCOME_NAMES = {"yes", "no", "over", "under", "draw", "tie", "push", "other", "field"}

HARD_EXCLUSION_RE = re.compile(
    r"\b("
    r"option|options|perpetual|perpetuals|perp|perps|derivative|derivatives|"
    r"future|futures|prop|props|parlay|spread|spreads|handicap|handicaps|"
    r"total|totals|over/?under|o/u|over|under|draw|tie|3-way|three-way|"
    r"map\s*\d*|set\s*\d*|quarter|period|inning|first half|second half|"
    r"points?|goals?|runs?|hits?|strikeouts?|rebounds?|assists?"
    r")\b",
    re.IGNORECASE,
)
OUTRIGHT_EXCLUSION_RE = re.compile(
    r"\b("
    r"outright|to win (?:the )?(?:series|tournament|championship|league|division|conference|cup|season|playoffs?)|"
    r"(?:series|tournament|championship|league|division|conference|cup|season|playoffs?) (?:winner|champion)|"
    r"(?:winner|champion) of (?:the )?(?:series|tournament|championship|league|division|conference|cup|season|playoffs?)"
    r")\b",
    re.IGNORECASE,
)
MATCHUP_RE = re.compile(r"\b(?:vs\.?|v\.?|at|@)\b|[A-Za-z0-9]\s+[-–—]\s+[A-Za-z0-9]", re.IGNORECASE)


def parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def is_open_status(value: Any) -> bool:
    return str(value or "").strip().lower() in ACTIVE_STATUSES


def _boolish(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "open", "active"}:
        return True
    if text in {"0", "false", "no", "n", "closed", "inactive"}:
        return False
    return default


def _joined_text(values: Iterable[Any]) -> str:
    return " ".join(str(value or "") for value in values if value is not None)


def has_disallowed_market_text(*values: Any) -> bool:
    text = _joined_text(values)
    if not text:
        return False
    return bool(HARD_EXCLUSION_RE.search(text) or OUTRIGHT_EXCLUSION_RE.search(text))


def _binary_participant_outcomes(outcomes: list[Any]) -> bool:
    if len(outcomes) != 2:
        return False
    keys = [normalized_key(outcome) for outcome in outcomes]
    if not all(keys):
        return False
    if len(set(keys)) != 2:
        return False
    return not any(key in DISALLOWED_OUTCOME_NAMES for key in keys)


def _pm_market_type_allowed(market: dict[str, Any]) -> bool:
    market_type = normalized_key(
        market.get("sportsMarketType")
        or market.get("sports_market_type")
        or market.get("marketType")
        or market.get("market_type")
    )
    if not market_type:
        return True
    allowed = {"winner", "moneyline", "match winner", "matchwinner", "game winner", "gamewinner"}
    return market_type in allowed


def is_pm_binary_winner_market(event: dict[str, Any], market: dict[str, Any]) -> bool:
    if not isinstance(event, dict) or not isinstance(market, dict):
        return False
    if not _boolish(event.get("active"), default=True) or _boolish(event.get("closed"), default=False):
        return False
    if not _boolish(market.get("active"), default=True) or _boolish(market.get("closed"), default=False):
        return False
    if not _boolish(market.get("enableOrderBook", market.get("enable_order_book")), default=True):
        return False
    outcomes = parse_json_list(market.get("outcomes"))
    token_ids = parse_json_list(market.get("clobTokenIds") or market.get("clob_token_ids"))
    if not _binary_participant_outcomes(outcomes):
        return False
    if len(token_ids) != 2 or not all(str(token or "").strip() for token in token_ids):
        return False
    if not _pm_market_type_allowed(market):
        return False
    text_values = (
        event.get("title"),
        event.get("slug"),
        event.get("description"),
        market.get("question"),
        market.get("groupItemTitle"),
        market.get("slug"),
        market.get("description"),
        market.get("sportsMarketType"),
        market.get("marketType"),
    )
    if has_disallowed_market_text(*text_values):
        return False
    return True


def eligible_pm_markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        market
        for market in event.get("markets") or []
        if isinstance(market, dict) and is_pm_binary_winner_market(event, market)
    ]


def _ks_participant_name(market: dict[str, Any]) -> str:
    return str(
        market.get("yes_sub_title")
        or market.get("subtitle")
        or market.get("participant")
        or market.get("yes_title")
        or ""
    ).strip()


def _ks_event_ticker(market: dict[str, Any]) -> str:
    return str(market.get("event_ticker") or "").strip()


def eligible_ks_binary_event_markets(markets_for_event: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    markets = [market for market in markets_for_event if isinstance(market, dict)]
    if len(markets) != 2:
        return []
    if any(not is_open_status(market.get("status")) for market in markets):
        return []
    outcomes = [_ks_participant_name(market) for market in markets]
    if not _binary_participant_outcomes(outcomes):
        return []
    event_tickers = {_ks_event_ticker(market) for market in markets}
    if len(event_tickers - {""}) > 1:
        return []
    for market in markets:
        if has_disallowed_market_text(
            market.get("title"),
            market.get("market_ticker"),
            market.get("ticker"),
            market.get("event_ticker"),
            market.get("yes_sub_title"),
            market.get("subtitle"),
            market.get("rules_primary"),
            market.get("settlement_sources"),
        ):
            return []
    return markets


def eligible_ks_market_groups(markets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        event_ticker = _ks_event_ticker(market)
        if not event_ticker:
            continue
        if event_ticker not in grouped:
            order.append(event_ticker)
        grouped[event_ticker].append(market)
    eligible: list[dict[str, Any]] = []
    for event_ticker in order:
        eligible.extend(eligible_ks_binary_event_markets(grouped[event_ticker]))
    return eligible
