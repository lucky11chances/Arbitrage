from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import index_event_index_core  # noqa: E402
import index_event_index_db  # noqa: E402
import old_index_event_matching  # noqa: E402
import old_market_db  # noqa: E402
import old_refresh_index_event_maps  # noqa: E402
import old_sync_index_snapshot  # noqa: E402
import index_sync_backfill_from_old_ranges  # noqa: E402


class OldIndexEventMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.index_db = Path(self.tempdir.name) / "index.sqlite"
        self.old_db = Path(self.tempdir.name) / "old.sqlite"
        with index_event_index_db.connect(self.index_db) as conn:
            index_event_index_db.init_db(conn)
            self.add_index_event(conn, "official_schedule", "official_api", "2026-09-01")
            self.add_platform_maps(conn)
            conn.commit()
        with old_market_db.connect(self.old_db) as conn:
            old_market_db.init_db(conn)
            conn.commit()
        old_sync_index_snapshot.sync_snapshot(self.index_db, self.old_db)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def add_index_event(self, conn: sqlite3.Connection, source_key: str, source_type: str, event_date: str) -> None:
        event = index_event_index_core.IndexedEvent(
            canonical_event_id=index_event_index_core.canonical_event_id_from_source("nfl", source_key, f"{event_date}-cle-arl"),
            category_key="football",
            universe="nfl",
            sport_key="football",
            competition_key="nfl",
            season="2026",
            event_date=event_date,
            source_local_date=event_date,
            start_time_utc=f"{event_date}T17:00:00Z",
            market_type="game_winner",
            competition_gender="men",
            match_name="Cleveland Browns @ Arlington Grays",
            event_name="Cleveland Browns @ Arlington Grays",
            source_key=source_key,
            source_event_id=f"{event_date}-cle-arl",
            source_type=source_type,
            source_confidence="high",
            source_url="https://example.test/official-schedule",
            participants=(
                index_event_index_core.IndexedParticipant(
                    "football:nfl:men:clevelandbrowns",
                    "Cleveland Browns",
                    "cle",
                    ("Cleveland Browns", "Cleveland", "Browns", "CLE"),
                    "away",
                    0,
                ),
                index_event_index_core.IndexedParticipant(
                    "football:nfl:men:arlingtongrays",
                    "Arlington Grays",
                    "arl",
                    ("Arlington Grays", "Arlington", "Grays", "ARL"),
                    "home",
                    1,
                ),
            ),
            raw_payload={"event_date": event_date, "source": source_key},
        )
        index_event_index_db.upsert_indexed_event(conn, event)

    def add_platform_maps(self, conn: sqlite3.Connection) -> None:
        now = index_event_index_db.utc_now()
        rows = (
            ("pm", "tag_slug", "nfl", "football", "nfl", "mapped", "high", ""),
            ("ks", "series_ticker", "KXNFLGAME", "football", "nfl", "mapped", "high", ""),
        )
        conn.executemany(
            """
            INSERT INTO index_platform_category_map (
                platform, platform_key_type, platform_key, sport_key, competition_key,
                mapping_status, confidence, notes, first_seen_ts, last_seen_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*row, now, now) for row in rows],
        )

    def add_pm_event(self, slug: str, title: str, outcomes: list[str]) -> None:
        now = old_market_db.utc_now()
        with old_market_db.connect(self.old_db) as conn:
            conn.execute(
                """
                INSERT INTO pm_events (
                    event_slug, event_id, title, start_date, end_date, active, closed,
                    tag_slug, tag_id, raw_json, first_seen_ts, last_seen_ts
                ) VALUES (?, ?, ?, '', '', 1, 0, 'nfl', '', '{}', ?, ?)
                """,
                (slug, slug, title, now, now),
            )
            conn.execute(
                """
                INSERT INTO pm_markets (
                    market_id, event_slug, question, market_slug, condition_id, active,
                    closed, enable_order_book, outcomes_json, clob_token_ids_json,
                    raw_json, first_seen_ts, last_seen_ts
                ) VALUES (?, ?, ?, ?, '', 1, 0, 1, ?, ?, '{}', ?, ?)
                """,
                (
                    f"{slug}-market",
                    slug,
                    title,
                    f"{slug}-market",
                    old_market_db.json_text(outcomes),
                    old_market_db.json_text(["token-a", "token-b"]),
                    now,
                    now,
                ),
            )
            conn.commit()

    def add_ks_event(self, event_ticker: str, title: str, outcomes: list[str]) -> None:
        now = old_market_db.utc_now()
        with old_market_db.connect(self.old_db) as conn:
            conn.execute(
                """
                INSERT INTO ks_events (
                    event_ticker, series_ticker, title, status, raw_json, first_seen_ts, last_seen_ts
                ) VALUES (?, 'KXNFLGAME', ?, 'open', '{}', ?, ?)
                """,
                (event_ticker, title, now, now),
            )
            for index, outcome in enumerate(outcomes):
                conn.execute(
                    """
                    INSERT INTO ks_markets (
                        market_ticker, event_ticker, series_ticker, title, yes_sub_title,
                        status, close_time, raw_json, first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, 'KXNFLGAME', ?, ?, 'open', '', '{}', ?, ?)
                    """,
                    (f"{event_ticker}-{index}", event_ticker, title, outcome, now, now),
                )
            conn.commit()

    def test_snapshot_copies_index_events_and_participants_to_old_db(self) -> None:
        with old_market_db.connect(self.old_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM old_index_canonical_participants").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM old_index_platform_category_map").fetchone()[0], 2)

    def test_pm_and_ks_map_order_insensitive_place_optional_names(self) -> None:
        self.add_pm_event(
            "nfl-cle-arl-2026-09-01",
            "Cleveland vs Arlington",
            ["Cleveland", "Arlington"],
        )
        self.add_ks_event(
            "KXNFLGAME-26SEP011700ARLCLE",
            "Arlington Grays vs Cleveland Browns Winner?",
            ["Browns", "Grays"],
        )

        old_refresh_index_event_maps.refresh(self.old_db, "pm")
        old_refresh_index_event_maps.refresh(self.old_db, "ks")

        with old_market_db.connect(self.old_db) as conn:
            pm_row = conn.execute("SELECT canonical_event_id FROM pm_canonical_event_map").fetchone()
            ks_row = conn.execute("SELECT canonical_event_id FROM ks_canonical_event_map").fetchone()
            self.assertIsNotNone(pm_row)
            self.assertIsNotNone(ks_row)
            self.assertEqual(pm_row["canonical_event_id"], ks_row["canonical_event_id"])

    def test_same_teams_different_date_does_not_map(self) -> None:
        self.add_pm_event(
            "nfl-cle-arl-2026-09-02",
            "Arlington Grays vs Cleveland Browns",
            ["Arlington Grays", "Cleveland Browns"],
        )

        old_refresh_index_event_maps.refresh(self.old_db, "pm")

        with old_market_db.connect(self.old_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pm_canonical_event_map").fetchone()[0], 0)
            reason = conn.execute(
                "SELECT reason FROM old_index_event_mapping_diagnostics WHERE platform = 'pm'"
            ).fetchone()["reason"]
            self.assertEqual(reason, "no_index_date")

    def test_same_date_wrong_participant_set_does_not_map(self) -> None:
        self.add_pm_event(
            "nfl-cle-bal-2026-09-01",
            "Cleveland Browns vs Baltimore Ravens",
            ["Cleveland Browns", "Baltimore Ravens"],
        )

        old_refresh_index_event_maps.refresh(self.old_db, "pm")

        with old_market_db.connect(self.old_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pm_canonical_event_map").fetchone()[0], 0)
            reason = conn.execute(
                "SELECT reason FROM old_index_event_mapping_diagnostics WHERE platform = 'pm'"
            ).fetchone()["reason"]
            self.assertEqual(reason, "participant_mismatch_same_date")

    def test_missing_competition_and_time_conflict_have_specific_reasons(self) -> None:
        with old_market_db.connect(self.old_db) as conn:
            index_events = old_index_event_matching.load_index_events_from_old(conn)
        missing_competition = old_index_event_matching.PlatformCandidate(
            platform="pm",
            platform_event_id="mlb-example-2026-09-01",
            platform_market_id="market",
            sport_key="baseball",
            competition_key="mlb",
            event_date="2026-09-01",
            start_time_utc="",
            market_type="game_winner",
            participant_names=("Cleveland", "Arlington"),
            source_name="Cleveland vs Arlington",
            context={},
        )
        time_conflict = old_index_event_matching.PlatformCandidate(
            platform="pm",
            platform_event_id="nfl-cle-arl-2026-09-01",
            platform_market_id="market",
            sport_key="football",
            competition_key="nfl",
            event_date="2026-09-01",
            start_time_utc="2026-09-01T19:00:00Z",
            market_type="game_winner",
            participant_names=("Cleveland", "Arlington"),
            source_name="Cleveland vs Arlington",
            context={},
        )

        self.assertEqual(
            old_index_event_matching.match_candidate(missing_competition, index_events).reason,
            "no_index_competition",
        )
        self.assertEqual(
            old_index_event_matching.match_candidate(time_conflict, index_events).reason,
            "time_mismatch",
        )

    def test_ks_mapping_does_not_need_pm_data(self) -> None:
        self.add_ks_event(
            "KXNFLGAME-26SEP011700ARLCLE",
            "Cleveland Browns vs Arlington Grays Winner?",
            ["Grays", "Browns"],
        )

        old_refresh_index_event_maps.refresh(self.old_db, "ks")

        with old_market_db.connect(self.old_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pm_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ks_canonical_event_map").fetchone()[0], 1)

    def test_source_purity_audit_rejects_pm_or_ks_sources(self) -> None:
        bad_index = Path(self.tempdir.name) / "bad_index.sqlite"
        bad_old = Path(self.tempdir.name) / "bad_old.sqlite"
        with index_event_index_db.connect(bad_index) as conn:
            index_event_index_db.init_db(conn)
            self.add_index_event(conn, "polymarket_fake_source", "pm_copy", "2026-09-01")
            self.add_platform_maps(conn)
            conn.commit()
        with old_market_db.connect(bad_old) as conn:
            old_market_db.init_db(conn)
            conn.commit()
        with self.assertRaises(RuntimeError):
            old_sync_index_snapshot.sync_snapshot(bad_index, bad_old)

    def test_backfill_windows_read_old_diagnostics_and_enabled_sources(self) -> None:
        now = old_market_db.utc_now()
        with old_market_db.connect(self.old_db) as conn:
            old_index_event_matching.ensure_old_index_tables(conn)
            rows = (
                ("pm", "baseball", "mlb", "2026-05-05", "pm-mlb-1"),
                ("ks", "baseball", "mlb", "2026-05-07", "ks-mlb-1"),
                ("pm", "football", "nfl", "2026-09-01", "pm-nfl-1"),
            )
            conn.executemany(
                """
                INSERT INTO old_index_event_mapping_diagnostics (
                    ts_utc, platform, sport_key, competition_key, status, reason,
                    eligible, event_date, market_type, platform_event_id
                ) VALUES (?, ?, ?, ?, 'unmapped', 'no_index_date', 1, ?, 'game_winner', ?)
                """,
                [(now, *row) for row in rows],
            )
            conn.commit()
        with index_sync_backfill_from_old_ranges.readonly_old_connect(self.old_db) as conn:
            windows = index_sync_backfill_from_old_ranges.candidate_windows_from_old(
                conn,
                padding_days=0,
            )

        by_competition = {(window.sport_key, window.competition_key): window for window in windows}
        self.assertEqual(by_competition[("baseball", "mlb")].start_date, "2026-05-05")
        self.assertEqual(by_competition[("baseball", "mlb")].end_date, "2026-05-07")
        self.assertEqual(by_competition[("baseball", "mlb")].source_key, "mlb_stats_api")
        self.assertEqual(by_competition[("football", "nfl")].source_key, "")


if __name__ == "__main__":
    unittest.main()
