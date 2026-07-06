from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class IndexedParticipant:
    participant_key: str
    display_name: str
    source_participant_id: str = ""
    aliases: tuple[str, ...] = ()
    role: str = ""
    order: int = 0


@dataclass(frozen=True)
class IndexedEvent:
    canonical_event_id: str
    category_key: str
    universe: str
    event_date: str
    market_type: str
    competition_gender: str
    match_name: str
    source_key: str
    source_event_id: str
    source_type: str
    source_confidence: str
    source_url: str = ""
    start_time_utc: str = ""
    sport_key: str = ""
    competition_key: str = ""
    season: str = ""
    event_name: str = ""
    source_local_date: str = ""
    venue_id: str = ""
    venue_name: str = ""
    venue_city: str = ""
    venue_region: str = ""
    venue_country: str = ""
    participants: tuple[IndexedParticipant, ...] = ()
    raw_payload: dict[str, Any] | None = None


def alias_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def participant_key(category_key: str, universe: str, competition_gender: str, value: str) -> str:
    return f"{category_key}:{universe}:{competition_gender}:{alias_key(value)}"


def canonical_event_id_from_source(competition_key: str, source_key: str, source_event_id: str) -> str:
    return f"{competition_key}:{source_key}:{alias_key(source_event_id)}"


def fallback_canonical_event_id(
    competition_key: str,
    event_date: str,
    market_type: str,
    participants: tuple[IndexedParticipant, ...],
) -> str:
    participant_keys = "|".join(sorted(participant.participant_key for participant in participants))
    return f"{competition_key}:{event_date}:{market_type}:{participant_keys}"


def normalize_utc_iso(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parseable = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sports_requested(sports: str) -> set[str]:
    return {part.strip().lower().replace("-", "_") for part in sports.split(",") if part.strip()}


def include_sport(category_key: str, universe: str, sports: str) -> bool:
    requested = sports_requested(sports)
    if not requested or "all" in requested:
        return True
    return category_key in requested or universe in requested


def team_aliases(name: str, *extras: str) -> tuple[str, ...]:
    parts = [part for part in re.split(r"\s+", name.strip()) if part]
    aliases = {name, *[extra for extra in extras if extra]}
    if len(parts) >= 2:
        aliases.add(parts[0])
        aliases.add(parts[-1])
        if len(parts) >= 3:
            aliases.add(" ".join(parts[:-1]))
            aliases.add(" ".join(parts[-2:]))
    return tuple(sorted(aliases, key=lambda item: (alias_key(item), item)))
