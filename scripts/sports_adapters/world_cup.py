from __future__ import annotations

import universe_adapters
import sports_pairing
from sports_adapters.base import SportAdapter
import sports_registry


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("world_cup"),
    pairer=universe_adapters.pair_worldcup_soccer,
    diagnostics_builder=lambda pm_limit, ks_limit, from_date: sports_pairing.official_pairer_diagnostics(
        "world_cup",
        universe_adapters.pair_worldcup_soccer,
        pm_limit,
        ks_limit,
        from_date,
    ),
)
