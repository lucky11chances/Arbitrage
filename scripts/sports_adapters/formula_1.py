from __future__ import annotations

import sports_pairing
import sports_registry
from sports_adapters.base import SportAdapter


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("formula_1"),
    pairer=sports_pairing.f1_drivers_championship_pairing,
)
