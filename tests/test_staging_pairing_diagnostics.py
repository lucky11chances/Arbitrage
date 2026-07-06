from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import old_market_db  # noqa: E402
import old_pipeline_core as core  # noqa: E402
import old_staging_pair_from_db  # noqa: E402


class StagingPairingDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.conn = old_market_db.connect(Path(self.tempdir.name) / "pairing.sqlite")
        old_market_db.init_db(self.conn)

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
        start_time_utc: str,
        market_id: str | None = None,
        question: str | None = None,
        sports_market_type: str = "moneyline",
        group_item_title: str = "Match Winner",
    ) -> None:
        market_id = market_id or f"market-{slug}"
        market = {
            "id": market_id,
            "question": question or title,
            "active": True,
            "closed": False,
            "enableOrderBook": True,
            "outcomes": old_market_db.json_text(outcomes),
            "clobTokenIds": old_market_db.json_text([f"{market_id}-token-a", f"{market_id}-token-b"]),
            "sportsMarketType": sports_market_type,
            "groupItemTitle": group_item_title,
            "gameStartTime": start_time_utc,
            "eventStartTime": start_time_utc,
        }
        event = {
            "id": slug,
            "slug": slug,
            "title": title,
            "description": title,
            "startDate": "2026-06-01T00:00:00Z",
            "endDate": start_time_utc,
            "active": True,
            "closed": False,
            "tags": [{"slug": tag_slug, "label": tag_slug}],
            "markets": [market],
        }
        old_market_db.upsert_pm_event(self.conn, event, tag_slug=tag_slug)

    def add_pm_event_with_markets(
        self,
        *,
        slug: str,
        title: str,
        tag_slug: str,
        markets: list[dict],
        start_time_utc: str,
    ) -> None:
        event = {
            "id": slug,
            "slug": slug,
            "title": title,
            "description": title,
            "startDate": "2026-06-01T00:00:00Z",
            "endDate": start_time_utc,
            "active": True,
            "closed": False,
            "tags": [{"slug": tag_slug, "label": tag_slug}],
            "markets": markets,
        }
        old_market_db.upsert_pm_event(self.conn, event, tag_slug=tag_slug)

    def add_ks_binary_event(
        self,
        *,
        series_ticker: str,
        event_ticker: str,
        title: str,
        outcomes: list[str],
        suffixes: list[str],
        close_time: str = "2026-07-04T00:00:00Z",
    ) -> None:
        for outcome, suffix in zip(outcomes, suffixes):
            old_market_db.upsert_ks_market(
                self.conn,
                series_ticker,
                {
                    "ticker": f"{event_ticker}-{suffix}",
                    "event_ticker": event_ticker,
                    "title": title,
                    "yes_sub_title": outcome,
                    "status": "active",
                    "close_time": close_time,
                },
            )

    def build(self):
        return old_staging_pair_from_db.build_pairs_csv_only(self.conn, sports="all", from_date=None)

    def test_no_candidate_ks_event_is_ksonly_not_aliasweak(self) -> None:
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUN290630MILADN",
            title="Will Alpha Dominion Nation win the Millennium Esports vs. Alpha Dominion Nation CS2 match?",
            outcomes=["Alpha Dominion Nation", "Millennium Esports"],
            suffixes=["ADN", "MIL"],
        )

        _pairs, diagnostics, _warnings, _counts = self.build()

        row = next(row for row in diagnostics if row["ks_event_ticker"] == "KXCS2GAME-26JUN290630MILADN")
        self.assertEqual(row["status"], "unmatched_semantics")
        self.assertIn("no PM event", row["reason"])
        self.assertNotIn("likely_pair_after_alias", row["reason"])
        self.assertEqual(row["pm_event_name"], "")

    def test_no_candidate_pm_event_is_pmonly_not_aliasweak(self) -> None:
        self.add_pm_binary_event(
            slug="cs2-alpha-bravo-2026-07-01",
            title="Counter-Strike: Alpha vs Bravo (BO3)",
            tag_slug="cs2",
            outcomes=["Alpha", "Bravo"],
            start_time_utc="2026-07-01T08:00:00Z",
        )

        _pairs, diagnostics, _warnings, _counts = self.build()

        row = next(row for row in diagnostics if row["pm_event_slug"] == "cs2-alpha-bravo-2026-07-01")
        self.assertEqual(row["status"], "pm_only_inventory")
        self.assertIn("no KS event", row["reason"])
        self.assertNotIn("likely_pair_after_alias", row["reason"])
        self.assertEqual(row["ks_event_name"], "")

    def test_candidate_match_has_pm_and_ks_event_information(self) -> None:
        self.add_pm_binary_event(
            slug="cs2-alpha-bravo-2026-07-01",
            title="Counter-Strike: Alpha vs Bravo (BO3)",
            tag_slug="cs2",
            outcomes=["Alpha", "Bravo"],
            start_time_utc="2026-07-01T08:00:00Z",
        )
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUL010400ALPCHA",
            title="Will Alpha win the Alpha vs. Charlie CS2 match?",
            outcomes=["Alpha", "Charlie"],
            suffixes=["ALP", "CHA"],
        )

        pairs, diagnostics, _warnings, _counts = self.build()

        self.assertEqual(pairs, [])
        candidate = next(row for row in diagnostics if row["status"] == "candidate_match")
        self.assertEqual(candidate["pm_event_name"], "Counter-Strike: Alpha vs Bravo (BO3)")
        self.assertIn("Alpha vs. Charlie", candidate["ks_event_name"])
        self.assertEqual(candidate["candidate_side"], "PM candidate for KS event")
        self.assertNotEqual(candidate["why_not_safe"], "")

    def test_wnba_nickname_and_time_match_safely(self) -> None:
        self.add_pm_binary_event(
            slug="wnba-las-nyl-2026-06-30",
            title="Las Vegas Aces vs. New York Liberty",
            tag_slug="wnba",
            outcomes=["Las Vegas Aces", "New York Liberty"],
            start_time_utc="2026-06-30T23:00:00Z",
        )
        self.add_ks_binary_event(
            series_ticker="KXWNBAGAME",
            event_ticker="KXWNBAGAME-26JUN30LVNY",
            title="Las Vegas vs New York winner?",
            outcomes=["Aces", "Liberty"],
            suffixes=["LV", "NY"],
            close_time="2026-07-14T23:00:00Z",
        )

        pairs, _diagnostics, _warnings, _counts = self.build()

        self.assertEqual(len(pairs), 2)
        self.assertEqual({pair.pm_yes_outcome for pair in pairs}, {"Las Vegas Aces", "New York Liberty"})
        self.assertEqual({pair.ks_yes_outcome for pair in pairs}, {"Aces", "Liberty"})

    def test_mlb_city_nickname_abbreviation_and_time_match_safely(self) -> None:
        cases = [
            (
                "mlb-cin-mil-2026-06-30",
                "Cincinnati Reds vs. Milwaukee Brewers",
                ["Cincinnati Reds", "Milwaukee Brewers"],
                "2026-06-30T23:40:00Z",
                "KXMLBGAME-26JUN301940CINMIL",
                "Cincinnati vs Milwaukee Winner?",
                ["Cincinnati", "Milwaukee"],
                ["CIN", "MIL"],
            ),
            (
                "mlb-det-nyy-2026-07-01",
                "Detroit Tigers vs. New York Yankees",
                ["Detroit Tigers", "New York Yankees"],
                "2026-07-01T17:35:00Z",
                "KXMLBGAME-26JUL011335DETNYY",
                "Detroit vs New York Y Winner?",
                ["Detroit", "New York Y"],
                ["DET", "NYY"],
            ),
        ]
        for slug, title, pm_outcomes, start, ticker, ks_title, ks_outcomes, suffixes in cases:
            self.add_pm_binary_event(
                slug=slug,
                title=title,
                tag_slug="mlb",
                outcomes=pm_outcomes,
                start_time_utc=start,
            )
            self.add_ks_binary_event(
                series_ticker="KXMLBGAME",
                event_ticker=ticker,
                title=ks_title,
                outcomes=ks_outcomes,
                suffixes=suffixes,
            )

        pairs, _diagnostics, _warnings, _counts = self.build()

        self.assertEqual(len(pairs), 4)
        self.assertEqual({pair.ks_event_ticker for pair in pairs}, {"KXMLBGAME-26JUN301940CINMIL", "KXMLBGAME-26JUL011335DETNYY"})

    def test_safe_pair_writes_separate_pm_and_ks_canonical_event_maps(self) -> None:
        self.add_pm_binary_event(
            slug="mlb-cin-mil-2026-06-30",
            title="Cincinnati Reds vs. Milwaukee Brewers",
            tag_slug="mlb",
            outcomes=["Cincinnati Reds", "Milwaukee Brewers"],
            start_time_utc="2026-06-30T23:40:00Z",
        )
        self.add_ks_binary_event(
            series_ticker="KXMLBGAME",
            event_ticker="KXMLBGAME-26JUN301940CINMIL",
            title="Cincinnati vs Milwaukee Winner?",
            outcomes=["Cincinnati", "Milwaukee"],
            suffixes=["CIN", "MIL"],
        )

        pairs, _diagnostics, _warnings, counts = old_staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="all",
            from_date=None,
        )

        self.assertEqual(counts["safe_pairs"], 2)
        canonical_ids = {pair.canonical_event_id for pair in pairs}
        self.assertEqual(len(canonical_ids), 1)
        canonical_id = next(iter(canonical_ids))
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM canonical_events WHERE canonical_event_id = ?",
                (canonical_id,),
            ).fetchone()
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT canonical_event_id FROM pm_canonical_event_map WHERE pm_event_slug = ?",
                ("mlb-cin-mil-2026-06-30",),
            ).fetchone()["canonical_event_id"],
            canonical_id,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT canonical_event_id FROM ks_canonical_event_map WHERE ks_event_ticker = ?",
                ("KXMLBGAME-26JUN301940CINMIL",),
            ).fetchone()["canonical_event_id"],
            canonical_id,
        )

    def test_uncertain_name_similar_candidate_does_not_write_canonical_maps(self) -> None:
        self.add_pm_binary_event(
            slug="cs2-alpha-bravo-2026-07-01",
            title="Counter-Strike: Alpha vs Bravo (BO3)",
            tag_slug="cs2",
            outcomes=["Alpha", "Bravo"],
            start_time_utc="2026-07-01T08:00:00Z",
        )
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUL010400ALPCHA",
            title="Will Alpha win the Alpha vs. Charlie CS2 match?",
            outcomes=["Alpha", "Charlie"],
            suffixes=["ALP", "CHA"],
        )

        pairs, diagnostics, _warnings, counts = old_staging_pair_from_db.build_pairs_from_db(
            self.conn,
            sports="all",
            from_date=None,
        )

        self.assertEqual(pairs, [])
        self.assertEqual(counts["safe_pairs"], 0)
        self.assertTrue(any(row["status"] == "candidate_match" for row in diagnostics))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM pm_canonical_event_map").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ks_canonical_event_map").fetchone()[0], 0)

    def test_esports_order_insensitive_teams_and_time_match_safely(self) -> None:
        self.add_pm_binary_event(
            slug="cs2-9z-eyeballers-2026-07-01",
            title="Counter-Strike: 9z vs EYEBALLERS (BO3)",
            tag_slug="cs2",
            outcomes=["9z", "EYEBALLERS"],
            start_time_utc="2026-07-01T08:00:00Z",
        )
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUL010400EYE9Z",
            title="Will 9z win the EYEBALLERS vs. 9z CS2 match?",
            outcomes=["9z", "EYEBALLERS"],
            suffixes=["9Z", "EYE"],
        )

        pairs, _diagnostics, _warnings, _counts = self.build()

        self.assertEqual(len(pairs), 2)
        self.assertEqual({pair.pm_yes_outcome for pair in pairs}, {"9z", "EYEBALLERS"})

    def test_esports_same_team_set_time_conflict_is_candidate_not_safe(self) -> None:
        self.add_pm_binary_event(
            slug="cs2-9z-eye-2026-07-01",
            title="Counter-Strike: 9z vs EYEBALLERS (BO1) - XSE Pro League Group Stage",
            tag_slug="cs2",
            outcomes=["9z", "EYEBALLERS"],
            start_time_utc="2026-07-01T14:00:00Z",
        )
        self.add_ks_binary_event(
            series_ticker="KXCS2GAME",
            event_ticker="KXCS2GAME-26JUL010400EYE9Z",
            title="Will 9z win the EYEBALLERS vs. 9z CS2 match?",
            outcomes=["9z", "EYEBALLERS"],
            suffixes=["9Z", "EYE"],
        )

        pairs, diagnostics, _warnings, _counts = self.build()

        self.assertEqual(pairs, [])
        candidate = next(row for row in diagnostics if row["status"] == "candidate_match")
        self.assertEqual(candidate["pm_event_name"], "Counter-Strike: 9z vs EYEBALLERS (BO1) - XSE Pro League Group Stage")
        self.assertIn("EYEBALLERS vs. 9z", candidate["ks_event_name"])
        self.assertIn("360 minutes", candidate["why_candidate"])
        self.assertEqual(candidate["safe_paired"], "false")

    def test_valorant_full_match_winner_pairs_but_map_markets_do_not(self) -> None:
        start = "2026-07-01T08:00:00Z"
        markets = [
            {
                "id": "map-1",
                "question": "Valorant: DRX Prospects vs ARETE - Map 1 Winner",
                "active": True,
                "closed": False,
                "enableOrderBook": True,
                "outcomes": old_market_db.json_text(["DRX Prospects", "ARETE"]),
                "clobTokenIds": old_market_db.json_text(["map-1-a", "map-1-b"]),
                "sportsMarketType": "child_moneyline",
                "groupItemTitle": "Map 1 Winner",
                "gameStartTime": start,
                "eventStartTime": start,
            },
            {
                "id": "match-winner",
                "question": "Valorant: DRX Prospects vs ARETE (BO3) - VCL Korea: Regular Season",
                "active": True,
                "closed": False,
                "enableOrderBook": True,
                "outcomes": old_market_db.json_text(["DRX Prospects", "ARETE"]),
                "clobTokenIds": old_market_db.json_text(["match-a", "match-b"]),
                "sportsMarketType": "moneyline",
                "groupItemTitle": "Match Winner",
                "gameStartTime": start,
                "eventStartTime": start,
            },
        ]
        self.add_pm_event_with_markets(
            slug="val-drxp-art-2026-07-01",
            title="Valorant: DRX Prospects vs ARETE (BO3) - VCL Korea: Regular Season",
            tag_slug="valorant",
            markets=markets,
            start_time_utc=start,
        )
        self.add_ks_binary_event(
            series_ticker="KXVALORANTGAME",
            event_ticker="KXVALORANTGAME-26JUL010400DRXARE",
            title="Will ARETE win the ARETE vs. DRX Prospects Valorant match?",
            outcomes=["ARETE", "DRX Prospects"],
            suffixes=["ARE", "DRXP"],
        )

        pairs, _diagnostics, _warnings, _counts = self.build()

        self.assertEqual(len(pairs), 2)
        self.assertEqual({pair.pm_market_id for pair in pairs}, {"match-winner"})

    def test_lol_futures_do_not_satisfy_ks_match_winner(self) -> None:
        self.add_pm_binary_event(
            slug="lol-team-liquid-msi-winner-2026",
            title="League of Legends: Will Team Liquid win MSI?",
            tag_slug="lol",
            outcomes=["Yes", "No"],
            start_time_utc="2026-06-29T08:00:00Z",
            question="League of Legends: Will Team Liquid win MSI?",
            sports_market_type="outright",
            group_item_title="Tournament Winner",
        )
        self.add_ks_binary_event(
            series_ticker="KXLOLGAME",
            event_ticker="KXLOLGAME-26JUN290400DCGTL",
            title="Will Deep Cross Gaming win the Deep Cross Gaming vs. Team Liquid League of Legends match?",
            outcomes=["Deep Cross Gaming", "Team Liquid"],
            suffixes=["DCG", "TL"],
        )

        pairs, diagnostics, _warnings, _counts = self.build()

        self.assertEqual(pairs, [])
        row = next(row for row in diagnostics if row["ks_event_ticker"] == "KXLOLGAME-26JUN290400DCGTL")
        self.assertEqual(row["status"], "unmatched_semantics")
        self.assertIn("no PM event", row["reason"])

    def test_football_future_ks_event_remains_ksonly(self) -> None:
        self.add_ks_binary_event(
            series_ticker="KXNFLGAME",
            event_ticker="KXNFLGAME-26AUG30TBDET",
            title="Will Detroit win the Tampa Bay vs Detroit Pro Football game?",
            outcomes=["Tampa Bay", "Detroit"],
            suffixes=["TB", "DET"],
        )

        pairs, diagnostics, _warnings, _counts = self.build()

        self.assertEqual(pairs, [])
        row = next(row for row in diagnostics if row["ks_event_ticker"] == "KXNFLGAME-26AUG30TBDET")
        self.assertEqual(row["status"], "unmatched_semantics")
        self.assertIn("no PM event", row["reason"])

    def test_incremental_refresh_does_not_delete_edge_snapshots(self) -> None:
        pair_id = old_market_db.upsert_paired_contract(
            self.conn,
            core.PairedContract(
                universe="mlb",
                category="sports",
                match_name="AAA vs BBB",
                event_date="2026-07-01",
                canonical_event_id="mlb:2026-07-01:aaa|bbb",
                market_type="game_winner",
                pm_yes_outcome="AAA",
                ks_yes_outcome="AAA",
                pm_event_slug="mlb-aaa-bbb-2026-07-01",
                pm_market_id="pm-market",
                pm_token_id="pm-token",
                ks_event_ticker="KXMLBGAME-26JUL011200AAABBB",
                ks_market_ticker="KXMLBGAME-26JUL011200AAABBB-AAA",
                schedule_source="unit-test",
            ),
        )
        pm_obs = old_market_db.record_orderbook_success(
            self.conn,
            "pm",
            "pm-token",
            {"bids": [{"price": "0.45", "size": "10"}], "asks": [{"price": "0.55", "size": "10"}]},
            "2026-07-01T00:00:00+00:00",
            "/pm",
            1,
            1,
        )
        ks_obs = old_market_db.record_orderbook_success(
            self.conn,
            "ks",
            "KXMLBGAME-26JUL011200AAABBB-AAA",
            {"orderbook_fp": {"yes_dollars": [["0.45", "10"]], "no_dollars": [["0.45", "10"]]}},
            "2026-07-01T00:00:00+00:00",
            "/ks",
            1,
            1,
        )
        self.conn.execute(
            """
            INSERT INTO edge_snapshots (
                paired_contract_id, ts_utc, pm_observation_id, ks_observation_id,
                pm_bid_scaled, pm_ask_scaled, pm_bid_size_scaled, pm_ask_size_scaled,
                ks_bid_scaled, ks_ask_scaled, ks_bid_size_scaled, ks_ask_size_scaled
            ) VALUES (?, ?, ?, ?, 450000, 550000, 10000000, 10000000, 450000, 550000, 10000000, 10000000)
            """,
            (pair_id, "2026-07-01T00:00:00+00:00", pm_obs, ks_obs),
        )
        self.conn.commit()

        old_staging_pair_from_db.refresh_pairs_incremental(self.conn, sports="mlb", from_date=None)

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM edge_snapshots").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT safe_paired FROM paired_contracts WHERE paired_contract_id = ?", (pair_id,)).fetchone()[0],
            0,
        )

    def test_human_review_fields_do_not_expose_machine_identifiers(self) -> None:
        script = (ROOT / "tmp/spreadsheets/build_unmatched_human_review.mjs").read_text()
        fields_block = script.split("const fields = [", 1)[1].split("];", 1)[0].lower()
        for forbidden in ("ticker", "slug", "token"):
            self.assertNotIn(forbidden, fields_block)


if __name__ == "__main__":
    unittest.main()
