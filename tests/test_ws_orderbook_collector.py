from __future__ import annotations

import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import new_ws_orderbook_core as core  # noqa: E402
import new_bootstrap_ws_database  # noqa: E402
import old_market_db  # noqa: E402
import new_run_ws_orderbook_collector  # noqa: E402
import old_staging_run_market_db_loop  # noqa: E402


class WebSocketOrderbookCollectorTests(unittest.TestCase):
    def test_normalize_sports_arg_maps_top_three_aliases(self) -> None:
        self.assertEqual(core.normalize_sports_arg("tennis,valorant,baseball"), ("tennis", "esports", "baseball"))
        self.assertEqual(core.normalize_sports_arg("cs2,lol,mlb,kbo"), ("esports", "baseball"))
        self.assertNotIn("valorant", core.SPORT_PROFILES)
        self.assertNotIn("mlb", core.SPORT_PROFILES)
        self.assertIsNone(core.normalize_sports_arg("all"))

    def test_selected_sports_map_to_pm_tags_and_ks_series(self) -> None:
        selected = core.normalize_sports_arg("tennis,esports,baseball")

        self.assertEqual(
            core.selected_pm_tag_slugs(selected),
            ("tennis", "esports", "valorant", "cs2", "lol", "baseball", "mlb", "kbo"),
        )
        self.assertIn("KXATPMATCH", core.selected_ks_series_tickers(selected))
        self.assertIn("KXVALORANTGAME", core.selected_ks_series_tickers(selected))
        self.assertIn("KXCS2GAME", core.selected_ks_series_tickers(selected))
        self.assertIn("KXLOLGAME", core.selected_ks_series_tickers(selected))
        self.assertIn("KXMLBGAME", core.selected_ks_series_tickers(selected))
        self.assertIn("KXBASEBALLGAME", core.selected_ks_series_tickers(selected))

    def test_pipeline_default_args_and_db_guards(self) -> None:
        ws_args = new_run_ws_orderbook_collector.build_arg_parser().parse_args([])
        self.assertEqual(ws_args.db, "data/new/new_arb_research.sqlite")
        self.assertEqual(ws_args.sports, "tennis,esports,baseball")
        self.assertEqual(ws_args.snapshot_interval, 5.0)
        self.assertEqual(ws_args.depth, 4)

        bootstrap_args = new_bootstrap_ws_database.build_arg_parser().parse_args([])
        self.assertEqual(bootstrap_args.db, "data/new/new_arb_research.sqlite")
        self.assertEqual(bootstrap_args.sports, "tennis,esports,baseball")

        legacy_args = old_staging_run_market_db_loop.build_arg_parser().parse_args([])
        self.assertEqual(legacy_args.db, "data/old/old_arb_research.sqlite")
        self.assertEqual(legacy_args.interval, 10.0)

        with self.assertRaises(ValueError):
            old_market_db.connect("data/new/new_arb_research.sqlite")
        with self.assertRaises(ValueError):
            core.connect_db(Path("data/old/old_arb_research.sqlite"))

    def test_active_ids_filter_to_selected_sports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = core.connect_db(Path(tmp) / "ws.sqlite")
            now = core.utc_now()
            events = [
                ("tennis-event", "tennis", 1, 0),
                ("valorant-event", "valorant", 1, 0),
                ("cs2-event", "cs2", 1, 0),
                ("lol-event", "lol", 1, 0),
                ("mlb-event", "mlb", 1, 0),
                ("kbo-event", "kbo", 1, 0),
                ("nfl-event", "nfl", 1, 0),
            ]
            for slug, tag_slug, active, closed in events:
                outcomes = [f"{slug} Alpha", f"{slug} Beta"]
                token_ids = [f"token-{slug}", f"token-{slug}-b"]
                market_payload = {
                    "id": f"market-{slug}",
                    "question": f"{outcomes[0]} vs. {outcomes[1]}",
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                    "outcomes": core.json_text(outcomes),
                    "clobTokenIds": core.json_text(token_ids),
                }
                conn.execute(
                    """
                    INSERT INTO pm_events (
                        event_slug, event_id, title, start_date, end_date, active, closed,
                        tag_slug, tag_id, raw_json, first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, ?, '', '', ?, ?, ?, '', '{}', ?, ?)
                    """,
                    (slug, slug, f"{outcomes[0]} vs. {outcomes[1]}", active, closed, tag_slug, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO pm_event_sources (
                        event_slug, source_type, source_value, source_label, first_seen_ts, last_seen_ts
                    ) VALUES (?, 'tag_slug', ?, ?, ?, ?)
                    """,
                    (slug, tag_slug, tag_slug, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO pm_markets (
                        market_id, event_slug, question, market_slug, condition_id,
                        active, closed, enable_order_book, outcomes_json, clob_token_ids_json,
                        raw_json, first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, ?, '', '', 1, 0, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"market-{slug}",
                        slug,
                        market_payload["question"],
                        core.json_text(outcomes),
                        core.json_text(token_ids),
                        core.json_text(market_payload),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO pm_tokens (
                        token_id, market_id, event_slug, outcome_index, outcome_name,
                        active, closed, raw_json, first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, ?, 0, ?, 1, 0, '{}', ?, ?)
                    """,
                    (token_ids[0], f"market-{slug}", slug, outcomes[0], now, now),
                )
            for series in (
                "KXATPMATCH",
                "KXVALORANTGAME",
                "KXCS2GAME",
                "KXLOLGAME",
                "KXMLBGAME",
                "KXBASEBALLGAME",
                "KXNFLGAME",
            ):
                conn.execute(
                    """
                    INSERT INTO ks_events (
                        event_ticker, series_ticker, title, status, raw_json, first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, ?, 'open', '{}', ?, ?)
                    """,
                    (f"{series}-EVENT", series, f"{series} Alpha vs Beta winner", now, now),
                )
                for suffix, outcome in (("A", "Alpha"), ("B", "Beta")):
                    market_payload = {
                        "ticker": f"{series}-EVENT-{suffix}",
                        "event_ticker": f"{series}-EVENT",
                        "title": f"{series} Alpha vs Beta winner",
                        "yes_sub_title": outcome,
                        "status": "open",
                    }
                    conn.execute(
                        """
                        INSERT INTO ks_markets (
                            market_ticker, event_ticker, series_ticker, title, yes_sub_title,
                            status, close_time, yes_bid_scaled, yes_ask_scaled, raw_json,
                            first_seen_ts, last_seen_ts
                        ) VALUES (?, ?, ?, ?, ?, 'open', '', NULL, NULL, ?, ?, ?)
                        """,
                        (
                            market_payload["ticker"],
                            market_payload["event_ticker"],
                            series,
                            market_payload["title"],
                            outcome,
                            core.json_text(market_payload),
                            now,
                            now,
                        ),
                    )
            bad_pm_market = {
                "id": "market-bad-prop",
                "question": "Will baseball Alpha score over 4.5 runs?",
                "active": True,
                "closed": False,
                "enableOrderBook": True,
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["token-bad-yes","token-bad-no"]',
            }
            conn.execute(
                """
                INSERT INTO pm_events (
                    event_slug, event_id, title, start_date, end_date, active, closed,
                    tag_slug, tag_id, raw_json, first_seen_ts, last_seen_ts
                ) VALUES ('bad-prop-event', 'bad-prop-event', 'Baseball prop', '', '', 1, 0,
                    'baseball', '', '{}', ?, ?)
                """,
                (now, now),
            )
            conn.execute(
                """
                INSERT INTO pm_markets (
                    market_id, event_slug, question, market_slug, condition_id,
                    active, closed, enable_order_book, outcomes_json, clob_token_ids_json,
                    raw_json, first_seen_ts, last_seen_ts
                ) VALUES ('market-bad-prop', 'bad-prop-event', ?, '', '', 1, 0, 1,
                    ?, ?, ?, ?, ?)
                """,
                (
                    bad_pm_market["question"],
                    bad_pm_market["outcomes"],
                    bad_pm_market["clobTokenIds"],
                    core.json_text(bad_pm_market),
                    now,
                    now,
                ),
            )
            for index, token_id in enumerate(("token-bad-yes", "token-bad-no")):
                conn.execute(
                    """
                    INSERT INTO pm_tokens (
                        token_id, market_id, event_slug, outcome_index, outcome_name,
                        active, closed, raw_json, first_seen_ts, last_seen_ts
                    ) VALUES (?, 'market-bad-prop', 'bad-prop-event', ?, ?, 1, 0, '{}', ?, ?)
                    """,
                    (token_id, index, ("Yes", "No")[index], now, now),
                )
            conn.execute(
                """
                INSERT INTO ks_events (
                    event_ticker, series_ticker, title, status, raw_json, first_seen_ts, last_seen_ts
                ) VALUES ('KXMLBGAME-BAD-DRAW', 'KXMLBGAME', 'Alpha vs Beta draw', 'open', '{}', ?, ?)
                """,
                (now, now),
            )
            for suffix, outcome in (("ALP", "Alpha"), ("BET", "Beta"), ("DRAW", "Draw")):
                market_payload = {
                    "ticker": f"KXMLBGAME-BAD-DRAW-{suffix}",
                    "event_ticker": "KXMLBGAME-BAD-DRAW",
                    "title": "Alpha vs Beta draw",
                    "yes_sub_title": outcome,
                    "status": "open",
                }
                conn.execute(
                    """
                    INSERT INTO ks_markets (
                        market_ticker, event_ticker, series_ticker, title, yes_sub_title,
                        status, close_time, yes_bid_scaled, yes_ask_scaled, raw_json,
                        first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, 'KXMLBGAME', ?, ?, 'open', '', NULL, NULL, ?, ?, ?)
                    """,
                    (
                        market_payload["ticker"],
                        market_payload["event_ticker"],
                        market_payload["title"],
                        outcome,
                        core.json_text(market_payload),
                        now,
                        now,
                    ),
                )
            conn.commit()

            selected = core.normalize_sports_arg("tennis,esports,baseball")

            self.assertEqual(
                core.active_pm_token_ids(conn, selected),
                [
                    "token-cs2-event",
                    "token-kbo-event",
                    "token-lol-event",
                    "token-mlb-event",
                    "token-tennis-event",
                    "token-valorant-event",
                ],
            )
            self.assertEqual(
                core.active_ks_market_tickers(conn, selected),
                [
                    "KXATPMATCH-EVENT-A",
                    "KXATPMATCH-EVENT-B",
                    "KXBASEBALLGAME-EVENT-A",
                    "KXBASEBALLGAME-EVENT-B",
                    "KXCS2GAME-EVENT-A",
                    "KXCS2GAME-EVENT-B",
                    "KXLOLGAME-EVENT-A",
                    "KXLOLGAME-EVENT-B",
                    "KXMLBGAME-EVENT-A",
                    "KXMLBGAME-EVENT-B",
                    "KXVALORANTGAME-EVENT-A",
                    "KXVALORANTGAME-EVENT-B",
                ],
            )

    def test_ws_discovery_writes_only_binary_winner_markets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = core.connect_db(Path(tmp) / "ws.sqlite")
            pm_count = core.upsert_pm_event(
                conn,
                {
                    "id": "pm-event",
                    "slug": "mlb-alpha-beta-2026-06-24",
                    "title": "Alpha Bears vs. Beta Cats",
                    "active": True,
                    "closed": False,
                    "markets": [
                        {
                            "id": "pm-winner",
                            "question": "Alpha Bears vs. Beta Cats winner",
                            "active": True,
                            "closed": False,
                            "enableOrderBook": True,
                            "outcomes": '["Alpha Bears","Beta Cats"]',
                            "clobTokenIds": '["pm-alpha","pm-beta"]',
                        },
                        {
                            "id": "pm-total",
                            "question": "Total runs over under",
                            "active": True,
                            "closed": False,
                            "enableOrderBook": True,
                            "outcomes": '["Over","Under"]',
                            "clobTokenIds": '["pm-over","pm-under"]',
                        },
                    ],
                },
                "baseball",
            )
            self.assertEqual(pm_count, 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pm_markets").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pm_tokens").fetchone()[0], 2)

            original_series = core.discover_ks_sports_series_tickers
            original_http = core.http_get_json

            def fake_series(_selected_sports=None):
                return ["KXMLBGAME"]

            def fake_http(_base_url, path, _params, timeout=20):
                self.assertEqual(path, "/markets")
                return {
                    "markets": [
                        {
                            "ticker": "KXMLBGAME-ALPBET-ALP",
                            "event_ticker": "KXMLBGAME-ALPBET",
                            "title": "Alpha Bears vs Beta Cats winner",
                            "yes_sub_title": "Alpha Bears",
                            "status": "open",
                        },
                        {
                            "ticker": "KXMLBGAME-ALPBET-BET",
                            "event_ticker": "KXMLBGAME-ALPBET",
                            "title": "Alpha Bears vs Beta Cats winner",
                            "yes_sub_title": "Beta Cats",
                            "status": "open",
                        },
                        {
                            "ticker": "KXMLBGAME-DRAW-ALP",
                            "event_ticker": "KXMLBGAME-DRAW",
                            "title": "Alpha Bears vs Beta Cats draw",
                            "yes_sub_title": "Alpha Bears",
                            "status": "open",
                        },
                        {
                            "ticker": "KXMLBGAME-DRAW-BET",
                            "event_ticker": "KXMLBGAME-DRAW",
                            "title": "Alpha Bears vs Beta Cats draw",
                            "yes_sub_title": "Beta Cats",
                            "status": "open",
                        },
                        {
                            "ticker": "KXMLBGAME-DRAW-DRAW",
                            "event_ticker": "KXMLBGAME-DRAW",
                            "title": "Alpha Bears vs Beta Cats draw",
                            "yes_sub_title": "Draw",
                            "status": "open",
                        },
                        {
                            "ticker": "KXMLBGAME-CHAMP-ALP",
                            "event_ticker": "KXMLBGAME-CHAMP",
                            "title": "League championship winner",
                            "yes_sub_title": "Alpha Bears",
                            "status": "open",
                        },
                        {
                            "ticker": "KXMLBGAME-CHAMP-BET",
                            "event_ticker": "KXMLBGAME-CHAMP",
                            "title": "League championship winner",
                            "yes_sub_title": "Beta Cats",
                            "status": "open",
                        },
                    ],
                    "cursor": "",
                }

            core.discover_ks_sports_series_tickers = fake_series
            core.http_get_json = fake_http
            try:
                ks_count = core.discover_ks_sports(conn, selected_sports=("baseball",), max_pages=1)
            finally:
                core.discover_ks_sports_series_tickers = original_series
                core.http_get_json = original_http

            self.assertEqual(ks_count, 2)
            self.assertEqual(
                [row["market_ticker"] for row in conn.execute("SELECT market_ticker FROM ks_markets ORDER BY market_ticker")],
                ["KXMLBGAME-ALPBET-ALP", "KXMLBGAME-ALPBET-BET"],
            )

    def test_extract_ks_sports_series_tickers_filters_non_sports(self) -> None:
        payload = {
            "series": [
                {"ticker": "KXMLBGAME", "category": "Sports", "title": "MLB game winner", "tags": []},
                {"ticker": "KXSOCCER", "category": "Other", "title": "Soccer match winner", "tags": []},
                {"ticker": "KXWEATHER", "category": "Weather", "title": "NYC temperature", "tags": []},
                {"ticker": "KXCS2GAME", "category": "Other", "title": "Match", "tags": ["esports"]},
            ]
        }

        self.assertEqual(
            core.extract_ks_sports_series_tickers(payload),
            ["KXMLBGAME", "KXSOCCER", "KXCS2GAME"],
        )

    def test_pm_book_and_price_change_keep_top_four_levels(self) -> None:
        books: dict[str, core.PriceBook] = {}
        core.handle_pm_message(
            books,
            {
                "event_type": "book",
                "asset_id": "pm-token-1",
                "timestamp": "1760000000000",
                "bids": [
                    {"price": "0.41", "size": "10"},
                    {"price": "0.42", "size": "20"},
                    {"price": "0.43", "size": "30"},
                    {"price": "0.44", "size": "40"},
                    {"price": "0.45", "size": "50"},
                ],
                "asks": [
                    {"price": "0.51", "size": "10"},
                    {"price": "0.52", "size": "20"},
                    {"price": "0.53", "size": "30"},
                    {"price": "0.54", "size": "40"},
                    {"price": "0.55", "size": "50"},
                ],
            },
        )
        core.handle_pm_message(
            books,
            {
                "event_type": "price_change",
                "timestamp": "1760000001000",
                "price_changes": [
                    {"asset_id": "pm-token-1", "side": "BUY", "price": "0.46", "size": "60"},
                    {"asset_id": "pm-token-1", "side": "SELL", "price": "0.51", "size": "0"},
                ],
            },
        )

        top = books["pm-token-1"].top(4)

        self.assertEqual(
            [price for price, _size in top.bids],
            [Decimal("0.46"), Decimal("0.45"), Decimal("0.44"), Decimal("0.43")],
        )
        self.assertEqual(
            [price for price, _size in top.asks],
            [Decimal("0.52"), Decimal("0.53"), Decimal("0.54"), Decimal("0.55")],
        )

    def test_ks_snapshot_and_delta_convert_no_bids_to_yes_asks(self) -> None:
        books: dict[str, core.KalshiPriceBook] = {}
        core.handle_ks_message(
            books,
            {
                "type": "orderbook_snapshot",
                "msg": {
                    "market_ticker": "KXMLBGAME-TEST",
                    "yes_dollars_fp": [["0.40", "100"], ["0.39", "50"]],
                    "no_dollars_fp": [["0.57", "25"], ["0.58", "10"]],
                },
            },
        )
        core.handle_ks_message(
            books,
            {
                "type": "orderbook_delta",
                "msg": {
                    "market_ticker": "KXMLBGAME-TEST",
                    "side": "yes",
                    "price_dollars": "0.41",
                    "delta_fp": "15",
                },
            },
        )
        core.handle_ks_message(
            books,
            {
                "type": "orderbook_delta",
                "msg": {
                    "market_ticker": "KXMLBGAME-TEST",
                    "side": "no",
                    "price_dollars": "0.58",
                    "delta_fp": "-10",
                },
            },
        )

        top = books["KXMLBGAME-TEST"].top(4)

        self.assertEqual([price for price, _size in top.bids], [Decimal("0.41"), Decimal("0.40"), Decimal("0.39")])
        self.assertEqual([price for price, _size in top.asks], [Decimal("0.43")])

    def test_flush_books_writes_only_four_bid_and_four_ask_levels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = core.connect_db(Path(tmp) / "ws.sqlite")
            book = core.PriceBook()
            book.replace(
                bids=[["0.40", "1"], ["0.41", "1"], ["0.42", "1"], ["0.43", "1"], ["0.44", "1"]],
                asks=[["0.50", "1"], ["0.51", "1"], ["0.52", "1"], ["0.53", "1"], ["0.54", "1"]],
            )

            stats = core.flush_books(
                conn,
                venue="pm",
                instrument_ids=["token"],
                books={"token": book},
                depth=4,
                max_stale_seconds=10,
                request_path="wss://test",
            )

            self.assertEqual(stats.ok, 1)
            rows = conn.execute(
                "SELECT side, level_index, price_scaled FROM orderbook_levels ORDER BY side, level_index"
            ).fetchall()
            self.assertEqual(len(rows), 8)
            self.assertEqual(max(row["level_index"] for row in rows), 3)

    def test_flush_books_uses_provided_batch_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = core.connect_db(Path(tmp) / "ws.sqlite")
            book = core.PriceBook()
            book.replace(bids=[["0.40", "1"]], asks=[["0.50", "1"]])
            batch_ts = "2026-07-02T05:00:00+00:00"

            core.flush_books(
                conn,
                venue="pm",
                instrument_ids=["token"],
                books={"token": book},
                depth=4,
                max_stale_seconds=10,
                request_path="wss://test",
                collected_ts_utc=batch_ts,
            )

            row = conn.execute("SELECT collected_ts_utc FROM orderbook_observations").fetchone()
            self.assertEqual(row["collected_ts_utc"], batch_ts)

    def test_stale_book_is_flushed_and_counted_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = core.connect_db(Path(tmp) / "ws.sqlite")
            book = core.PriceBook()
            book.replace(bids=[["0.40", "1"]], asks=[["0.50", "1"]])
            book.last_update_monotonic = time.monotonic() - 100

            stats = core.flush_books(
                conn,
                venue="pm",
                instrument_ids=["token"],
                books={"token": book},
                depth=4,
                max_stale_seconds=1,
                request_path="wss://test",
            )

            self.assertEqual(stats.ok, 1)
            self.assertEqual(stats.fresh, 0)
            self.assertEqual(stats.stale, 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orderbook_observations").fetchone()[0], 1)

    def test_missing_book_can_be_written_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = core.connect_db(Path(tmp) / "ws.sqlite")

            core.flush_books(
                conn,
                venue="pm",
                instrument_ids=["token"],
                books={},
                depth=4,
                max_stale_seconds=1,
                request_path="wss://test",
                write_stale_errors=True,
            )

            row = conn.execute("SELECT status, error_message FROM orderbook_observations").fetchone()
            self.assertEqual(row["status"], "error")
            self.assertEqual(row["error_message"], "missing_ws_book")


if __name__ == "__main__":
    unittest.main()
