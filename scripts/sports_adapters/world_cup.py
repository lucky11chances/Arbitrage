from __future__ import annotations

import universe_adapters
from sports_adapters.base import SportAdapter
import sports_registry


ADAPTER = SportAdapter(
    category=sports_registry.category_for_key("world_cup"),
    pairer=universe_adapters.pair_worldcup_soccer,
)
