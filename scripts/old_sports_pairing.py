from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import old_market_db
import old_pipeline_core as core
import old_staging_pair_from_db


"""
Compatibility bridge for legacy adapter imports.

The staging DB pairer in `old_staging_pair_from_db.py` is the source of truth for
PM/KS safe sports pairing. The previous live-fetch matcher was archived under
`archive/legacy_pairing_2026-06-30/` and should not be used by runtime paths.
"""


DIAGNOSTIC_FIELDS = old_staging_pair_from_db.DIAGNOSTIC_FIELDS
MATCH_SOURCE = old_staging_pair_from_db.MATCH_SOURCE
NORMAL_SPORTS_MARKET_TYPES = old_staging_pair_from_db.SAFE_MARKET_TYPES
DEFAULT_DB_PATH = old_market_db.DEFAULT_DB_PATH
LEGACY_ARCHIVE_PATH = Path("archive/legacy_pairing_2026-06-30/scripts/old_sports_pairing.py")


def normalize_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blank_diagnostic_row(category_key: str, status: str, reason: str) -> dict[str, Any]:
    row = {field: "" for field in DIAGNOSTIC_FIELDS}
    row.update(
        {
            "ts_utc": _utc_now(),
            "category_key": category_key,
            "universe": category_key,
            "status": status,
            "reason": reason,
            "safe_paired": "false",
        }
    )
    return row


def empty_status(category_key: str, status: str, reason: str) -> dict[str, Any]:
    return _blank_diagnostic_row(category_key, status, reason)


def pair_status_row(category_key: str, pair: core.PairedContract, status: str, reason: str) -> dict[str, Any]:
    row = _blank_diagnostic_row(category_key, status, reason)
    row.update(
        {
            "universe": pair.universe,
            "safe_paired": "true" if status in {"paired", "paired_existing"} else "false",
            "event_date": pair.event_date,
            "canonical_event_id": pair.canonical_event_id,
            "market_type": pair.market_type,
            "match_name": pair.match_name,
            "outcome": pair.pm_yes_outcome,
            "pm_event_slug": pair.pm_event_slug,
            "pm_market_id": pair.pm_market_id,
            "pm_token_id": pair.pm_token_id,
            "ks_event_ticker": pair.ks_event_ticker,
            "ks_market_ticker": pair.ks_market_ticker,
            "ks_yes_outcome": pair.ks_yes_outcome,
            "entity_key": pair.canonical_event_id,
            "outcome_key": normalize_key(pair.pm_yes_outcome),
        }
    )
    return row


def _sports_arg_for_category(category_or_key: Any) -> str:
    key = str(getattr(category_or_key, "key", category_or_key) or "").strip().lower().replace("-", "_")
    universe = str(getattr(category_or_key, "arb_universe", "") or "").strip().lower().replace("-", "_")
    sport_codes = tuple(getattr(category_or_key, "sport_codes", ()) or ())

    if key == "basketball":
        return "wnba"
    if key == "football":
        return "nfl,cfb"
    if key == "tennis":
        return "tennis"
    if key == "esports":
        return "esports"
    if universe:
        return universe
    if sport_codes:
        return ",".join(str(code).strip().lower().replace("-", "_") for code in sport_codes if str(code).strip())
    return key or "all"


def _load_db_pairing(
    *,
    sports: str,
    from_date: str | None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    with old_market_db.connect(Path(db_path)) as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        old_market_db.init_db(conn)
        pairs, diagnostics, warnings, _counts = old_staging_pair_from_db.build_pairs_csv_only(
            conn,
            sports=sports,
            from_date=from_date,
        )
    warnings = [
        "legacy old_sports_pairing bridge used DB-only old_staging_pair_from_db; archived old matcher is "
        f"at {LEGACY_ARCHIVE_PATH}",
        *warnings,
    ]
    return pairs, diagnostics, warnings


def db_category_pairing(
    category_or_key: Any,
    pm_limit: int = 0,
    ks_limit: int = 0,
    from_date: str | None = None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> tuple[list[core.PairedContract], list[str]]:
    del pm_limit, ks_limit
    sports = _sports_arg_for_category(category_or_key)
    pairs, _diagnostics, warnings = _load_db_pairing(sports=sports, from_date=from_date, db_path=db_path)
    return pairs, warnings


def build_category_pairing(
    category: Any,
    pm_limit: int,
    ks_limit: int,
    from_date: str | None = None,
) -> tuple[list[core.PairedContract], list[dict[str, Any]], list[str]]:
    del pm_limit, ks_limit
    sports = _sports_arg_for_category(category)
    return _load_db_pairing(sports=sports, from_date=from_date)


def build_category_diagnostics(
    category: Any,
    pm_limit: int,
    ks_limit: int,
    from_date: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    _pairs, rows, warnings = build_category_pairing(category, pm_limit, ks_limit, from_date)
    if not rows:
        rows = [
            empty_status(
                str(getattr(category, "key", category) or ""),
                "unmatched_semantics",
                "DB-only staging pairer returned no rows for this category",
            )
        ]
    return rows, warnings


def official_pairer_diagnostics(
    category_key: str,
    pairer,
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    del pairer, pm_limit, ks_limit
    _pairs, diagnostics, warnings = _load_db_pairing(sports=category_key, from_date=from_date)
    if not diagnostics:
        diagnostics = [
            empty_status(
                category_key,
                "unmatched_semantics",
                "DB-only staging pairer returned no rows for this category",
            )
        ]
    return diagnostics, warnings


def f1_drivers_championship_pairing(
    pm_limit: int,
    ks_limit: int,
    from_date: str | None,
) -> tuple[list[core.PairedContract], list[str]]:
    pairs, warnings = db_category_pairing("formula_1", pm_limit, ks_limit, from_date)
    warnings.append("formula_1 is not currently promoted by the DB-only safe pairer")
    return pairs, warnings
