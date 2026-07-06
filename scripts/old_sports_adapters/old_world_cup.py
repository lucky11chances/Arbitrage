from __future__ import annotations

import old_sports_pairing
from old_sports_adapters.old_base import SportAdapter
import old_sports_registry


ADAPTER = SportAdapter(
    category=old_sports_registry.category_for_key("world_cup"),
    pairer=lambda pm_limit, ks_limit, from_date: old_sports_pairing.db_category_pairing(
        "world_cup",
        pm_limit,
        ks_limit,
        from_date,
    ),
    diagnostics_builder=lambda pm_limit, ks_limit, from_date: old_sports_pairing.official_pairer_diagnostics(
        "world_cup",
        None,
        pm_limit,
        ks_limit,
        from_date,
    ),
)
