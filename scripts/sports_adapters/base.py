from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pipeline_core as core
import sports_inventory
import sports_registry


Pairer = Callable[[int, int, str | None], tuple[list[core.PairedContract], list[str]]]
DiagnosticsBuilder = Callable[[int, int, str | None], tuple[list[dict[str, Any]], list[str]]]


@dataclass(frozen=True)
class SportAdapter:
    category: sports_registry.SportsCategory
    pairer: Pairer | None = None
    diagnostics_builder: DiagnosticsBuilder | None = None

    @property
    def key(self) -> str:
        return self.category.key

    @property
    def output_path(self) -> Path:
        return Path("data/sports") / f"{self.key}_latest.csv"

    @property
    def history_prefix(self) -> str:
        return f"sports_{self.key}"

    def build_inventory_rows(self, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
        return sports_inventory.build_category_inventory_rows(self.category, limit)

    def pair_contracts(
        self,
        pm_limit: int,
        ks_limit: int,
        from_date: str | None,
    ) -> tuple[list[core.PairedContract], list[str]]:
        if self.pairer is None:
            return [], [f"{self.key} is inventory-only; no PM/Kalshi arb pairing is enabled."]
        return self.pairer(pm_limit, ks_limit, from_date)

    def build_pairing_diagnostics(
        self,
        pm_limit: int,
        ks_limit: int,
        from_date: str | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if self.diagnostics_builder is not None:
            return self.diagnostics_builder(pm_limit, ks_limit, from_date)
        import sports_pairing

        return sports_pairing.build_category_diagnostics(self.category, pm_limit, ks_limit, from_date)


def inventory_adapter(key: str) -> SportAdapter:
    return SportAdapter(category=sports_registry.category_for_key(key))
