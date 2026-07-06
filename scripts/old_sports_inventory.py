from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import old_pipeline_core as core
import old_sports_registry
import old_universe_adapters


INVENTORY_FIELDS = [
    "ts_utc",
    "source",
    "category_key",
    "category_label",
    "parent_category",
    "sport_codes",
    "tag_slug",
    "tag_id",
    "series",
    "event_id",
    "event_slug",
    "event_title",
    "event_start_date",
    "market_id",
    "market_question",
    "market_slug",
    "market_type_guess",
    "outcomes",
    "clob_token_ids",
    "active",
    "closed",
    "enable_order_book",
    "is_binary",
    "pm_volume",
    "pm_liquidity",
    "arb_universe",
    "arb_enabled",
    "kalshi_series_ticker",
    "adapter_name",
    "arb_status",
    "pairing_status",
    "schedule_source",
    "resolution_source",
    "risk_level",
    "notes",
]


def build_polymarket_old_sports_inventory(limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    ts_utc = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()
    for category in old_sports_registry.inventory_categories():
        category_rows, category_warnings = build_category_inventory_rows(category, limit, ts_utc)
        warnings.extend(category_warnings)
        for row in category_rows:
            key = (str(row["category_key"]), str(row["tag_slug"]), str(row["event_slug"]), str(row["market_id"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: (row["category_key"], row["tag_slug"], row["event_start_date"], row["event_slug"], row["market_id"]))
    return rows, warnings


def build_category_inventory_rows(
    category: old_sports_registry.SportsCategory,
    limit: int,
    ts_utc: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    ts = ts_utc or datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for tag_slug, tag_id, events in fetch_category_events(category, limit, warnings):
        for event in events:
            event_slug = str(event.get("slug") or "")
            markets = event.get("markets") or []
            if not markets:
                key = (event_slug, "")
                if key not in seen:
                    seen.add(key)
                    rows.append(event_inventory_row(ts, category, tag_slug, tag_id, event, None))
                continue
            for market in markets:
                key = (event_slug, str(market.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(event_inventory_row(ts, category, tag_slug, tag_id, event, market))
    rows.sort(key=lambda row: (row["event_start_date"], row["event_slug"], row["market_id"]))
    return rows, warnings


def fetch_category_events(
    category: old_sports_registry.SportsCategory,
    limit: int,
    warnings: list[str],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    event_groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    for tag_slug in category.tag_slugs:
        try:
            event_groups.append((tag_slug, "", core.fetch_polymarket_events((tag_slug,), limit)))
        except Exception as exc:  # noqa: BLE001 - inventory should not stop arb snapshots.
            warnings.append(f"inventory fetch failed for {category.key} tag_slug={tag_slug}: {exc}")
    for tag_id in category.tag_ids:
        try:
            event_groups.append((f"tag_id:{tag_id}", str(tag_id), core.fetch_polymarket_events_by_tag_ids((tag_id,), limit)))
        except Exception as exc:  # noqa: BLE001 - inventory should not stop arb snapshots.
            warnings.append(f"inventory fetch failed for {category.key} tag_id={tag_id}: {exc}")
    return event_groups


def event_inventory_row(
    ts_utc: str,
    category: old_sports_registry.SportsCategory,
    tag_slug: str,
    tag_id: str,
    event: dict[str, Any],
    market: dict[str, Any] | None,
) -> dict[str, Any]:
    outcomes = core.parse_json_list(market.get("outcomes")) if market else []
    token_ids = core.parse_json_list(market.get("clobTokenIds")) if market else []
    market_active = bool(market.get("active")) if market else False
    market_closed = bool(market.get("closed")) if market else False
    enable_order_book = bool(market.get("enableOrderBook", True)) if market else False
    return {
        "ts_utc": ts_utc,
        "source": "polymarket",
        "category_key": category.key,
        "category_label": category.label,
        "parent_category": category.parent,
        "sport_codes": " | ".join(category.sport_codes),
        "tag_slug": tag_slug,
        "tag_id": tag_id,
        "series": " | ".join(category.series),
        "event_id": str(event.get("id") or ""),
        "event_slug": str(event.get("slug") or ""),
        "event_title": str(event.get("title") or ""),
        "event_start_date": str(event.get("startDate") or event.get("endDate") or ""),
        "market_id": str(market.get("id") or "") if market else "",
        "market_question": str(market.get("question") or market.get("groupItemTitle") or "") if market else "",
        "market_slug": str(market.get("slug") or "") if market else "",
        "market_type_guess": guess_market_type(outcomes, market),
        "outcomes": " | ".join(str(outcome) for outcome in outcomes),
        "clob_token_ids": " | ".join(str(token_id) for token_id in token_ids),
        "active": str(market_active).lower(),
        "closed": str(market_closed).lower(),
        "enable_order_book": str(enable_order_book).lower(),
        "is_binary": str(len(outcomes) == 2).lower(),
        "pm_volume": first_present(market, event, ("volume", "volumeNum", "volume24hr", "volume24hrClob")),
        "pm_liquidity": first_present(market, event, ("liquidity", "liquidityNum", "liquidityClob")),
        "arb_universe": category.arb_universe,
        "arb_enabled": str(category.arb_enabled).lower(),
        "kalshi_series_ticker": category.kalshi_series_ticker,
        "adapter_name": category.adapter_name,
        "arb_status": category.arb_status,
        "pairing_status": pairing_status(category),
        "schedule_source": category.schedule_source,
        "resolution_source": category.resolution_source,
        "risk_level": category.risk_level,
        "notes": category.notes,
    }


def first_present(
    market: dict[str, Any] | None,
    event: dict[str, Any],
    keys: tuple[str, ...],
) -> str:
    sources = [source for source in (market, event) if source]
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def pairing_status(category: old_sports_registry.SportsCategory) -> str:
    if category.arb_enabled:
        return "arb_enabled"
    if category.key == "esports":
        return "inventory_only_esports"
    if category.kalshi_series_ticker:
        return "adapter_pending"
    return "inventory_only"


def guess_market_type(outcomes: list[Any], market: dict[str, Any] | None) -> str:
    question = str((market or {}).get("question") or (market or {}).get("groupItemTitle") or "").lower()
    labels = [str(outcome) for outcome in outcomes]
    lowered = [label.strip().lower() for label in labels]
    if len(outcomes) == 3 and any(old_universe_adapters.is_draw_or_tie(label) for label in labels):
        return "three_way_moneyline"
    if len(outcomes) == 2 and lowered == ["yes", "no"]:
        if "spread" in question or "handicap" in question:
            return "binary_spread"
        if "total" in question or "over" in question or "under" in question:
            return "binary_total"
        return "binary_yes_no"
    if len(outcomes) == 2 and set(lowered) == {"over", "under"}:
        return "total"
    if len(outcomes) == 2:
        return "two_outcome_winner"
    if len(outcomes) > 2:
        return "multi_outcome"
    return "unknown"
