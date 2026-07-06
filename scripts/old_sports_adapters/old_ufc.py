from __future__ import annotations

from typing import Any

import old_pipeline_core as core
import old_sports_pairing
import old_sports_registry
from old_sports_adapters.old_base import SportAdapter


def pair_ufc(pm_limit: int, ks_limit: int, from_date: str | None) -> tuple[list[core.PairedContract], list[str]]:
    return old_sports_pairing.db_category_pairing("ufc", pm_limit, ks_limit, from_date)


def build_ufc_diagnostics(
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    return old_sports_pairing.official_pairer_diagnostics("ufc", None, pm_limit, ks_limit, from_date)


ADAPTER = SportAdapter(
    category=old_sports_registry.category_for_key("ufc"),
    pairer=pair_ufc,
    diagnostics_builder=build_ufc_diagnostics,
)
