from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import market_db  # noqa: E402
import staging_collect_paired_orderbooks as paired_collect  # noqa: E402
import staging_edge_snapshot_builder as edge_builder  # noqa: E402


class PairedOrderbookCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "paired_collect.sqlite"
        self.conn = market_db.connect(self.db_path)
        market_db.init_db(self.conn)
        edge_builder.ensure_builder_indexes(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def add_pair(self) -> int:
        now = "2026-06-29T00:00:00+00:00"
        cursor = self.conn.execute(
            """
            INSERT INTO paired_contracts (
                pair_key, universe, category, match_name, event_date, canonical_event_id,
                market_type, pm_yes_outcome, ks_yes_outcome, pm_event_slug, pm_market_id,
                pm_token_id, ks_event_ticker, ks_market_ticker, match_format, schedule_source,
                safe_paired, first_seen_ts, last_seen_ts
            ) VALUES ('pair-a', 'mlb', 'baseball', 'AAA vs BBB', '2026-06-29', 'mlb:aaa-bbb',
                'winner', 'AAA', 'AAA', 'pm-event', 'pm-market',
                'pm-token', 'KS-EVENT', 'KS-MARKET', 'game', 'unit-test', 1, ?, ?)
            """,
            (now, now),
        )
        return int(cursor.lastrowid)

    def add_pm_book(self, ts_utc: str, bid: str, ask: str) -> int:
        return market_db.record_orderbook_success(
            self.conn,
            "pm",
            "pm-token",
            {
                "timestamp": "1780000000",
                "bids": [{"price": bid, "size": "10"}],
                "asks": [{"price": ask, "size": "10"}],
            },
            ts_utc,
            "/book",
            None,
            1,
        )

    def add_ks_book(self, ts_utc: str, yes_bid: str, no_bid: str) -> int:
        return market_db.record_orderbook_success(
            self.conn,
            "ks",
            "KS-MARKET",
            {
                "orderbook_fp": {
                    "yes_dollars": [[yes_bid, "8"]],
                    "no_dollars": [[no_bid, "8"]],
                }
            },
            ts_utc,
            "/markets/KS-MARKET/orderbook",
            10,
            1,
        )

    def test_collected_pair_edge_uses_exact_observation_ids_not_latest(self) -> None:
        self.add_pair()
        pair = self.conn.execute("SELECT * FROM paired_contracts WHERE pair_key = 'pair-a'").fetchone()

        exact_pm = self.add_pm_book("2026-06-29T00:00:00+00:00", "0.60", "0.40")
        exact_ks = self.add_ks_book("2026-06-29T00:00:01+00:00", "0.70", "0.50")
        newer_pm = self.add_pm_book("2026-06-29T00:00:04+00:00", "0.20", "0.80")
        self.conn.commit()

        original_collect_pair = paired_collect.collect_pair

        def fake_collect_pair(_conn, _pair, _ks_depth, _sleep_seconds=0.0):
            return exact_pm, exact_ks

        paired_collect.collect_pair = fake_collect_pair
        try:
            _pm_count, _ks_count, stats = paired_collect.collect_pairs(
                self.conn,
                [pair],
                ks_depth=10,
                sleep_seconds=0.0,
                commit_every=1,
                compute_edges=True,
                max_age_seconds=10.0,
                max_skew_seconds=5.0,
                dedupe_edges=True,
            )
        finally:
            paired_collect.collect_pair = original_collect_pair

        self.assertEqual(stats.inserted, 1)
        edge = self.conn.execute("SELECT * FROM edge_snapshots ORDER BY edge_snapshot_id DESC LIMIT 1").fetchone()
        self.assertEqual(int(edge["pm_observation_id"]), exact_pm)
        self.assertNotEqual(int(edge["pm_observation_id"]), newer_pm)
        self.assertEqual(int(edge["ks_observation_id"]), exact_ks)


if __name__ == "__main__":
    unittest.main()
