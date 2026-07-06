#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import old_market_db
import old_pipeline_core as core


DEFAULT_PM_SLUGS = (
    "mlb-cin-mil-2026-07-01",
    "mlb-det-nyy-2026-07-01",
    "cs2-9z-eyeballers-2026-07-01",
    "cs2-9z-eye-2026-07-01",
    "cs2-eyeballers-9z-2026-07-01",
    "cs2-9z-eyebal-2026-07-01",
    "cs2-eyebal-9z-2026-07-01",
    "cs2-9z-eyeball-2026-07-01",
    "cs2-eyeball-9z-2026-07-01",
)

DEFAULT_PM_SEARCHES = (
    "9z EYEBALLERS",
    "EYEBALLERS 9z",
)

DEFAULT_KS_EVENT_TICKERS = (
    "KXWNBAGAME-26JUN30LVNY",
    "KXVALORANTGAME-26JUL010700XCAT1A",
)


def source_tag_for_pm_slug(slug: str) -> str:
    prefix = slug.split("-", 1)[0].lower()
    if prefix in {"mlb", "wnba", "cs2", "lol", "valorant"}:
        return prefix
    return prefix or "targeted"


def fetch_pm_by_slug(slug: str, timeout: float) -> list[dict[str, Any]]:
    payload = core.get_json(
        core.PM_GAMMA,
        "/events",
        {
            "slug": slug,
            "active": "true",
            "closed": "false",
            "limit": 10,
        },
        timeout=timeout,
    )
    events = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    return [event for event in events if isinstance(event, dict) and str(event.get("slug") or "") == slug]


def fetch_pm_by_search(query: str, timeout: float) -> list[dict[str, Any]]:
    seen = set()
    events: list[dict[str, Any]] = []
    for key in ("search", "q"):
        payload = core.get_json(
            core.PM_GAMMA,
            "/events",
            {
                key: query,
                "active": "true",
                "closed": "false",
                "limit": 50,
                "order": "startDate",
                "ascending": "true",
            },
            timeout=timeout,
        )
        page_events = payload if isinstance(payload, list) else payload.get("events", []) if isinstance(payload, dict) else []
        for event in page_events:
            if not isinstance(event, dict):
                continue
            slug = str(event.get("slug") or "")
            title = str(event.get("title") or "")
            text = f"{slug} {title}".lower()
            if not slug or slug in seen:
                continue
            if "9z" not in text or "eyeball" not in text:
                continue
            seen.add(slug)
            events.append(event)
        if events:
            break
    return events


def fetch_ks_event_markets(event_ticker: str, timeout: float) -> list[dict[str, Any]]:
    payload = core.get_json(
        core.KALSHI_API,
        "/markets",
        {
            "event_ticker": event_ticker,
            "status": "open",
            "limit": 200,
        },
        timeout=timeout,
    )
    markets = payload.get("markets", []) if isinstance(payload, dict) else []
    return [market for market in markets if isinstance(market, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Targeted PM/KS metadata repair for known missing pairing candidates.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--pm-slug", action="append", default=[])
    parser.add_argument("--pm-search", action="append", default=[])
    parser.add_argument("--ks-event-ticker", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--busy-timeout-ms", type=int, default=120000)
    args = parser.parse_args()

    pm_slugs = [*DEFAULT_PM_SLUGS, *args.pm_slug]
    pm_searches = [*DEFAULT_PM_SEARCHES, *args.pm_search]
    ks_event_tickers = [*DEFAULT_KS_EVENT_TICKERS, *args.ks_event_ticker]

    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        conn.execute(f"PRAGMA busy_timeout = {args.busy_timeout_ms}")
        pm_seen = 0
        pm_inserted = 0
        for slug in dict.fromkeys(pm_slugs):
            events = fetch_pm_by_slug(slug, args.timeout)
            pm_seen += len(events)
            for event in events:
                old_market_db.upsert_pm_event(conn, event, tag_slug=source_tag_for_pm_slug(slug))
                pm_inserted += 1
            conn.commit()

        pm_search_seen = 0
        for query in dict.fromkeys(pm_searches):
            events = fetch_pm_by_search(query, args.timeout)
            pm_search_seen += len(events)
            for event in events:
                slug = str(event.get("slug") or "")
                old_market_db.upsert_pm_event(conn, event, tag_slug=source_tag_for_pm_slug(slug))
                pm_inserted += 1
            conn.commit()

        ks_markets_seen = 0
        for event_ticker in dict.fromkeys(ks_event_tickers):
            markets = fetch_ks_event_markets(event_ticker, args.timeout)
            ks_markets_seen += len(markets)
            for market in markets:
                series_ticker = str(market.get("series_ticker") or event_ticker.split("-", 1)[0])
                old_market_db.upsert_ks_market(conn, series_ticker, market)
            conn.commit()

        counts = old_market_db.row_counts(conn)

    print(
        "targeted metadata refresh complete: "
        f"pm_slug_events_seen={pm_seen}; pm_search_events_seen={pm_search_seen}; "
        f"pm_events_upserted={pm_inserted}; ks_markets_seen={ks_markets_seen}; db={args.db}"
    )
    print(", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
