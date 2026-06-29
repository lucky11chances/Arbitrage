from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import market_db  # noqa: E402
import pipeline_core as core  # noqa: E402
import sports_taxonomy  # noqa: E402
import staging_collect_market_db  # noqa: E402
import staging_pair_from_db  # noqa: E402
import staging_normalize_market_db  # noqa: E402
import validate_market_db  # noqa: E402


class MarketDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "test.sqlite"
        self.conn = market_db.connect(self.db_path)
        market_db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def add_pm_binary_event(
        self,
        *,
        slug: str,
        title: str,
        tag_slug: str,
        outcomes: list[str],
        token_ids: list[str],
        market_id: str | None = None,
        question: str | None = None,
        tags: list[dict[str, str]] | None = None,
        event_description: str = "",
        market_description: str = "",
        event_extra: dict | None = None,
        market_extra: dict | None = None,
    ) -> None:
        market_payload = {
            "id": market_id or f"market-{slug}",
            "question": question or title,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "description": market_description,
            "outcomes": market_db.json_text(outcomes),
            "clobTokenIds": market_db.json_text(token_ids),
        }
        if market_extra:
            market_payload.update(market_extra)
        event_payload = {
            "id": slug,
            "slug": slug,
            "title": title,
            "description": event_description,
            "startDate": "2026-06-24T00:00:00Z",
            "active": True,
            "closed": False,
            "tags": tags or [{"slug": tag_slug, "label": tag_slug}],
            "markets": [market_payload],
        }
        if event_extra:
            event_payload.update(event_extra)
        market_db.upsert_pm_event(
            self.conn,
            event_payload,
            tag_slug=tag_slug,
        )

    def add_ks_binary_event(
        self,
        *,
        series_ticker: str,
        event_ticker: str,
        title: str,
        outcomes: list[str],
        suffixes: list[str],
    ) -> None:
        for outcome, suffix in zip(outcomes, suffixes):
            market_db.upsert_ks_market(
                self.conn,
                series_ticker,
                {
                    "ticker": f"{event_ticker}-{suffix}",
                    "event_ticker": event_ticker,
                    "title": title,
                    "yes_sub_title": outcome,
                    "status": "active",
                    "close_time": "2026-06-24T23:00:00Z",
                },
            )

    def test_pm_orderbook_best_bid_ask(self) -> None:
        parsed = market_db.parse_pm_orderbook(
            {
                "timestamp": "1780000000",
                "bids": [{"price": "0.45", "size": "100"}, {"price": "0.47", "size": "20"}],
                "asks": [{"price": "0.50", "size": "10"}, {"price": "0.49", "size": "5"}],
            }
        )

        self.assertEqual(parsed.best_bid, market_db.Decimal("0.47"))
        self.assertEqual(parsed.best_bid_size, market_db.Decimal("20"))
        self.assertEqual(parsed.best_ask, market_db.Decimal("0.49"))
        self.assertEqual(parsed.best_ask_size, market_db.Decimal("5"))

    def test_ks_orderbook_yes_bid_and_implied_ask(self) -> None:
        parsed = market_db.parse_ks_orderbook(
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.4100", "10.00"], ["0.4200", "13.00"]],
                    "no_dollars": [["0.5500", "5.00"], ["0.5600", "17.00"]],
                }
            }
        )

        self.assertEqual(parsed.best_bid, market_db.Decimal("0.4200"))
        self.assertEqual(parsed.best_bid_size, market_db.Decimal("13.00"))
        self.assertEqual(parsed.best_ask, market_db.Decimal("0.4400"))
        self.assertEqual(parsed.best_ask_size, market_db.Decimal("17.00"))

    def test_metadata_upsert_and_payload_deduplication(self) -> None:
        event = {
            "id": "event-1",
            "slug": "mlb-aaa-bbb-2026-06-24",
            "title": "AAA vs. BBB",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "id": "market-1",
                    "question": "AAA vs. BBB",
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                    "outcomes": '["AAA","BBB"]',
                    "clobTokenIds": '["token-a","token-b"]',
                }
            ],
        }
        market_db.upsert_pm_event(self.conn, event, tag_slug="mlb")
        payload = {
            "timestamp": "1780000000",
            "bids": [{"price": "0.45", "size": "100"}],
            "asks": [{"price": "0.46", "size": "50"}],
        }
        ts = datetime.now(timezone.utc).isoformat()
        market_db.record_orderbook_success(self.conn, "pm", "token-a", payload, ts, "/book", None, 10)
        market_db.record_orderbook_success(self.conn, "pm", "token-a", payload, ts, "/book", None, 10)
        self.conn.commit()

        counts = market_db.row_counts(self.conn)
        self.assertEqual(counts["pm_events"], 1)
        self.assertEqual(counts["pm_event_sources"], 1)
        self.assertEqual(counts["pm_markets"], 1)
        self.assertEqual(counts["pm_tokens"], 2)
        self.assertEqual(counts["orderbook_payloads"], 1)
        self.assertEqual(counts["orderbook_observations"], 2)
        self.assertEqual(counts["orderbook_levels"], 4)
        validate_market_db.validate_integrity(self.conn)
        self.assertEqual(validate_market_db.validate_raw_json(self.conn), 3)

    def test_gender_aware_taxonomy(self) -> None:
        pm_wnba = sports_taxonomy.classify_pm_event(
            "wnba-atl-gsv-2026-06-24",
            "Atlanta Dream vs. Golden State Valkyries",
            ["tag_slug:basketball", "tag_slug:wnba"],
        )
        self.assertEqual(pm_wnba.category_key, "basketball")
        self.assertEqual(pm_wnba.universe, "wnba")
        self.assertEqual(pm_wnba.competition_gender, "women")

        pm_nba = sports_taxonomy.classify_pm_event(
            "nba-bos-nyk-2026-06-24",
            "Boston Celtics vs. New York Knicks",
            ["tag_slug:nba"],
        )
        self.assertEqual(pm_nba.category_key, "basketball")
        self.assertEqual(pm_nba.universe, "nba")
        self.assertEqual(pm_nba.competition_gender, "men")

        self.assertEqual(
            sports_taxonomy.classify_ks_market("KXWNBAGAME", "KXWNBAGAME-26JUN24NYSEA-NY", "New York vs Seattle winner?").competition_gender,
            "women",
        )
        self.assertEqual(
            sports_taxonomy.classify_ks_market("KXNBAGAME", "KXNBAGAME-26JUN24NYBOS-NY", "New York vs Boston winner?").competition_gender,
            "men",
        )
        self.assertEqual(
            sports_taxonomy.classify_ks_market("KXWTAMATCH", "KXWTAMATCH-26JUN24AAABBB-AAA", "A vs B match").competition_gender,
            "women",
        )
        self.assertEqual(
            sports_taxonomy.classify_ks_market("KXITFMATCH", "KXITFMATCH-26JUN24AAABBB-AAA", "M15 Round of 32 match").competition_gender,
            "men",
        )

        conflict = sports_taxonomy.classify_ks_market(
            "KXNBAGAME",
            "KXNBAGAME-26JUN24AAABBB-AAA",
            "Will AAA win the Women's basketball game?",
        )
        self.assertEqual(conflict.competition_gender, "unknown")
        self.assertIn("conflict", conflict.gender_source)

    def test_wnba_entity_key_maps_city_to_team(self) -> None:
        self.assertEqual(
            sports_taxonomy.entity_key("basketball", "wnba", "women", "New York Liberty"),
            sports_taxonomy.entity_key("basketball", "wnba", "women", "New York"),
        )
        self.assertNotEqual(
            sports_taxonomy.entity_key("basketball", "wnba", "women", "New York"),
            sports_taxonomy.entity_key("basketball", "nba", "men", "New York"),
        )

    def test_pm_orderbook_candidates_rotate_across_tags(self) -> None:
        for slug, tag_slug, token_ids in (
            ("mlb-aaa-bbb-2026-06-24", "baseball", ["token-a", "token-b"]),
            ("soccer-ccc-ddd-2026-06-24", "soccer", ["token-c", "token-d"]),
        ):
            market_db.upsert_pm_event(
                self.conn,
                {
                    "id": slug,
                    "slug": slug,
                    "title": slug,
                    "active": True,
                    "closed": False,
                    "markets": [
                        {
                            "id": f"market-{slug}",
                            "question": slug,
                            "active": True,
                            "closed": False,
                            "enableOrderBook": True,
                            "outcomes": '["A","B"]',
                            "clobTokenIds": f'["{token_ids[0]}","{token_ids[1]}"]',
                        }
                    ],
                },
                tag_slug=tag_slug,
            )
        self.conn.commit()

        self.assertEqual(market_db.active_pm_token_ids(self.conn, limit=2), ["token-a", "token-c"])

    def test_active_ks_markets_are_orderbook_candidates(self) -> None:
        markets = [
            ("KXMLBGAME", "KXMLBGAME-26JUN241900AAABBB-AAA"),
            ("KXMLBGAME", "KXMLBGAME-26JUN241900AAABBB-BBB"),
            ("KXNFLGAME", "KXNFLGAME-26SEP101900CCCDDD-CCC"),
        ]
        for series_ticker, ticker in markets:
            market_db.upsert_ks_market(
                self.conn,
                series_ticker,
                {
                    "ticker": ticker,
                    "event_ticker": ticker.rsplit("-", 1)[0],
                    "title": ticker,
                    "yes_sub_title": ticker.rsplit("-", 1)[-1],
                    "status": "active",
                },
            )
        self.conn.commit()

        self.assertEqual(
            market_db.active_ks_market_tickers(self.conn, limit=2),
            ["KXMLBGAME-26JUN241900AAABBB-AAA", "KXNFLGAME-26SEP101900CCCDDD-CCC"],
        )
        self.assertEqual(
            market_db.ks_markets_for_series(self.conn, "KXMLBGAME", 10)[0]["ticker"],
            "KXMLBGAME-26JUN241900AAABBB-AAA",
        )

    def test_normalizer_is_gender_aware_and_inventory_only(self) -> None:
        wnba_event = {
            "id": "wnba-event",
            "slug": "wnba-nyl-sea-2026-06-25",
            "title": "New York Liberty vs. Seattle Storm",
            "active": True,
            "closed": False,
            "tags": [{"slug": "basketball", "label": "Basketball"}, {"slug": "wnba", "label": "WNBA"}],
            "markets": [
                {
                    "id": "wnba-market",
                    "question": "New York Liberty vs. Seattle Storm",
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                    "outcomes": '["New York Liberty","Seattle Storm"]',
                    "clobTokenIds": '["pm-wnba-ny","pm-wnba-sea"]',
                }
            ],
        }
        nba_event = {
            "id": "nba-event",
            "slug": "nba-nyk-bos-2026-06-25",
            "title": "New York Knicks vs. Boston Celtics",
            "active": True,
            "closed": False,
            "tags": [{"slug": "nba", "label": "NBA"}],
            "markets": [
                {
                    "id": "nba-market",
                    "question": "New York Knicks vs. Boston Celtics",
                    "active": True,
                    "closed": False,
                    "enableOrderBook": True,
                    "outcomes": '["New York Knicks","Boston Celtics"]',
                    "clobTokenIds": '["pm-nba-ny","pm-nba-bos"]',
                }
            ],
        }
        market_db.upsert_pm_event(self.conn, wnba_event, tag_slug="basketball")
        market_db.upsert_pm_event(self.conn, wnba_event, tag_slug="wnba")
        market_db.upsert_pm_event(self.conn, nba_event, tag_slug="nba")
        market_db.upsert_ks_market(
            self.conn,
            "KXWNBAGAME",
            {
                "ticker": "KXWNBAGAME-26JUN25NYSEA-NY",
                "event_ticker": "KXWNBAGAME-26JUN25NYSEA",
                "title": "New York vs Seattle winner?",
                "yes_sub_title": "New York",
                "status": "active",
            },
        )
        self.conn.commit()

        first = staging_normalize_market_db.normalize_db(self.conn)
        second = staging_normalize_market_db.normalize_db(self.conn)
        self.assertEqual(first, second)

        rows = self.conn.execute(
            """
            SELECT venue, universe, competition_gender, outcome_name, entity_key, safe_pair_candidate
            FROM normalized_contracts
            ORDER BY venue, instrument_id
            """
        ).fetchall()
        self.assertEqual(len(rows), 5)
        genders = {(row["universe"], row["competition_gender"]) for row in rows}
        self.assertIn(("wnba", "women"), genders)
        self.assertIn(("nba", "men"), genders)
        self.assertTrue(all(row["safe_pair_candidate"] == 0 for row in rows))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paired_contracts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM edge_snapshots").fetchone()[0], 0)
        validate_market_db.validate_normalized_contracts(self.conn)

    def test_db_only_pairer_rejects_unknown_gender(self) -> None:
        self.add_pm_binary_event(
            slug="basketball-alpha-beta-2026-06-24",
            title="Alpha City vs. Beta City",
            tag_slug="basketball",
            outcomes=["Alpha City", "Beta City"],
            token_ids=["pm-alpha", "pm-beta"],
        )
        self.add_ks_binary_event(
            series_ticker="KXNCAABGAME",
            event_ticker="KXNCAABGAME-26JUN24ALPBET",
            title="Alpha City vs Beta City winner?",
            outcomes=["Alpha City", "Beta City"],
            suffixes=["ALP", "BET"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="basketball",
            from_date="2026-06-24",
        )

        self.assertEqual(pairs, [])
        self.assertEqual(counts["safe_pairs"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paired_contracts").fetchone()[0], 0)

    def test_db_only_pairer_keeps_nba_and_wnba_city_aliases_separate(self) -> None:
        self.add_pm_binary_event(
            slug="wnba-nyl-sea-2026-06-25",
            title="New York Liberty vs. Seattle Storm",
            tag_slug="wnba",
            outcomes=["New York Liberty", "Seattle Storm"],
            token_ids=["pm-wnba-ny", "pm-wnba-sea"],
            tags=[{"slug": "basketball", "label": "Basketball"}, {"slug": "wnba", "label": "WNBA"}],
        )
        self.add_pm_binary_event(
            slug="nba-nyk-bos-2026-06-25",
            title="New York Knicks vs. Boston Celtics",
            tag_slug="nba",
            outcomes=["New York Knicks", "Boston Celtics"],
            token_ids=["pm-nba-ny", "pm-nba-bos"],
        )
        self.add_ks_binary_event(
            series_ticker="KXWNBAGAME",
            event_ticker="KXWNBAGAME-26JUN25NYSEA",
            title="New York vs Seattle winner?",
            outcomes=["New York", "Seattle"],
            suffixes=["NY", "SEA"],
        )
        self.add_ks_binary_event(
            series_ticker="KXNBAGAME",
            event_ticker="KXNBAGAME-26JUN251900NYBOS",
            title="New York vs Boston winner?",
            outcomes=["New York", "Boston"],
            suffixes=["NY", "BOS"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="basketball,nba,wnba",
            from_date="2026-06-25",
        )

        self.assertEqual(counts["safe_pairs"], 4)
        by_ks = {pair.ks_market_ticker: pair.pm_token_id for pair in pairs}
        self.assertEqual(by_ks["KXWNBAGAME-26JUN25NYSEA-NY"], "pm-wnba-ny")
        self.assertEqual(by_ks["KXNBAGAME-26JUN251900NYBOS-NY"], "pm-nba-ny")
        self.assertTrue(all(pair.pm_yes_outcome != "New York Liberty" or pair.universe == "wnba" for pair in pairs))
        self.assertTrue(all(pair.pm_yes_outcome != "New York Knicks" or pair.universe == "nba" for pair in pairs))

    def test_db_only_pairer_rejects_cross_tour_tennis(self) -> None:
        self.add_pm_binary_event(
            slug="atp-alpha-beta-2026-06-24",
            title="Mallorca Championships: Alice Alpha vs Bob Beta",
            tag_slug="tennis",
            outcomes=["Alice Alpha", "Bob Beta"],
            token_ids=["pm-atp-alice", "pm-atp-bob"],
        )
        self.add_ks_binary_event(
            series_ticker="KXWTAMATCH",
            event_ticker="KXWTAMATCH-26JUN24ALPBET",
            title="Will Alice Alpha win the Alpha vs Beta match?",
            outcomes=["Alice Alpha", "Bob Beta"],
            suffixes=["ALP", "BET"],
        )
        self.add_ks_binary_event(
            series_ticker="KXATPMATCH",
            event_ticker="KXATPMATCH-26JUN24ALPBET",
            title="Will Alice Alpha win the Alpha vs Beta match?",
            outcomes=["Alice Alpha", "Bob Beta"],
            suffixes=["ALP", "BET"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-24",
        )

        self.assertEqual(counts["safe_pairs"], 2)
        self.assertEqual({pair.universe for pair in pairs}, {"atp"})
        self.assertTrue(all(pair.ks_event_ticker.startswith("KXATPMATCH-") for pair in pairs))

    def test_db_only_pairer_uses_pm_tennis_raw_hints(self) -> None:
        self.add_pm_binary_event(
            slug="itf-alpha-beta-2026-06-24",
            title="Bergamo: Alice Alpha vs Bob Beta",
            tag_slug="tennis",
            outcomes=["Alice Alpha", "Bob Beta"],
            token_ids=["pm-itf-alice", "pm-itf-bob"],
            event_description="ITF Women Bergamo match.",
        )
        self.add_pm_binary_event(
            slug="atp-gamma-delta-2026-06-24",
            title="Piracicaba: Gary Gamma vs Dan Delta",
            tag_slug="tennis",
            outcomes=["Gary Gamma", "Dan Delta"],
            token_ids=["pm-challenger-gary", "pm-challenger-dan"],
            market_description="Official Challenger results/statistics decide this market.",
        )
        self.add_ks_binary_event(
            series_ticker="KXITFWMATCH",
            event_ticker="KXITFWMATCH-26JUN24ALPBET",
            title="Alice Alpha vs Bob Beta match",
            outcomes=["Alice Alpha", "Bob Beta"],
            suffixes=["ALP", "BET"],
        )
        self.add_ks_binary_event(
            series_ticker="KXATPCHALLENGERMATCH",
            event_ticker="KXATPCHALLENGERMATCH-26JUN24GAMDEL",
            title="Gary Gamma vs Dan Delta match",
            outcomes=["Gary Gamma (b. 2001)", "Dan Delta"],
            suffixes=["GAM", "DEL"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-24",
        )

        self.assertEqual(counts["safe_pairs"], 4)
        self.assertEqual({pair.universe for pair in pairs}, {"itf_women", "atp_challenger"})

    def test_wimbledon_atp_context_description_does_not_force_challenger(self) -> None:
        self.add_pm_binary_event(
            slug="atp-michels-fearnle-2026-06-29",
            title="Wimbledon ATP: Alex Michelsen vs Jacob Fearnley",
            tag_slug="tennis",
            outcomes=["Alex Michelsen", "Jacob Fearnley"],
            token_ids=["pm-michelsen", "pm-fearnley"],
            event_extra={
                "eventMetadata": {
                    "league": "Wimbledon ATP",
                    "context_description": "Michelsen has Challenger title momentum, but this is not an ATP Challenger event.",
                },
                "seriesSlug": "atp",
            },
        )
        self.add_ks_binary_event(
            series_ticker="KXATPMATCH",
            event_ticker="KXATPMATCH-26JUN29MICFEA",
            title="Will Alex Michelsen win the Michelsen vs Fearnley: Round Of 128 match?",
            outcomes=["Alex Michelsen", "Jacob Fearnley"],
            suffixes=["MIC", "FEA"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-29",
        )

        self.assertEqual(counts["safe_pairs"], 2)
        self.assertEqual({pair.universe for pair in pairs}, {"atp"})
        self.assertTrue(all(pair.ks_event_ticker.startswith("KXATPMATCH-") for pair in pairs))

    def test_pm_tennis_slug_date_wins_over_game_start_time(self) -> None:
        self.add_pm_binary_event(
            slug="atp-grieksp-duckwor-2026-06-29",
            title="Wimbledon ATP: Tallon Griekspoor vs James Duckworth",
            tag_slug="tennis",
            outcomes=["Tallon Griekspoor", "James Duckworth"],
            token_ids=["pm-griekspoor", "pm-duckworth"],
            market_extra={"gameStartTime": "2026-06-30 15:00:00+00"},
            event_extra={"seriesSlug": "atp", "eventMetadata": {"league": "Wimbledon ATP"}},
        )
        self.add_ks_binary_event(
            series_ticker="KXATPMATCH",
            event_ticker="KXATPMATCH-26JUN29GRIDUC",
            title="Will James Duckworth win the Griekspoor vs Duckworth: Round Of 128 match?",
            outcomes=["James Duckworth", "Tallon Griekspoor"],
            suffixes=["DUC", "GRI"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-29",
        )

        self.assertEqual(counts["safe_pairs"], 2)
        self.assertEqual({pair.event_date for pair in pairs}, {"2026-06-29"})

    def test_tennis_alias_pairing_handles_initials_middle_names_transliteration_and_suffixes(self) -> None:
        cases = [
            (
                "atp-sonego-etcheve-2026-06-29",
                "Wimbledon ATP: Lorenzo Sonego vs Tomas Etcheverry",
                ["L.Sonego", "Tomas Etcheverry"],
                "KXATPMATCH-26JUN29SONETC",
                ["Lorenzo Sonego", "Tomas Martin Etcheverry"],
                ["SON", "ETC"],
            ),
            (
                "atp-auger-shevche-2026-06-29",
                "Wimbledon ATP: Felix Auger-Aliassime vs Alexander Shevchenko",
                ["Felix Auger-Aliassime", "Alexander Shevchenko"],
                "KXATPMATCH-26JUN29AUGSHE",
                ["Felix Auger-Aliassime", "Aleksandr Shevchenko"],
                ["AUG", "SHE"],
            ),
            (
                "atp-trung-damm-2026-06-29",
                "Wimbledon ATP: Marco Trungelliti vs Martin Damm",
                ["Marco Trungelliti", "Martin Damm"],
                "KXATPMATCH-26JUN29TRUDAM",
                ["Marco Trungelliti", "Martin Damm Jr"],
                ["TRU", "DAM"],
            ),
        ]
        for slug, title, pm_outcomes, ks_event, ks_outcomes, suffixes in cases:
            self.add_pm_binary_event(
                slug=slug,
                title=title,
                tag_slug="tennis",
                outcomes=pm_outcomes,
                token_ids=[f"pm-{slug}-a", f"pm-{slug}-b"],
                event_extra={"seriesSlug": "atp", "eventMetadata": {"league": "Wimbledon ATP"}},
            )
            self.add_ks_binary_event(
                series_ticker="KXATPMATCH",
                event_ticker=ks_event,
                title=f"Will {ks_outcomes[0]} win the match?",
                outcomes=ks_outcomes,
                suffixes=suffixes,
            )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-29",
        )

        self.assertEqual(counts["safe_pairs"], 6)
        by_event = Counter(pair.ks_event_ticker for pair in pairs)
        self.assertEqual(set(by_event.values()), {2})
        self.assertIn(("L.Sonego", "Lorenzo Sonego"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})
        self.assertIn(("Alexander Shevchenko", "Aleksandr Shevchenko"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})
        self.assertIn(("Martin Damm", "Martin Damm Jr"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})

    def test_wimbledon_false_ks_only_examples_pair_when_pm_slug_is_present(self) -> None:
        cases = [
            (
                "atp-majchrz-tabilo-2026-06-29",
                "Wimbledon ATP: Kamil Majchrzak vs Alejandro Tabilo",
                ["Kamil Majchrzak", "Alejandro Tabilo"],
                "KXATPMATCH-26JUN29MAJTAB",
                ["Alejandro Tabilo", "Kamil Majchrzak"],
                ["TAB", "MAJ"],
            ),
            (
                "atp-kovacev-zandsch-2026-06-29",
                "Wimbledon ATP: Aleksandar Kovacevic vs Botic Van de Zandschulp",
                ["Aleksandar Kovacevic", "Botic Van De Zandschulp"],
                "KXATPMATCH-26JUN29KOVVAN",
                ["Aleksandar Kovacevic", "Botic Van de Zandschulp"],
                ["KOV", "VAN"],
            ),
        ]
        for slug, title, pm_outcomes, ks_event, ks_outcomes, suffixes in cases:
            self.add_pm_binary_event(
                slug=slug,
                title=title,
                tag_slug="tennis",
                outcomes=pm_outcomes,
                token_ids=[f"pm-{slug}-a", f"pm-{slug}-b"],
                event_extra={"seriesSlug": "atp", "eventMetadata": {"league": "Wimbledon ATP"}},
                market_extra={"gameStartTime": "2026-06-30 10:00:00-04:00"},
            )
            self.add_ks_binary_event(
                series_ticker="KXATPMATCH",
                event_ticker=ks_event,
                title=f"Will {ks_outcomes[0]} win the Wimbledon Round Of 128 match?",
                outcomes=ks_outcomes,
                suffixes=suffixes,
            )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-29",
        )

        self.assertEqual(counts["safe_pairs"], 4)
        self.assertEqual({pair.event_date for pair in pairs}, {"2026-06-29"})
        self.assertIn(("Kamil Majchrzak", "Kamil Majchrzak"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})
        self.assertIn(("Botic Van De Zandschulp", "Botic Van de Zandschulp"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})

    def test_tennis_alias_pairing_rejects_ambiguous_surname_collision(self) -> None:
        for slug, first_name, token_prefix in (
            ("atp-alex-smith-jones-2026-06-29", "Alex Smith", "alex"),
            ("atp-alan-smith-jones-2026-06-29", "Alan Smith", "alan"),
        ):
            self.add_pm_binary_event(
                slug=slug,
                title=f"Wimbledon ATP: {first_name} vs Bob Jones",
                tag_slug="tennis",
                outcomes=[first_name, "Bob Jones"],
                token_ids=[f"pm-{token_prefix}-smith", f"pm-{token_prefix}-jones"],
                event_extra={"seriesSlug": "atp", "eventMetadata": {"league": "Wimbledon ATP"}},
            )
        self.add_ks_binary_event(
            series_ticker="KXATPMATCH",
            event_ticker="KXATPMATCH-26JUN29SMIJON",
            title="Will Smith win the Smith vs Jones match?",
            outcomes=["Smith", "Jones"],
            suffixes=["SMI", "JON"],
        )
        self.conn.commit()

        pairs, diagnostics, warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="tennis",
            from_date="2026-06-29",
        )

        self.assertEqual(pairs, [])
        self.assertEqual(counts["safe_pairs"], 0)
        self.assertTrue(any("ambiguous tennis alias match" in warning for warning in warnings))
        self.assertTrue(any(row["status"] == "ambiguous_match" for row in diagnostics))

    def test_cs2_alias_pairing_handles_hyphen_reversed_order_and_one_char_team_alias(self) -> None:
        self.add_pm_binary_event(
            slug="cs2-berg-aimhau-2026-06-29",
            title="Esport Berg- Aimhaus",
            tag_slug="cs2",
            outcomes=["Esport Berg", "Aimhaus"],
            token_ids=["pm-berg", "pm-aimhaus"],
        )
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUN290600AIMBERG",
            title="Will Aimhau win the Aimhau vs. Esport BERG CS2 match?",
            outcomes=["Aimhau", "Esport BERG"],
            suffixes=["AIM", "BERG"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="cs2,esports",
            from_date="2026-06-29",
        )

        self.assertEqual(counts["safe_pairs"], 2)
        self.assertIn(("Aimhaus", "Aimhau"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})
        self.assertIn(("Esport Berg", "Esport BERG"), {(pair.pm_yes_outcome, pair.ks_yes_outcome) for pair in pairs})

    def test_cs2_alias_pairing_rejects_multiple_near_team_candidates(self) -> None:
        for suffix, team in (("aimhaus", "Aimhaus"), ("aimhaz", "Aimhaz")):
            self.add_pm_binary_event(
                slug=f"cs2-berg-{suffix}-2026-06-29",
                title=f"Esport Berg- {team}",
                tag_slug="cs2",
                outcomes=["Esport Berg", team],
                token_ids=[f"pm-berg-{suffix}", f"pm-{suffix}"],
            )
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUN290600AIMBERG",
            title="Will Aimhau win the Aimhau vs. Esport BERG CS2 match?",
            outcomes=["Aimhau", "Esport BERG"],
            suffixes=["AIM", "BERG"],
        )
        self.conn.commit()

        pairs, diagnostics, warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="cs2,esports",
            from_date="2026-06-29",
        )

        self.assertEqual(pairs, [])
        self.assertEqual(counts["safe_pairs"], 0)
        self.assertTrue(any("ambiguous esports alias match" in warning for warning in warnings))
        self.assertTrue(any(row["status"] == "ambiguous_match" for row in diagnostics))

    def test_pm_slug_fallback_upserts_required_repair_slugs(self) -> None:
        payloads = {}
        for slug, title, outcomes in (
            (
                "atp-majchrz-tabilo-2026-06-29",
                "Wimbledon ATP: Kamil Majchrzak vs Alejandro Tabilo",
                ["Kamil Majchrzak", "Alejandro Tabilo"],
            ),
            (
                "atp-kovacev-zandsch-2026-06-29",
                "Wimbledon ATP: Aleksandar Kovacevic vs Botic Van de Zandschulp",
                ["Aleksandar Kovacevic", "Botic Van de Zandschulp"],
            ),
            (
                "cs2-berg-aimhau-2026-06-29",
                "Esport Berg- Aimhaus",
                ["Esport Berg", "Aimhaus"],
            ),
        ):
            payloads[slug] = {
                "id": slug,
                "slug": slug,
                "title": title,
                "startDate": "2026-06-29T10:00:00Z",
                "active": True,
                "closed": False,
                "markets": [
                    {
                        "id": f"market-{slug}",
                        "question": title,
                        "active": True,
                        "closed": False,
                        "enableOrderBook": True,
                        "outcomes": market_db.json_text(outcomes),
                        "clobTokenIds": market_db.json_text([f"token-{slug}-a", f"token-{slug}-b"]),
                    }
                ],
            }

        original_get_json = staging_collect_market_db.core.get_json

        def fake_get_json(_base_url, path, params, timeout=20):
            self.assertEqual(path, "/events")
            slug = params.get("slug")
            return [payloads[slug]] if slug in payloads else []

        staging_collect_market_db.core.get_json = fake_get_json
        try:
            count = staging_collect_market_db.collect_pm_repair_metadata(
                self.conn,
                SimpleNamespace(
                    sports="all",
                    pm_extra_slug=[],
                    pm_fallback_max_slugs=3,
                    pm_fallback_search=False,
                    pm_fallback_max_searches=0,
                    pm_page_limit=20,
                ),
            )
        finally:
            staging_collect_market_db.core.get_json = original_get_json

        self.assertEqual(count, 3)
        slugs = {
            row["event_slug"]
            for row in self.conn.execute("SELECT event_slug FROM pm_events")
        }
        self.assertTrue(set(payloads).issubset(slugs))

    def test_db_only_pairer_skips_world_cup_props_and_futures(self) -> None:
        self.add_pm_binary_event(
            slug="world-cup-group-b-winner",
            title="World Cup Group B Winner",
            tag_slug="world-cup",
            outcomes=["Yes", "No"],
            token_ids=["pm-wc-yes", "pm-wc-no"],
            question="Will Canada win Group B in the 2026 FIFA World Cup?",
        )
        self.add_ks_binary_event(
            series_ticker="KXWCGAME",
            event_ticker="KXWCGAME-26JUN24CANQAT",
            title="Canada vs Qatar Winner?",
            outcomes=["Canada", "Qatar", "Tie"],
            suffixes=["CAN", "QAT", "TIE"],
        )
        self.conn.commit()

        pairs, _diagnostics, _warnings, counts = staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="all",
            from_date="2026-06-24",
        )

        self.assertEqual(pairs, [])
        self.assertEqual(counts["safe_pairs"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paired_contracts").fetchone()[0], 0)

    def test_edge_snapshot_uses_db_orderbooks(self) -> None:
        pair = core.PairedContract(
            universe="mlb",
            category="sports",
            match_name="AAA @ BBB",
            event_date="2026-06-24",
            canonical_event_id="mlb:1",
            market_type="game_winner",
            pm_yes_outcome="AAA",
            pm_event_slug="mlb-aaa-bbb-2026-06-24",
            pm_market_id="pm-market",
            pm_token_id="pm-token",
            ks_yes_outcome="AAA",
            ks_event_ticker="KXMLBGAME-26JUN241900AAABBB",
            ks_market_ticker="KXMLBGAME-26JUN241900AAABBB-AAA",
            schedule_source="fixture",
        )
        market_db.upsert_paired_contract(self.conn, pair)
        ts = datetime.now(timezone.utc).isoformat()
        market_db.record_orderbook_success(
            self.conn,
            "pm",
            "pm-token",
            {
                "timestamp": "1780000000",
                "bids": [{"price": "0.60", "size": "10"}],
                "asks": [{"price": "0.61", "size": "10"}],
            },
            ts,
            "/book",
            None,
            10,
        )
        market_db.record_orderbook_success(
            self.conn,
            "ks",
            "KXMLBGAME-26JUN241900AAABBB-AAA",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.70", "8"]],
                    "no_dollars": [["0.25", "8"]],
                }
            },
            ts,
            "/markets/ticker/orderbook",
            0,
            10,
        )
        self.conn.commit()

        rows, warnings = market_db.compute_edge_snapshots(self.conn, ignore_age=True)
        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["alert"], "ALERT")
        self.assertEqual(rows[0]["best_leg"], "PM_YES_KS_NO")
        self.assertEqual(rows[0]["net_edge"], "0.068163")
        self.assertEqual(rows[0]["best_leg_bbo_size"], "8")
        self.assertEqual(rows[0]["net_profit_at_bbo"], "0.545304")
        validate_market_db.validate_pairs_and_edges(self.conn)
        validate_market_db.validate_observations(self.conn)

    def test_zero_bbo_depth_skips_edge_and_alert(self) -> None:
        pair = core.PairedContract(
            universe="mlb",
            category="sports",
            match_name="AAA @ BBB",
            event_date="2026-06-24",
            canonical_event_id="mlb:1",
            market_type="game_winner",
            pm_yes_outcome="AAA",
            pm_event_slug="mlb-aaa-bbb-2026-06-24",
            pm_market_id="pm-market",
            pm_token_id="pm-token",
            ks_yes_outcome="AAA",
            ks_event_ticker="KXMLBGAME-26JUN241900AAABBB",
            ks_market_ticker="KXMLBGAME-26JUN241900AAABBB-AAA",
            schedule_source="fixture",
        )
        market_db.upsert_paired_contract(self.conn, pair)
        ts = datetime.now(timezone.utc).isoformat()
        market_db.record_orderbook_success(
            self.conn,
            "pm",
            "pm-token",
            {
                "timestamp": "1780000000",
                "bids": [{"price": "0.60", "size": "10"}],
                "asks": [{"price": "0.61", "size": "0"}],
            },
            ts,
            "/book",
            None,
            10,
        )
        market_db.record_orderbook_success(
            self.conn,
            "ks",
            "KXMLBGAME-26JUN241900AAABBB-AAA",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.70", "8"]],
                    "no_dollars": [["0.25", "8"]],
                }
            },
            ts,
            "/markets/ticker/orderbook",
            0,
            10,
        )
        self.conn.commit()

        rows, warnings = market_db.compute_edge_snapshots(self.conn, ignore_age=True)

        self.assertEqual(rows, [])
        self.assertTrue(any("non-executable BBO depth" in warning for warning in warnings))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM edge_snapshots").fetchone()[0], 0)
        validate_market_db.validate_pairs_and_edges(self.conn)

    def test_stale_or_skewed_books_skip_edge_and_alert(self) -> None:
        pair = core.PairedContract(
            universe="mlb",
            category="sports",
            match_name="AAA @ BBB",
            event_date="2026-06-24",
            canonical_event_id="mlb:1",
            market_type="game_winner",
            pm_yes_outcome="AAA",
            pm_event_slug="mlb-aaa-bbb-2026-06-24",
            pm_market_id="pm-market",
            pm_token_id="pm-token",
            ks_yes_outcome="AAA",
            ks_event_ticker="KXMLBGAME-26JUN241900AAABBB",
            ks_market_ticker="KXMLBGAME-26JUN241900AAABBB-AAA",
            schedule_source="fixture",
        )
        market_db.upsert_paired_contract(self.conn, pair)
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        market_db.record_orderbook_success(
            self.conn,
            "pm",
            "pm-token",
            {
                "timestamp": "1780000000",
                "bids": [{"price": "0.60", "size": "10"}],
                "asks": [{"price": "0.61", "size": "10"}],
            },
            stale_ts,
            "/book",
            None,
            10,
        )
        market_db.record_orderbook_success(
            self.conn,
            "ks",
            "KXMLBGAME-26JUN241900AAABBB-AAA",
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.70", "8"]],
                    "no_dollars": [["0.25", "8"]],
                }
            },
            fresh_ts,
            "/markets/ticker/orderbook",
            0,
            10,
        )
        self.conn.commit()

        rows, warnings = market_db.compute_edge_snapshots(self.conn)

        self.assertEqual(rows, [])
        self.assertTrue(any("stale/skewed DB orderbook skipped" in warning for warning in warnings))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM edge_snapshots").fetchone()[0], 0)
        validate_market_db.validate_pairs_and_edges(self.conn)


if __name__ == "__main__":
    unittest.main()
