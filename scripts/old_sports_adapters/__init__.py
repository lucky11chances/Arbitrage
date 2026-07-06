from __future__ import annotations

from collections.abc import Iterable

import old_sports_registry
from old_sports_adapters.old_base import SportAdapter
from old_sports_adapters import (
    old_baseball as baseball,
    old_basketball as basketball,
    old_boxing as boxing,
    old_cricket as cricket,
    old_esports as esports,
    old_football as football,
    old_formula_1 as formula_1,
    old_golf as golf,
    old_hockey as hockey,
    old_lacrosse as lacrosse,
    old_mlb as mlb,
    old_nba as nba,
    old_pickleball as pickleball,
    old_rugby as rugby,
    old_soccer as soccer,
    old_table_tennis as table_tennis,
    old_tennis as tennis,
    old_ufc as ufc,
    old_world_cup as world_cup,
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
    return [ADAPTERS[key] for key in old_sports_registry.adapter_keys()]


def get_adapter(key: str) -> SportAdapter:
    return ADAPTERS[old_sports_registry.normalize_sport_key(key)]


def select_adapters(raw_sports: str) -> list[SportAdapter]:
    requested = [sport.strip().lower() for sport in raw_sports.split(",") if sport.strip()]
    if not requested or "all" in requested:
        if len(requested) > 1:
            raise ValueError("--sports all cannot be combined with explicit sports")
        return all_adapters()
    keys = old_sports_registry.adapter_keys_with_legacy_expansion(requested)
    return [get_adapter(key) for key in keys]


def paired_adapters(adapters: Iterable[SportAdapter]) -> list[SportAdapter]:
    return [adapter for adapter in adapters if adapter.category.arb_status == "paired"]
