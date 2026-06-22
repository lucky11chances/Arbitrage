from __future__ import annotations

import universe_adapters
import sports_pairing
from sports_adapters.base import SportAdapter
import sports_registry


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("mlb"),
    pairer=universe_adapters.pair_mlb,
    diagnostics_builder=lambda pm_limit, ks_limit, from_date: sports_pairing.official_pairer_diagnostics(
        "mlb",
        universe_adapters.pair_mlb,
        pm_limit,
        ks_limit,
        from_date,
    ),
)
