#!/usr/bin/env python3
from __future__ import annotations

import argparse

import new_ws_orderbook_core as core


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the WebSocket sports orderbook SQLite database and load PM/KS sports metadata."
    )
    parser.add_argument("--db", default=str(core.DEFAULT_WS_DB_PATH))
    parser.add_argument("--sports", default=core.DEFAULT_WS_SPORTS, help="Comma-separated sports, or 'all'.")
    parser.add_argument("--pm-page-limit", type=int, default=200)
    parser.add_argument("--ks-page-limit", type=int, default=200)
    parser.add_argument("--max-pm-pages", type=int, default=0, help="0 means no page cap.")
    parser.add_argument("--max-ks-pages", type=int, default=0, help="0 means no page cap.")
    parser.add_argument("--skip-discovery", action="store_true", help="Only create/verify schema; do not call REST metadata.")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        selected_sports = core.normalize_sports_arg(args.sports)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        db_path = core.ensure_ws_db_path(args.db)
    except ValueError as exc:
        parser.error(str(exc))
    if args.pm_page_limit <= 0 or args.ks_page_limit <= 0:
        parser.error("page limits must be positive")
    if args.max_pm_pages < 0 or args.max_ks_pages < 0:
        parser.error("max page values must be >= 0")

    with core.connect_db(db_path) as conn:
        pm_seen = ks_seen = 0
        if not args.skip_discovery:
            try:
                pm_seen = core.discover_pm_sports(
                    conn,
                    page_limit=args.pm_page_limit,
                    max_pages=args.max_pm_pages,
                    selected_sports=selected_sports,
                )
            except Exception as exc:  # noqa: BLE001 - startup can continue with cached metadata.
                print(f"warning: PM metadata discovery failed; using cached DB metadata: {exc}")
            try:
                ks_seen = core.discover_ks_sports(
                    conn,
                    page_limit=args.ks_page_limit,
                    max_pages=args.max_ks_pages,
                    selected_sports=selected_sports,
                )
            except Exception as exc:  # noqa: BLE001 - startup can continue with cached metadata.
                print(f"warning: KS metadata discovery failed; using cached DB metadata: {exc}")
        pm_tokens = len(core.active_pm_token_ids(conn, selected_sports))
        ks_tickers = len(core.active_ks_market_tickers(conn, selected_sports))
        counts = {
            "pm_events": conn.execute("SELECT COUNT(*) FROM pm_events").fetchone()[0],
            "pm_markets": conn.execute("SELECT COUNT(*) FROM pm_markets").fetchone()[0],
            "pm_tokens": conn.execute("SELECT COUNT(*) FROM pm_tokens").fetchone()[0],
            "ks_events": conn.execute("SELECT COUNT(*) FROM ks_events").fetchone()[0],
            "ks_markets": conn.execute("SELECT COUNT(*) FROM ks_markets").fetchone()[0],
        }

    print(
        "ws DB bootstrap complete: "
        f"db={db_path}; sports={args.sports}; pm_metadata_upserts={pm_seen}; ks_metadata_upserts={ks_seen}; "
        f"active_pm_tokens={pm_tokens}; active_ks_tickers={ks_tickers}; "
        + ", ".join(f"{key}={value}" for key, value in counts.items())
    )


if __name__ == "__main__":
    main()
