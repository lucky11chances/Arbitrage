from __future__ import annotations

from typing import Any

import old_pipeline_core as core
import old_sports_pairing
import old_sports_registry
from old_sports_adapters.old_base import SportAdapter


def pair_wnba(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    return old_sports_pairing.db_category_pairing("wnba", pm_limit, ks_limit, from_date)


def build_basketball_diagnostics(
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    return old_sports_pairing.official_pairer_diagnostics("wnba", None, pm_limit, ks_limit, from_date)


ADAPTER = SportAdapter(
    category=old_sports_registry.category_for_key("basketball"),
    pairer=pair_wnba,
    diagnostics_builder=build_basketball_diagnostics,
)
