#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import old_market_db
import old_pipeline_core as core
import old_sports_market_filters as market_filters
import old_sports_adapters
import old_sports_registry
import old_sports_taxonomy
from old_sports_adapters.old_base import SportAdapter


DEFAULT_PM_REPAIR_SLUGS = (
    "atp-majchrz-tabilo-2026-06-29",
    "atp-kovacev-zandsch-2026-06-29",
    "cs2-berg-aimhau-2026-06-29",
)
PM_FALLBACK_UNIVERSES = {"atp", "wta", "cs2"}
PM_FALLBACK_STRIP_WORDS = {"esport", "esports"}


def parse_adapters(value: str) -> list[SportAdapter]:
    try:
        return old_sports_adapters.select_adapters(value)
    except (KeyError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def fetch_pm_events_by_tag_slug(tag_slug: str, page_limit: int, max_pages: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    page = 0
    while True:
        if max_pages and page >= max_pages:
            break
        payload = core.get_json(
            core.PM_GAMMA,
            "/events",
            {
                "tag_slug": tag_slug,
                "active": "true",
                "closed": "false",
                "limit": page_limit,
                "offset": offset,
                "order": "startDate",
                "ascending": "true",
            },
        )
        if not isinstance(payload, list) or not payload:
            break
        page += 1
        for event in payload:
            slug = str(event.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            events.append(event)
        if len(payload) < page_limit:
            break
        offset += len(payload)
    return events


def fetch_pm_events_by_tag_id(tag_id: int, page_limit: int, max_pages: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    page = 0
    while True:
        if max_pages and page >= max_pages:
            break
        payload = core.get_json(
            core.PM_GAMMA,
            "/events",
            {
                "tag_id": tag_id,
                "active": "true",
                "closed": "false",
                "limit": page_limit,
                "offset": offset,
                "order": "startDate",
                "ascending": "true",
            },
        )
        if not isinstance(payload, list) or not payload:
            break
        page += 1
        for event in payload:
            slug = str(event.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            events.append(event)
        if len(payload) < page_limit:
            break
        offset += len(payload)
    return events


def normalize_slug_token(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def name_slug_parts(name: str, universe: str) -> tuple[str, ...]:
    ascii_text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    tokens = [token for token in re.findall(r"[a-z0-9]+", ascii_text) if token]
    parts: set[str] = set()
    if not tokens:
        return ()
    if universe in {"atp", "wta"}:
        surname = tokens[-1]
        parts.add(surname)
        if len(tokens) >= 3:
            parts.add("".join(tokens[-2:]))
    elif universe == "cs2":
        without_esports = [token for token in tokens if token not in PM_FALLBACK_STRIP_WORDS]
        if without_esports:
            parts.add("".join(without_esports))
            parts.add(without_esports[-1])
        parts.add("".join(tokens))
    else:
        parts.add("".join(tokens))
    shortened = set(parts)
    for part in list(parts):
        if len(part) > 7:
            shortened.add(part[:7])
        if len(part) > 6:
            shortened.add(part[:6])
    return tuple(sorted(part for part in shortened if part))


def pm_slug_prefix_for_universe(universe: str) -> str:
    return {"atp": "atp", "wta": "wta", "cs2": "cs2"}.get(universe, "")


def pm_source_tag_for_slug(slug: str) -> str:
    prefix = slug.split("-", 1)[0].lower()
    if prefix in {"atp", "wta", "itf"}:
        return "tennis"
    if prefix in {"cs2", "lol", "valorant"}:
        return prefix
    return prefix


def fetch_pm_events_by_slug(slug: str) -> list[dict[str, Any]]:
    payload = core.get_json(
        core.PM_GAMMA,
        "/events",
        {
            "slug": slug,
            "active": "true",
            "closed": "false",
            "limit": 10,
        },
    )
    events = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    return [event for event in events if isinstance(event, dict) and str(event.get("slug") or "") == slug]


def fetch_pm_events_by_search(query: str, page_limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("search", "q"):
        try:
            payload = core.get_json(
                core.PM_GAMMA,
                "/events",
                {
                    key: query,
                    "active": "true",
                    "closed": "false",
                    "limit": page_limit,
                    "order": "startDate",
                    "ascending": "true",
                },
            )
        except Exception:
            if key == "q":
                raise
            continue
        page_events = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) else []
        for event in page_events:
            if not isinstance(event, dict):
                continue
            slug = str(event.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            events.append(event)
        if events:
            break
    return events


def ks_binary_events_for_pm_fallback(conn, sports: str) -> list[tuple[str, str, str, tuple[str, str]]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT market_ticker, event_ticker, series_ticker, title, yes_sub_title, close_time, status
        FROM ks_markets
        WHERE LOWER(COALESCE(status, 'open')) IN ('', 'open', 'active')
        ORDER BY event_ticker, market_ticker
        """
    ):
        grouped[str(row["event_ticker"] or "")].append(row)

    events: list[tuple[str, str, str, tuple[str, str]]] = []
    for event_ticker, rows in grouped.items():
        if len(rows) != 2:
            continue
        classification = old_sports_taxonomy.classify_ks_market(
            str(rows[0]["series_ticker"] or ""),
            str(rows[0]["market_ticker"] or ""),
            str(rows[0]["title"] or ""),
        )
        if not include_sport_for_fallback(classification.category_key, classification.universe, sports):
            continue
        if classification.universe not in PM_FALLBACK_UNIVERSES:
            continue
        event_date = old_sports_taxonomy.event_date_from_ks(event_ticker, str(rows[0]["close_time"] or ""), str(rows[0]["title"] or ""))
        outcomes = tuple(str(row["yes_sub_title"] or "") for row in rows)
        if event_date and all(outcomes):
            events.append((classification.universe, event_date, event_ticker, outcomes))
    return events


def include_sport_for_fallback(category_key: str, universe: str, sports: str) -> bool:
    requested = {part.strip().lower().replace("-", "_") for part in sports.split(",") if part.strip()}
    if not requested or "all" in requested:
        return True
    return category_key in requested or universe in requested


def candidate_pm_slugs_from_ks_event(universe: str, event_date: str, outcomes: tuple[str, str]) -> set[str]:
    prefix = pm_slug_prefix_for_universe(universe)
    if not prefix or len(outcomes) != 2:
        return set()
    left_parts = name_slug_parts(outcomes[0], universe)
    right_parts = name_slug_parts(outcomes[1], universe)
    slugs: set[str] = set()
    for left in left_parts:
        for right in right_parts:
            if left and right and left != right:
                slugs.add(f"{prefix}-{left}-{right}-{event_date}")
                slugs.add(f"{prefix}-{right}-{left}-{event_date}")
    return slugs


def collect_pm_repair_metadata(conn, args: argparse.Namespace) -> int:
    explicit_slugs = set(DEFAULT_PM_REPAIR_SLUGS)
    explicit_slugs.update(slug.strip() for slug in getattr(args, "pm_extra_slug", []) if slug.strip())

    generated_slugs: set[str] = set()
    search_queries: list[str] = []
    for universe, event_date, _event_ticker, outcomes in ks_binary_events_for_pm_fallback(conn, args.sports):
        generated_slugs.update(candidate_pm_slugs_from_ks_event(universe, event_date, outcomes))
        if getattr(args, "pm_fallback_search", False):
            search_queries.append(" ".join((*outcomes, event_date)))

    max_slugs = max(getattr(args, "pm_fallback_max_slugs", 0), 0)
    candidate_slugs = sorted(explicit_slugs | generated_slugs)
    if max_slugs:
        always_keep = sorted(explicit_slugs)
        generated_only = [slug for slug in candidate_slugs if slug not in explicit_slugs]
        candidate_slugs = always_keep + generated_only[: max(max_slugs - len(always_keep), 0)]

    existing_slugs = {str(row["event_slug"] or "") for row in conn.execute("SELECT event_slug FROM pm_events")}
    count = 0
    for slug in candidate_slugs:
        if slug in existing_slugs:
            continue
        try:
            events = fetch_pm_events_by_slug(slug)
        except Exception as exc:  # noqa: BLE001 - fallback should not stop the collector.
            old_market_db.record_warning(
                conn,
                "collect_pm_metadata",
                f"PM slug fallback failed: {slug}: {exc}",
                severity="warning",
                context={"slug": slug},
            )
            continue
        for event in events:
            old_market_db.upsert_pm_event(conn, event, tag_slug=pm_source_tag_for_slug(slug))
            existing_slugs.add(slug)
            count += 1
        conn.commit()

    if getattr(args, "pm_fallback_search", False):
        seen_slugs = {str(row["event_slug"] or "") for row in conn.execute("SELECT event_slug FROM pm_events")}
        for query in search_queries[: max(getattr(args, "pm_fallback_max_searches", 50), 0)]:
            try:
                events = fetch_pm_events_by_search(query, args.pm_page_limit)
            except Exception as exc:  # noqa: BLE001
                old_market_db.record_warning(
                    conn,
                    "collect_pm_metadata",
                    f"PM search fallback failed: {query}: {exc}",
                    severity="warning",
                    context={"query": query},
                )
                continue
            for event in events:
                slug = str(event.get("slug") or "")
                if not slug or slug in seen_slugs:
                    continue
                old_market_db.upsert_pm_event(conn, event, tag_slug=pm_source_tag_for_slug(slug))
                seen_slugs.add(slug)
                count += 1
            conn.commit()
    return count


def collect_pm_metadata(conn, adapters: list[SportAdapter], args: argparse.Namespace) -> int:
    count = 0
    for adapter in adapters:
        category = adapter.category
        for tag_slug in category.tag_slugs:
            try:
                events = fetch_pm_events_by_tag_slug(tag_slug, args.pm_page_limit, args.max_pm_pages)
            except Exception as exc:  # noqa: BLE001 - staging collector records per-category failures.
                old_market_db.record_warning(
                    conn,
                    "collect_pm_metadata",
                    f"PM tag_slug fetch failed: {category.key} {tag_slug}: {exc}",
                    category_key=category.key,
                    severity="error",
                    context={"tag_slug": tag_slug},
                )
                if args.show_warnings:
                    print(f"PM warning: {category.key} tag_slug={tag_slug}: {exc}", file=sys.stderr)
                continue
            for event in events:
                old_market_db.upsert_pm_event(conn, event, tag_slug=tag_slug)
            count += len(events)
            conn.commit()
        for tag_id in category.tag_ids:
            try:
                events = fetch_pm_events_by_tag_id(tag_id, args.pm_page_limit, args.max_pm_pages)
            except Exception as exc:  # noqa: BLE001
                old_market_db.record_warning(
                    conn,
                    "collect_pm_metadata",
                    f"PM tag_id fetch failed: {category.key} {tag_id}: {exc}",
                    category_key=category.key,
                    severity="error",
                    context={"tag_id": tag_id},
                )
                if args.show_warnings:
                    print(f"PM warning: {category.key} tag_id={tag_id}: {exc}", file=sys.stderr)
                continue
            for event in events:
                old_market_db.upsert_pm_event(conn, event, tag_slug=f"tag_id:{tag_id}", tag_id=str(tag_id))
            count += len(events)
            conn.commit()
    return count


def candidate_ks_series(adapters: list[SportAdapter]) -> list[str]:
    seen: set[str] = set()
    series: list[str] = []
    for adapter in adapters:
        for ticker in adapter.category.kalshi_candidate_series:
            if ticker and ticker not in seen:
                seen.add(ticker)
                series.append(ticker)
        ticker = adapter.category.kalshi_series_ticker
        if ticker and ticker not in seen:
            seen.add(ticker)
            series.append(ticker)
    return series


def fetch_ks_markets(series_ticker: str, page_limit: int, max_pages: int) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = ""
    page = 0
    while True:
        if max_pages and page >= max_pages:
            break
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": "open",
            "limit": min(max(page_limit, 1), 200),
        }
        if cursor:
            params["cursor"] = cursor
        payload = core.get_json(core.KALSHI_API, "/markets", params)
        page_markets = payload.get("markets", []) if isinstance(payload, dict) else []
        if not page_markets:
            break
        page += 1
        markets.extend(page_markets)
        cursor = str(payload.get("cursor") or "")
        if not cursor:
            break
    return markets


def collect_ks_metadata(conn, adapters: list[SportAdapter], args: argparse.Namespace) -> int:
    count = 0
    for series_ticker in candidate_ks_series(adapters):
        try:
            markets = fetch_ks_markets(series_ticker, args.ks_page_limit, args.max_ks_pages)
        except Exception as exc:  # noqa: BLE001
            old_market_db.record_warning(
                conn,
                "collect_ks_metadata",
                f"KS series fetch failed: {series_ticker}: {exc}",
                severity="error",
                context={"series_ticker": series_ticker},
            )
            if args.show_warnings:
                print(f"KS warning: series={series_ticker}: {exc}", file=sys.stderr)
            continue
        eligible_markets = market_filters.eligible_ks_market_groups(markets)
        for market in eligible_markets:
            old_market_db.upsert_ks_market(conn, series_ticker, market)
        count += len(eligible_markets)
        conn.commit()
    return count


def collect_orderbooks(conn, args: argparse.Namespace) -> tuple[int, int]:
    pm_limit = args.max_pm_orderbooks if args.max_pm_orderbooks > 0 else args.max_orderbooks
    pm_tokens = old_market_db.active_pm_token_ids(conn, pm_limit)
    ks_limit = args.max_ks_orderbooks if args.max_ks_orderbooks > 0 else args.max_orderbooks
    ks_tickers = old_market_db.active_ks_market_tickers(conn, ks_limit)
    commit_every = getattr(args, "orderbook_commit_every", 100)
    pm_count = 0
    ks_count = 0

    for token_id in pm_tokens:
        old_market_db.fetch_and_record_orderbook(
            conn,
            venue="pm",
            instrument_id=token_id,
            request_path="/book",
            depth=None,
            fetcher=lambda token_id=token_id: core.get_json(core.PM_CLOB, "/book", {"token_id": token_id}),
        )
        pm_count += 1
        if commit_every > 0 and pm_count % commit_every == 0:
            conn.commit()
        if args.pm_sleep:
            time.sleep(args.pm_sleep)
    conn.commit()

    for market_ticker in ks_tickers:
        old_market_db.fetch_and_record_orderbook(
            conn,
            venue="ks",
            instrument_id=market_ticker,
            request_path=f"/markets/{market_ticker}/orderbook",
            depth=args.ks_depth,
            fetcher=lambda market_ticker=market_ticker: core.get_json(
                core.KALSHI_API,
                f"/markets/{market_ticker}/orderbook",
                {"depth": args.ks_depth},
            ),
        )
        ks_count += 1
        if commit_every > 0 and ks_count % commit_every == 0:
            conn.commit()
        if args.ks_sleep:
            time.sleep(args.ks_sleep)
    conn.commit()
    return pm_count, ks_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage PM/KS sports metadata and full orderbooks into SQLite.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--sports", default="all")
    parser.add_argument("--pm-page-limit", type=int, default=200)
    parser.add_argument("--ks-page-limit", type=int, default=200)
    parser.add_argument("--max-pm-pages", type=int, default=0, help="0 means no page cap.")
    parser.add_argument("--max-ks-pages", type=int, default=0, help="0 means no page cap.")
    parser.add_argument(
        "--no-pm-repair-fallback",
        action="store_true",
        help="Disable targeted PM slug fallback for known KS-only tennis/CS2 repair cases.",
    )
    parser.add_argument(
        "--pm-extra-slug",
        action="append",
        default=[],
        help="Additional exact PM event slug to fetch through the repair fallback. Can be repeated.",
    )
    parser.add_argument(
        "--pm-fallback-max-slugs",
        type=int,
        default=500,
        help="Maximum generated PM slugs to try in the repair fallback; explicit slugs are always kept. 0 means no cap.",
    )
    parser.add_argument(
        "--pm-fallback-search",
        action="store_true",
        help="Also try PM Gamma search queries for generated KS tennis/CS2 candidates.",
    )
    parser.add_argument(
        "--pm-fallback-max-searches",
        type=int,
        default=50,
        help="Maximum PM search fallback queries when --pm-fallback-search is enabled.",
    )
    parser.add_argument("--max-orderbooks", type=int, default=0, help="Per-venue cap; 0 means all active instruments.")
    parser.add_argument("--max-pm-orderbooks", type=int, default=0, help="Overrides --max-orderbooks for PM.")
    parser.add_argument("--max-ks-orderbooks", type=int, default=0, help="Overrides --max-orderbooks for KS.")
    parser.add_argument("--ks-depth", type=int, default=0, help="0 asks Kalshi for full depth.")
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--orderbooks-only", action="store_true")
    parser.add_argument("--pm-sleep", type=float, default=0.0)
    parser.add_argument("--ks-sleep", type=float, default=0.0)
    parser.add_argument("--orderbook-commit-every", type=int, default=100)
    parser.add_argument("--busy-timeout-ms", type=int, default=60000)
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    if args.metadata_only and args.orderbooks_only:
        parser.error("--metadata-only and --orderbooks-only cannot be combined")
    if args.pm_page_limit <= 0 or args.ks_page_limit <= 0:
        parser.error("page limits must be positive")
    if (
        args.max_orderbooks < 0
        or args.max_pm_orderbooks < 0
        or args.max_ks_orderbooks < 0
        or args.max_pm_pages < 0
        or args.max_ks_pages < 0
        or args.pm_fallback_max_slugs < 0
        or args.pm_fallback_max_searches < 0
        or args.orderbook_commit_every < 0
        or args.busy_timeout_ms <= 0
    ):
        parser.error("max values must be >= 0 and --busy-timeout-ms must be positive")

    adapters = parse_adapters(args.sports)
    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        conn.execute(f"PRAGMA busy_timeout = {args.busy_timeout_ms}")
        pm_events = 0
        ks_markets = 0
        pm_books = 0
        ks_books = 0
        if not args.orderbooks_only:
            pm_events = collect_pm_metadata(conn, adapters, args)
            ks_markets = collect_ks_metadata(conn, adapters, args)
            if not args.no_pm_repair_fallback:
                pm_events += collect_pm_repair_metadata(conn, args)
        if not args.metadata_only:
            pm_books, ks_books = collect_orderbooks(conn, args)
        counts = old_market_db.row_counts(conn)

    print(
        "staging DB collect complete: "
        f"pm_events_seen={pm_events}; ks_markets_seen={ks_markets}; "
        f"pm_books={pm_books}; ks_books={ks_books}; db={args.db}"
    )
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
