from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import index_event_index_db  # noqa: E402
import index_import_curated_schedule  # noqa: E402
import index_sync_wanted_ranges  # noqa: E402
import old_export_index_wanted_ranges  # noqa: E402
import old_index_event_matching  # noqa: E402
import old_market_db  # noqa: E402


@dataclass(frozen=True)
class Wanted:
    sport_key: str
    competition_key: str
    wanted_from_date: str
    wanted_to_date: str
    pm_event_count: int
    ks_event_count: int
    total_event_count: int
    sample_events: list[dict[str, str]]
    source_export_path: str = "test.jsonl"


class ProactiveCalendarIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.tempdir.name)
        self.index_db = self.temp_path / "index.sqlite"
        self.index_conn = index_event_index_db.connect(self.index_db)
        index_event_index_db.init_db(self.index_conn)

    def tearDown(self) -> None:
        self.index_conn.close()
        self.tempdir.cleanup()

    def test_new_proactive_calendar_tables_exist(self) -> None:
        tables = {
            row["name"]
            for row in self.index_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

        self.assertIn("index_wanted_competition_ranges", tables)
        self.assertIn("index_manual_schedule_imports", tables)
        self.assertIn("index_event_evidence", tables)

    def test_sync_wanted_ranges_jsonl(self) -> None:
        path = self.temp_path / "wanted.jsonl"
        path.write_text(
            json.dumps(
                {
                    "sport_key": "tennis",
                    "competition_key": "atp",
                    "wanted_from_date": "2026-07-01",
                    "wanted_to_date": "2026-07-10",
                    "pm_event_count": 2,
                    "ks_event_count": 3,
                    "total_event_count": 5,
                    "sample_events": [{"platform": "pm", "platform_event_id": "pm-1"}],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        count = index_sync_wanted_ranges.sync_wanted_ranges(self.index_db, path)

        row = self.index_conn.execute(
            """
            SELECT sport_key, competition_key, wanted_from_date, wanted_to_date,
                   pm_event_count, ks_event_count, total_event_count
            FROM index_wanted_competition_ranges
            WHERE sport_key = 'tennis' AND competition_key = 'atp'
            """
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(dict(row), {
            "sport_key": "tennis",
            "competition_key": "atp",
            "wanted_from_date": "2026-07-01",
            "wanted_to_date": "2026-07-10",
            "pm_event_count": 2,
            "ks_event_count": 3,
            "total_event_count": 5,
        })

    def test_old_export_wanted_ranges_takes_pm_ks_union_without_writing_old(self) -> None:
        old_db = self.temp_path / "old.sqlite"
        conn = old_market_db.connect(old_db)
        old_market_db.init_db(conn)
        old_index_event_matching.ensure_old_index_tables(conn)
        now = "2026-07-06T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO old_index_platform_category_map (
                platform, platform_key_type, platform_key, sport_key, competition_key,
                mapping_status, confidence, notes, first_seen_ts, last_seen_ts
            ) VALUES
              ('pm', 'tag_slug', 'tennis', 'tennis', 'generic_tennis', 'broad', 'medium', '', ?, ?),
              ('ks', 'series_ticker', 'KXATPMATCH', 'tennis', 'atp', 'mapped', 'high', '', ?, ?)
            """,
            (now, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO pm_events (
                event_slug, event_id, title, start_date, active, closed, tag_slug, tag_id,
                raw_json, first_seen_ts, last_seen_ts
            ) VALUES ('atp-alcaraz-sinner-2026-07-08', '1', 'Carlos Alcaraz vs Jannik Sinner',
                '2026-07-08T13:00:00Z', 1, 0, 'tennis', '', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO pm_markets (
                market_id, event_slug, question, active, closed, enable_order_book,
                outcomes_json, clob_token_ids_json, raw_json, first_seen_ts, last_seen_ts
            ) VALUES ('pm-mkt-1', 'atp-alcaraz-sinner-2026-07-08', 'Carlos Alcaraz vs Jannik Sinner',
                1, 0, 1, '["Carlos Alcaraz","Jannik Sinner"]', '["a","b"]', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO ks_events (
                event_ticker, series_ticker, title, status, raw_json, first_seen_ts, last_seen_ts
            ) VALUES ('KXATPMATCH-26JUL08ALCASINN', 'KXATPMATCH', 'Alcaraz vs Sinner Winner?',
                'active', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO ks_markets (
                market_ticker, event_ticker, series_ticker, title, yes_sub_title,
                status, close_time, raw_json, first_seen_ts, last_seen_ts
            ) VALUES
              ('ks-1', 'KXATPMATCH-26JUL08ALCASINN', 'KXATPMATCH', 'Winner?', 'Carlos Alcaraz', 'open', '', '{}', ?, ?),
              ('ks-2', 'KXATPMATCH-26JUL08ALCASINN', 'KXATPMATCH', 'Winner?', 'Jannik Sinner', 'open', '', '{}', ?, ?)
            """,
            (now, now, now, now),
        )
        conn.commit()
        conn.close()
        before_mtime = old_db.stat().st_mtime_ns

        with old_export_index_wanted_ranges.readonly_connect(old_db) as readonly:
            wanted = old_export_index_wanted_ranges.aggregate_wanted_ranges(readonly)

        after_mtime = old_db.stat().st_mtime_ns
        tennis_rows = [row for row in wanted if row.sport_key == "tennis"]
        self.assertEqual(before_mtime, after_mtime)
        self.assertTrue(any(row.pm_event_count == 1 for row in tennis_rows))
        self.assertTrue(any(row.ks_event_count == 1 for row in tennis_rows))

    def test_curated_import_requires_source_url_and_writes_evidence(self) -> None:
        path = self.temp_path / "curated.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sport_key",
                    "competition_key",
                    "event_date",
                    "start_time_utc",
                    "participant_a",
                    "participant_b",
                    "source_name",
                    "source_url",
                    "source_type",
                    "source_accessed_date",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "sport_key": "basketball",
                    "competition_key": "nba",
                    "event_date": "2026-07-08",
                    "start_time_utc": "2026-07-08T23:00:00Z",
                    "participant_a": "Cleveland Cavaliers",
                    "participant_b": "Boston Celtics",
                    "source_name": "Example Sports Schedule",
                    "source_url": "https://example.com/nba-schedule",
                    "source_type": "authoritative_sports_media",
                    "source_accessed_date": "2026-07-06",
                }
            )

        seen, upserted = index_import_curated_schedule.import_file(self.index_conn, path)
        self.index_conn.commit()

        self.assertEqual((seen, upserted), (1, 1))
        self.assertEqual(self.index_conn.execute("SELECT COUNT(*) FROM canonical_events").fetchone()[0], 1)
        self.assertEqual(self.index_conn.execute("SELECT COUNT(*) FROM index_event_evidence").fetchone()[0], 1)
        evidence = self.index_conn.execute("SELECT source_type, source_url FROM index_event_evidence").fetchone()
        self.assertEqual(evidence["source_type"], "authoritative_sports_media")
        self.assertEqual(evidence["source_url"], "https://example.com/nba-schedule")

    def test_curated_import_rejects_pm_ks_or_missing_source_url(self) -> None:
        row = {
            "sport_key": "basketball",
            "competition_key": "nba",
            "event_date": "2026-07-08",
            "participant_a": "A",
            "participant_b": "B",
            "source_name": "Polymarket",
            "source_url": "",
            "source_type": "authoritative_sports_media",
            "source_accessed_date": "2026-07-06",
        }

        with self.assertRaises(ValueError):
            index_import_curated_schedule.validate_row(row, path=self.temp_path / "bad.csv", row_number=1)


if __name__ == "__main__":
    unittest.main()
