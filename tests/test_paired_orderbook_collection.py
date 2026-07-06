from __future__ import annotations

import sys
import tempfile
import unittest
import csv
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import old_market_db  # noqa: E402
import old_staging_collect_paired_orderbooks as paired_collect  # noqa: E402
import old_staging_edge_snapshot_builder as edge_builder  # noqa: E402


class PairedOrderbookCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "paired_collect.sqlite"
        self.conn = old_market_db.connect(self.db_path)
        old_market_db.init_db(self.conn)
        edge_builder.ensure_builder_indexes(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def add_pair(self, suffix: str = "a", *, safe_paired: int = 1) -> int:
        now = "2026-06-29T00:00:00+00:00"
        pm_token = "pm-token" if suffix == "a" else f"pm-token-{suffix}"
        ks_market = "KS-MARKET" if suffix == "a" else f"KS-MARKET-{suffix}"
        cursor = self.conn.execute(
            """
            INSERT INTO paired_contracts (
                pair_key, universe, category, match_name, event_date, canonical_event_id,
                market_type, pm_yes_outcome, ks_yes_outcome, pm_event_slug, pm_market_id,
                pm_token_id, ks_event_ticker, ks_market_ticker, match_format, schedule_source,
                safe_paired, first_seen_ts, last_seen_ts
            ) VALUES (?, 'mlb', 'baseball', ?, '2026-06-29', ?,
                'winner', 'AAA', 'AAA', 'pm-event', 'pm-market',
                ?, 'KS-EVENT', ?, 'game', 'unit-test', ?, ?, ?)
            """,
            (
                f"pair-{suffix}",
                f"AAA vs BBB {suffix}",
                f"mlb:aaa-bbb-{suffix}",
                pm_token,
                ks_market,
                safe_paired,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def add_pm_book(self, ts_utc: str, bid: str, ask: str) -> int:
        return old_market_db.record_orderbook_success(
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
        return old_market_db.record_orderbook_success(
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

    def default_args(self, **overrides):
        values = {
            "universes": "all",
            "max_pairs": 0,
            "interval": 5.0,
            "ks_depth": 1,
            "pair_workers": 4,
            "pair_start_interval_ms": 0,
            "request_timeout": 0.1,
            "compute_edges": True,
            "max_age_seconds": 5.0,
            "max_skew_seconds": 2.0,
            "no_edge_dedupe": False,
            "universal_output": str(Path(self.tempdir.name) / "latest_universal_snapshot.csv"),
            "output": str(Path(self.tempdir.name) / "latest_edges.csv"),
            "alert_output": str(Path(self.tempdir.name) / "latest_alerts.csv"),
            "alert_dir": str(Path(self.tempdir.name) / "alerts"),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def fake_success_fetch(self, *, venue, instrument_id, request_path, params, depth, request_timeout):
        if venue == "pm":
            payload = {
                "timestamp": "1780000000",
                "bids": [{"price": "0.55", "size": "10"}],
                "asks": [{"price": "0.40", "size": "10"}],
            }
        else:
            payload = {
                "orderbook_fp": {
                    "yes_dollars": [["0.70", "8"]],
                    "no_dollars": [["0.50", "8"]],
                }
            }
        return paired_collect.FetchResult(
            venue,
            instrument_id,
            payload,
            old_market_db.utc_now(),
            request_path,
            depth,
            1,
        )

    def run_window_with_fetch(self, fetcher, **arg_overrides):
        original_fetch = paired_collect.fetch_orderbook_request
        paired_collect.fetch_orderbook_request = fetcher
        try:
            return paired_collect.collect_snapshot_window(self.conn, self.default_args(**arg_overrides))
        finally:
            paired_collect.fetch_orderbook_request = original_fetch

    def test_max_pairs_zero_selects_all_safe_pairs(self) -> None:
        self.add_pair("a")
        self.add_pair("b")
        self.add_pair("c")
        self.add_pair("unsafe", safe_paired=0)
        self.conn.commit()

        pairs = paired_collect.selected_pairs(self.conn, {"all"}, 0)

        self.assertEqual(len(pairs), 3)

    def test_full_coverage_window_attempts_all_safe_pairs_by_default(self) -> None:
        self.add_pair("a")
        self.add_pair("b")
        self.add_pair("c")
        self.conn.commit()

        stats = self.run_window_with_fetch(self.fake_success_fetch)

        self.assertEqual(stats.safe_pair_count, 3)
        self.assertEqual(stats.attempted_count, 3)
        self.assertEqual(stats.missed_count, 0)
        self.assertEqual(stats.edge_inserted_count, 3)
        window = self.conn.execute("SELECT * FROM snapshot_windows WHERE snapshot_window_id = ?", (stats.snapshot_window_id,)).fetchone()
        self.assertEqual(window["safe_pair_count"], 3)
        self.assertEqual(window["attempted_count"], 3)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM snapshot_pair_results WHERE snapshot_window_id = ?",
                (stats.snapshot_window_id,),
            ).fetchone()[0],
            3,
        )

    def test_window_edge_uses_same_window_observation_ids(self) -> None:
        self.add_pair("a")
        self.conn.commit()

        stats = self.run_window_with_fetch(self.fake_success_fetch)

        result = self.conn.execute(
            "SELECT * FROM snapshot_pair_results WHERE snapshot_window_id = ?",
            (stats.snapshot_window_id,),
        ).fetchone()
        edge = self.conn.execute(
            "SELECT * FROM edge_snapshots WHERE edge_snapshot_id = ?",
            (result["edge_snapshot_id"],),
        ).fetchone()
        self.assertEqual(edge["snapshot_window_id"], stats.snapshot_window_id)
        self.assertEqual(edge["pm_observation_id"], result["pm_observation_id"])
        self.assertEqual(edge["ks_observation_id"], result["ks_observation_id"])

    def test_timeout_error_writes_pair_result_reason(self) -> None:
        self.add_pair("a")
        self.conn.commit()

        def fake_timeout_fetch(*, venue, instrument_id, request_path, params, depth, request_timeout):
            if venue == "pm":
                return paired_collect.FetchResult(
                    venue,
                    instrument_id,
                    None,
                    old_market_db.utc_now(),
                    request_path,
                    depth,
                    3000,
                    "request timed out",
                )
            return self.fake_success_fetch(
                venue=venue,
                instrument_id=instrument_id,
                request_path=request_path,
                params=params,
                depth=depth,
                request_timeout=request_timeout,
            )

        stats = self.run_window_with_fetch(fake_timeout_fetch)

        result = self.conn.execute(
            "SELECT * FROM snapshot_pair_results WHERE snapshot_window_id = ?",
            (stats.snapshot_window_id,),
        ).fetchone()
        self.assertEqual(result["status"], "timeout")
        self.assertIn("pm_fetch_failed:request timed out", result["reason"])
        self.assertEqual(stats.error_count, 1)

    def test_snapshot_csv_outputs_include_every_attempted_pair(self) -> None:
        self.add_pair("a")
        self.add_pair("b")
        self.add_pair("c")
        self.conn.commit()
        args = self.default_args()

        stats = self.run_window_with_fetch(self.fake_success_fetch)
        rows_written, _alerts_written = paired_collect.write_snapshot_csv_outputs(self.conn, stats, args)

        with Path(args.universal_output).open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows_written, 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["snapshot_window_id"] for row in rows}, {str(stats.snapshot_window_id)})
        self.assertEqual({row["pair_status"] for row in rows}, {"ok"})
        self.assertEqual({row["attempted_count"] for row in rows}, {"3"})

    def test_latest_edges_csv_contains_only_positive_edges(self) -> None:
        self.add_pair("a")
        self.conn.commit()
        args = self.default_args()

        stats = self.run_window_with_fetch(self.fake_success_fetch)
        universal_rows_written, positive_rows_written = paired_collect.write_snapshot_csv_outputs(self.conn, stats, args)

        with Path(args.universal_output).open(newline="") as handle:
            universal_rows = list(csv.DictReader(handle))
        with Path(args.output).open(newline="") as handle:
            edge_rows = list(csv.DictReader(handle))
        with Path(args.alert_output).open(newline="") as handle:
            alert_rows = list(csv.DictReader(handle))

        self.assertEqual(universal_rows_written, 1)
        self.assertEqual(positive_rows_written, 1)
        self.assertEqual(len(universal_rows), 1)
        self.assertEqual(len(edge_rows), 1)
        self.assertEqual(len(alert_rows), 1)
        self.assertEqual(edge_rows[0]["alert"], "ALERT")
        self.assertGreater(float(edge_rows[0]["net_edge"]), 0)

    def test_latest_edges_csv_has_only_header_when_no_positive_edges(self) -> None:
        self.add_pair("a")
        self.conn.commit()
        args = self.default_args()

        def fake_no_edge_fetch(*, venue, instrument_id, request_path, params, depth, request_timeout):
            if venue == "pm":
                payload = {
                    "timestamp": "1780000000",
                    "bids": [{"price": "0.40", "size": "10"}],
                    "asks": [{"price": "0.60", "size": "10"}],
                }
            else:
                payload = {
                    "orderbook_fp": {
                        "yes_dollars": [["0.40", "8"]],
                        "no_dollars": [["0.40", "8"]],
                    }
                }
            return paired_collect.FetchResult(
                venue,
                instrument_id,
                payload,
                old_market_db.utc_now(),
                request_path,
                depth,
                1,
            )

        stats = self.run_window_with_fetch(fake_no_edge_fetch)
        universal_rows_written, positive_rows_written = paired_collect.write_snapshot_csv_outputs(self.conn, stats, args)

        with Path(args.universal_output).open(newline="") as handle:
            universal_rows = list(csv.DictReader(handle))
        with Path(args.output).open(newline="") as handle:
            edge_rows = list(csv.DictReader(handle))

        self.assertEqual(universal_rows_written, 1)
        self.assertEqual(positive_rows_written, 0)
        self.assertEqual(len(universal_rows), 1)
        self.assertEqual(edge_rows, [])

    def test_late_window_is_not_marked_ok(self) -> None:
        self.add_pair("a")
        self.conn.commit()

        stats = self.run_window_with_fetch(self.fake_success_fetch, interval=0.000001)

        self.assertEqual(stats.status, "late")
        self.assertIn("elapsed=", stats.reason)

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
