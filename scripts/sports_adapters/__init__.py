from __future__ import annotations

from collections.abc import Iterable

import sports_registry
from sports_adapters.base import SportAdapter
from sports_adapters import (
    baseball,
    basketball,
    boxing,
    cricket,
    esports,
    football,
    formula_1,
    golf,
    hockey,
    lacrosse,
    mlb,
    nba,
    pickleball,
    rugby,
    soccer,
    table_tennis,
    tennis,
    ufc,
    world_cup,
)


ADAPTERS: dict[str, SportAdapter] = {
    adapter.category.key: adapter
    for adapter in (
        world_cup.ADAPTER,
        mlb.ADAPTER,
        nba.ADAPTER,
        ufc.ADAPTER,
        football.ADAPTER,
        soccer.ADAPTER,
        tennis.ADAPTER,
        cricket.ADAPTER,
        basketball.ADAPTER,
        baseball.ADAPTER,
        rugby.ADAPTER,
        table_tennis.ADAPTER,
        golf.ADAPTER,
        formula_1.ADAPTER,
        boxing.ADAPTER,
        pickleball.ADAPTER,
        lacrosse.ADAPTER,
        hockey.ADAPTER,
        esports.ADAPTER,
    )
}


def all_adapters() -> list[SportAdapter]:
    return [ADAPTERS[key] for key in sports_registry.adapter_keys()]


def get_adapter(key: str) -> SportAdapter:
    return ADAPTERS[sports_registry.normalize_sport_key(key)]


def select_adapters(raw_sports: str) -> list[SportAdapter]:
    requested = [sport.strip().lower() for sport in raw_sports.split(",") if sport.strip()]
    if not requested or "all" in requested:
        if len(requested) > 1:
            raise ValueError("--sports all cannot be combined with explicit sports")
        return all_adapters()
    keys = sports_registry.adapter_keys_with_legacy_expansion(requested)
    return [get_adapter(key) for key in keys]


def paired_adapters(adapters: Iterable[SportAdapter]) -> list[SportAdapter]:
    return [adapter for adapter in adapters if adapter.category.arb_status == "paired"]
