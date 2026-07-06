from __future__ import annotations

import old_sports_pairing
import old_sports_registry
from old_sports_adapters.old_base import SportAdapter


ADAPTER = SportAdapter(
    category=old_sports_registry.category_for_key("formula_1"),
    pairer=old_sports_pairing.f1_drivers_championship_pairing,
)
