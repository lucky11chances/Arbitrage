from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import index_event_index_core  # noqa: E402
import index_event_index_db  # noqa: E402
import index_event_index_sources  # noqa: E402
import index_sports_registry  # noqa: E402
import index_taxonomy  # noqa: E402
import index_universe_adapters  # noqa: E402


class EventIndexDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "event_index.sqlite"
        self.conn = index_event_index_db.connect(self.db_path)
        index_event_index_db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_index_db_rejects_legacy_and_ws_db_paths(self) -> None:
        with self.assertRaises(ValueError):
            index_event_index_db.connect(ROOT / "data/old/old_arb_research.sqlite")
        with self.assertRaises(ValueError):
            index_event_index_db.connect(ROOT / "data/new/new_arb_research.sqlite")

    def test_source_registry_covers_all_taxonomy_categories(self) -> None:
        coverage = index_event_index_sources.registry_coverage()
        missing = [category.key for category in index_sports_registry.inventory_categories() if not coverage.get(category.key)]
        self.assertEqual(missing, [])

    def test_taxonomy_schema_tables_and_view_exist(self) -> None:
        tables = {
            row["name"]
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }

        self.assertIn("index_sports", tables)
        self.assertIn("index_competitions", tables)
        self.assertIn("index_platform_categories", tables)
        self.assertIn("index_platform_category_map", tables)
        self.assertIn("index_competition_sources", tables)
        self.assertIn("index_taxonomy_full_join", tables)

    def test_seed_taxonomy_maps_platform_examples(self) -> None:
        counts = index_taxonomy.seed_index_taxonomy(self.conn)

        self.assertEqual(counts["sports"], 16)
        self.assertGreaterEqual(counts["competitions"], 55)

        pm_mlb = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'pm' AND platform_key_type = 'tag_slug' AND platform_key = 'mlb'
            """
        ).fetchone()
        pm_baseball = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'pm' AND platform_key_type = 'tag_slug' AND platform_key = 'baseball'
            """
        ).fetchone()
        ks_mlb = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'ks' AND platform_key_type = 'series_ticker' AND platform_key = 'KXMLBGAME'
            """
        ).fetchone()
        ks_boxing = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'ks' AND platform_key_type = 'series_ticker' AND platform_key = 'KXBOXING'
            """
        ).fetchone()
        pm_ufc = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'pm' AND platform_key_type = 'tag_slug' AND platform_key = 'ufc'
            """
        ).fetchone()
        ks_f1 = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'ks' AND platform_key_type = 'series_ticker' AND platform_key = 'KXF1'
            """
        ).fetchone()

        self.assertEqual(dict(pm_mlb), {"sport_key": "baseball", "competition_key": "mlb", "mapping_status": "mapped"})
        self.assertEqual(pm_baseball["sport_key"], "baseball")
        self.assertEqual(pm_baseball["mapping_status"], "broad")
        self.assertEqual(dict(ks_mlb), {"sport_key": "baseball", "competition_key": "mlb", "mapping_status": "mapped"})
        self.assertEqual(dict(ks_boxing), {"sport_key": "boxing", "competition_key": "boxing", "mapping_status": "mapped"})
        self.assertEqual(dict(pm_ufc), {"sport_key": "mma", "competition_key": "ufc", "mapping_status": "mapped"})
        self.assertEqual(dict(ks_f1), {"sport_key": "motor_sports", "competition_key": "f1", "mapping_status": "mapped"})

    def test_taxonomy_excludes_pm_observed_sports_adjacent_market_categories(self) -> None:
        index_taxonomy.seed_index_taxonomy(self.conn)
        discovered = index_taxonomy.upsert_discovered_platform_categories(
            self.conn,
            [
                index_taxonomy.PlatformCategory("pm", "tag_slug", "futures", "Sports Futures"),
                index_taxonomy.PlatformCategory("pm", "tag_slug", "home-runs", "Home Run Leaders"),
                index_taxonomy.PlatformCategory("pm", "tag_slug", "mlb", "MLB"),
            ],
        )

        motorsports = self.conn.execute(
            """
            SELECT sport_key, competition_key, mapping_status
            FROM index_platform_category_map
            WHERE platform = 'pm' AND platform_key_type = 'tag_slug' AND platform_key = 'motorsports'
            """
        ).fetchone()
        forbidden_sports = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM index_sports
            WHERE sport_key LIKE 'sports_%'
            """
        ).fetchone()[0]
        forbidden_categories = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM index_platform_categories
            WHERE platform = 'pm'
              AND platform_key IN (
                  'futures', 'playoffs', 'postseason', 'nba-draft', 'nfl-draft',
                  'home-runs', 'statistical-leaders', 'awards', 'mvp',
                  'nba-free-agency', 'video-games', 'madden-nfl'
              )
            """
        ).fetchone()[0]
        forbidden_maps = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM index_platform_category_map
            WHERE sport_key LIKE 'sports_%'
            """
        ).fetchone()[0]

        self.assertEqual(discovered, 1)
        self.assertEqual(dict(motorsports), {"sport_key": "motor_sports", "competition_key": "generic_motorsports", "mapping_status": "broad"})
        self.assertEqual(forbidden_sports, 0)
        self.assertEqual(forbidden_categories, 0)
        self.assertEqual(forbidden_maps, 0)

    def test_taxonomy_full_join_reports_platform_and_source_coverage(self) -> None:
        index_taxonomy.seed_index_taxonomy(self.conn)

        mlb = self.conn.execute(
            "SELECT * FROM index_taxonomy_full_join WHERE sport_key = 'baseball' AND competition_key = 'mlb'"
        ).fetchone()
        kbo = self.conn.execute(
            "SELECT * FROM index_taxonomy_full_join WHERE sport_key = 'baseball' AND competition_key = 'kbo'"
        ).fetchone()
        ncaa_mbb = self.conn.execute(
            "SELECT * FROM index_taxonomy_full_join WHERE sport_key = 'basketball' AND competition_key = 'ncaa_mbb'"
        ).fetchone()
        tennis_atp = self.conn.execute(
            "SELECT * FROM index_taxonomy_full_join WHERE sport_key = 'tennis' AND competition_key = 'atp'"
        ).fetchone()

        self.assertEqual(mlb["coverage_status"], "both_platforms")
        self.assertIn("tag_slug:mlb:mapped", mlb["pm_categories"])
        self.assertIn("series_ticker:KXMLBGAME:mapped", mlb["ks_categories"])
        self.assertIn("mlb_stats_api:enabled", mlb["source_keys"])

        self.assertEqual(kbo["coverage_status"], "pm_only")
        self.assertIn("tag_slug:kbo:mapped", kbo["pm_categories"])
        self.assertEqual(kbo["ks_categories"], "")

        self.assertEqual(ncaa_mbb["coverage_status"], "ks_only")
        self.assertEqual(ncaa_mbb["pm_categories"], "")
        self.assertIn("series_ticker:KXNCAABGAME:mapped", ncaa_mbb["ks_categories"])

        self.assertEqual(tennis_atp["coverage_status"], "both_platforms")
        self.assertIn("balldontlie_atp_matches:enabled_with_api_key", tennis_atp["source_keys"])
        self.assertNotIn("source_gap", tennis_atp["source_statuses"])

    def test_kbo_official_site_fixture_parser_creates_source_first_event(self) -> None:
        html = """
        <table>
          <tr><th>Date</th><th>Time</th><th>Game</th><th>Venue</th></tr>
          <tr><td>07.08</td><td>18:30</td><td>LG vs KIA</td><td>Jamsil</td></tr>
        </table>
        """

        events, warnings = index_event_index_sources.parse_kbo_schedule_html(html, default_year=2026)

        self.assertEqual(warnings, [])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.sport_key, "baseball")
        self.assertEqual(event.competition_key, "kbo")
        self.assertEqual(event.source_key, "kbo_official_site")
        self.assertEqual(event.source_type, "official_site")
        self.assertEqual(event.event_date, "2026-07-08")
        self.assertEqual(event.start_time_utc, "2026-07-08T09:30:00Z")
        self.assertEqual(event.venue_name, "Jamsil")
        self.assertEqual(event.match_name, "LG Twins @ KIA Tigers")
        self.assertIn("2026-07-08:18:30:lgtwins:kiatigers:jamsil", event.source_event_id)
        self.assertEqual([participant.role for participant in event.participants], ["away", "home"])

    def test_kbo_official_json_payload_uses_site_game_id_when_present(self) -> None:
        payload = {
            "rows": [
                {
                    "row": [
                        {"Text": "07.01(Wed)", "Class": "day"},
                        {"Text": "<b>18:30</b>", "Class": "time"},
                        {"Text": "<span>Lotte</span><em><span>5</span><span>vs</span><span>2</span></em><span>Doosan</span>", "Class": "play"},
                        {"Text": "<a href='/Schedule/GameCenter/Main.aspx?gameDate=20260701&gameId=20260701LTOB0&section=REVIEW'>Review</a>", "Class": "relay"},
                        {"Text": ""},
                        {"Text": ""},
                        {"Text": "Jamsil"},
                        {"Text": "-"},
                    ]
                }
            ]
        }

        events, warnings = index_event_index_sources.parse_kbo_schedule_payload(payload, default_year=2026)

        self.assertEqual(warnings, [])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.source_event_id, "20260701LTOB0")
        self.assertEqual(event.canonical_event_id, index_event_index_core.canonical_event_id_from_source("kbo", "kbo_official_site", "20260701LTOB0"))
        self.assertEqual(event.match_name, "Lotte Giants @ Doosan Bears")
        self.assertEqual(event.venue_name, "Jamsil")

    def test_pandascore_cs2_fixture_maps_teams_and_rejects_tbd(self) -> None:
        event = index_event_index_sources.pandascore_cs2_index_event(
            {
                "id": 456,
                "begin_at": "2026-07-08T16:00:00Z",
                "opponents": [
                    {"opponent": {"id": 10, "name": "Natus Vincere", "acronym": "NAVI"}},
                    {"opponent": {"id": 20, "name": "Team Spirit", "acronym": "TS"}},
                ],
                "tournament": {"name": "IEM Cologne"},
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.sport_key, "esports")
        self.assertEqual(event.competition_key, "cs2")
        self.assertEqual(event.source_key, "pandascore_cs2_fixtures")
        self.assertEqual(event.source_type, "vetted_provider_api")
        self.assertEqual(event.source_event_id, "456")
        self.assertEqual(event.event_date, "2026-07-08")
        self.assertEqual(event.venue_name, "IEM Cologne")
        self.assertIn("NAVI", event.participants[0].aliases)

        rejected = index_event_index_sources.pandascore_cs2_index_event(
            {
                "id": 457,
                "begin_at": "2026-07-08T16:00:00Z",
                "opponents": [
                    {"opponent": {"id": 10, "name": "Natus Vincere", "acronym": "NAVI"}},
                    {"opponent": {"id": 0, "name": "TBD", "acronym": ""}},
                ],
            }
        )
        self.assertIsNone(rejected)

    def test_balldontlie_tennis_match_creates_player_event(self) -> None:
        event = index_event_index_sources.balldontlie_tennis_index_event(
            "atp",
            "balldontlie_atp_matches",
            {
                "id": 789,
                "scheduled_time": "2026-07-08T13:00:00Z",
                "player1": {"id": 1, "full_name": "Carlos Alcaraz", "first_name": "Carlos", "last_name": "Alcaraz"},
                "player2": {"id": 2, "full_name": "Jannik Sinner", "first_name": "Jannik", "last_name": "Sinner"},
                "tournament": {"name": "Wimbledon", "location": "London"},
            },
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.sport_key, "tennis")
        self.assertEqual(event.competition_key, "atp")
        self.assertEqual(event.competition_gender, "men")
        self.assertEqual(event.source_type, "paid_provider_api")
        self.assertEqual(event.market_type, "match_winner")
        self.assertEqual(event.venue_name, "Wimbledon")
        self.assertEqual(event.venue_city, "London")
        self.assertEqual([participant.display_name for participant in event.participants], ["Carlos Alcaraz", "Jannik Sinner"])

    def test_balldontlie_ufc_fight_creates_fighter_event(self) -> None:
        event = index_event_index_sources.balldontlie_mma_index_event(
            {
                "id": 987,
                "event": {
                    "date": "2026-07-12T02:00:00Z",
                    "league": {"abbreviation": "UFC", "name": "Ultimate Fighting Championship"},
                    "short_name": "UFC 999",
                    "venue_name": "T-Mobile Arena",
                    "venue_city": "Las Vegas",
                    "venue_state": "NV",
                    "venue_country": "US",
                },
                "fighter1": {"id": 100, "name": "Example Fighter", "first_name": "Example", "last_name": "Fighter"},
                "fighter2": {"id": 200, "name": "Sample Striker", "first_name": "Sample", "last_name": "Striker"},
                "weight_class": {"gender": "male"},
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.sport_key, "mma")
        self.assertEqual(event.competition_key, "ufc")
        self.assertEqual(event.market_type, "fighter_winner")
        self.assertEqual(event.source_key, "balldontlie_mma_fights")
        self.assertEqual(event.source_type, "paid_provider_api")
        self.assertEqual(event.venue_name, "T-Mobile Arena")
        self.assertEqual([participant.source_participant_id for participant in event.participants], ["ufc_fighter_100", "ufc_fighter_200"])

    def test_missing_api_key_source_run_writes_diagnostic_only(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            counts = index_event_index_sources.sync_index_event_index_sources(
                self.conn,
                sports="cs2",
                from_date="2026-07-08",
                thru_date="2026-07-08",
                source_keys={"pandascore_cs2_fixtures"},
            )

        self.assertEqual(counts["missing_api_keys"], 1)
        self.assertEqual(counts["events_upserted"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0], 0)
        run = self.conn.execute(
            "SELECT status, message FROM event_index_source_runs WHERE source_key = 'pandascore_cs2_fixtures'"
        ).fetchone()
        self.assertEqual(run["status"], "missing_api_key")
        self.assertIn("INDEX_PANDASCORE_API_KEY", run["message"])

    def test_static_world_cup_sync_populates_index_only(self) -> None:
        counts = index_event_index_sources.sync_index_event_index_sources(
            self.conn,
            sports="world_cup",
            from_date="2026-06-11",
            thru_date="2026-06-11",
            source_keys={"fifa_world_cup_2026_local_schedule"},
        )

        self.assertGreater(counts["events_upserted"], 0)
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0], 0)
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM event_index_source_catalog").fetchone()[0], 0)
        tables = {row["name"] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("pm_events", tables)
        self.assertNotIn("ks_events", tables)
        self.assertNotIn("paired_contracts", tables)

    def test_index_event_writes_venue_time_source_identity_and_raw_payload(self) -> None:
        event = index_event_index_core.IndexedEvent(
            canonical_event_id=index_event_index_core.canonical_event_id_from_source("mlb", "mlb_stats_api", "123"),
            category_key="baseball",
            universe="mlb",
            sport_key="baseball",
            competition_key="mlb",
            season="2026",
            event_date="2026-07-04",
            source_local_date="2026-07-04",
            start_time_utc="2026-07-04T23:10:00+00:00",
            market_type="game_winner",
            competition_gender="men",
            match_name="Away Bears @ Home Cats",
            event_name="Away Bears @ Home Cats",
            venue_id="2602",
            venue_name="Great American Ball Park",
            source_key="mlb_stats_api",
            source_event_id="123",
            source_type="official_api",
            source_confidence="high",
            source_url=index_event_index_sources.MLB_SOURCE_URL,
            participants=(
                index_event_index_core.IndexedParticipant("baseball:mlb:men:away", "Away Bears", "away", ("Away", "Bears"), "away", 0),
                index_event_index_core.IndexedParticipant("baseball:mlb:men:home", "Home Cats", "home", ("Home", "Cats"), "home", 1),
            ),
            raw_payload={"game_pk": 123, "venue_name": "Great American Ball Park"},
        )

        index_event_index_db.upsert_indexed_event(self.conn, event)
        self.conn.commit()

        row = self.conn.execute(
            """
            SELECT sport_key, competition_key, season, event_name, start_time_utc,
                   venue_id, venue_name, source_event_id, source_confidence
            FROM canonical_events
            WHERE canonical_event_id = ?
            """,
            (event.canonical_event_id,),
        ).fetchone()
        source_row = self.conn.execute(
            "SELECT raw_payload_hash, raw_payload_json FROM canonical_event_sources WHERE source_key = ? AND source_event_id = ?",
            ("mlb_stats_api", "123"),
        ).fetchone()

        self.assertEqual(row["sport_key"], "baseball")
        self.assertEqual(row["competition_key"], "mlb")
        self.assertEqual(row["season"], "2026")
        self.assertEqual(row["event_name"], "Away Bears @ Home Cats")
        self.assertEqual(row["start_time_utc"], "2026-07-04T23:10:00Z")
        self.assertEqual(row["venue_id"], "2602")
        self.assertEqual(row["venue_name"], "Great American Ball Park")
        self.assertEqual(row["source_event_id"], "123")
        self.assertEqual(row["source_confidence"], "high")
        self.assertTrue(source_row["raw_payload_hash"])
        self.assertIn("Great American Ball Park", source_row["raw_payload_json"])

    def test_default_sync_cli_uses_standalone_index_db(self) -> None:
        db_path = Path(self.tempdir.name) / "cli_source_only.sqlite"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "index_sync_event_index.py"),
                "--db",
                str(db_path),
                "--sports",
                "world_cup",
                "--from-date",
                "2026-06-11",
                "--thru-date",
                "2026-06-11",
                "--sources",
                "fifa_world_cup_2026_local_schedule",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        with index_event_index_db.connect(db_path) as conn:
            self.assertGreater(conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0], 0)
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("pm_canonical_event_map", tables)
            self.assertNotIn("ks_canonical_event_map", tables)
            self.assertNotIn("paired_contracts", tables)
        self.assertIn("events_upserted=", result.stdout)
        self.assertIn(str(db_path), result.stdout)

    def test_esports_source_participant_does_not_require_extra_alias_field(self) -> None:
        team = index_universe_adapters.OfficialEsportsTeam(id="team-1", name="Example Esports", code="EX")

        participant = index_event_index_sources.esports_participant("valorant", team, 0)

        self.assertEqual(participant.participant_key, "esports:valorant:open:team1")
        self.assertIn("Example Esports", participant.aliases)
        self.assertIn("EX", participant.aliases)


if __name__ == "__main__":
    unittest.main()
