from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import old_market_db  # noqa: E402
import old_staging_paper_trade_simulator as simulator  # noqa: E402
import old_staging_week3_daily_report as daily_report  # noqa: E402


class Week3DailyReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "week3_daily.sqlite"
        self.conn = old_market_db.connect(self.db_path)
        old_market_db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def add_pair(self, pm_token_id: str = "pm-token", ks_market_ticker: str = "KS-MARKET") -> int:
        now = "2026-06-29T00:00:00+00:00"
        cursor = self.conn.execute(
            """
            INSERT INTO paired_contracts (
                pair_key, universe, category, match_name, event_date, canonical_event_id,
                market_type, pm_yes_outcome, ks_yes_outcome, pm_event_slug, pm_market_id,
                pm_token_id, ks_event_ticker, ks_market_ticker, match_format, schedule_source,
                safe_paired, first_seen_ts, last_seen_ts
            ) VALUES (?, 'mlb', 'baseball', 'AAA vs BBB', '2026-06-29', 'mlb:aaa-bbb',
                'winner', 'AAA', 'AAA', 'pm-event', 'pm-market',
                ?, 'KS-EVENT', ?, 'game', 'unit-test', 1, ?, ?)
            """,
            (f"{pm_token_id}:{ks_market_ticker}", pm_token_id, ks_market_ticker, now, now),
        )
        return int(cursor.lastrowid)

    def add_pm_observation(self, token_id: str, ts_utc: str) -> int:
        return old_market_db.record_orderbook_success(
            self.conn,
            "pm",
            token_id,
            {
                "timestamp": "1780000000",
                "bids": [{"price": "0.60", "size": "100"}],
                "asks": [{"price": "0.40", "size": "100"}],
            },
            ts_utc,
            "/book",
            None,
            1,
        )

    def add_ks_observation(self, ticker: str, ts_utc: str) -> int:
        return old_market_db.record_orderbook_success(
            self.conn,
            "ks",
            ticker,
            {
                "orderbook_fp": {
                    "yes_dollars": [["0.70", "100"]],
                    "no_dollars": [["0.50", "100"]],
                }
            },
            ts_utc,
            f"/markets/{ticker}/orderbook",
            10,
            1,
        )

    def add_edge_snapshot(
        self,
        pair_id: int,
        edge_ts_utc: str,
        pm_obs_id: int,
        ks_obs_id: int,
        *,
        net_edge: Decimal = Decimal("0.03"),
    ) -> int:
        pm_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (pm_obs_id,)).fetchone()
        ks_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (ks_obs_id,)).fetchone()
        cursor = self.conn.execute(
            """
            INSERT INTO edge_snapshots (
                paired_contract_id, ts_utc, pm_observation_id, ks_observation_id,
                pm_bid_scaled, pm_ask_scaled, pm_bid_size_scaled, pm_ask_size_scaled,
                ks_bid_scaled, ks_ask_scaled, ks_bid_size_scaled, ks_ask_size_scaled,
                best_leg, gross_cost_scaled, net_edge_scaled, best_leg_bbo_size_scaled,
                net_profit_at_bbo_scaled, alert, alert_reason, book_age_seconds,
                snapshot_skew_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, 'ALERT', 'net_edge_positive', 0, 0)
            """,
            (
                pair_id,
                edge_ts_utc,
                pm_obs_id,
                ks_obs_id,
                pm_obs["best_bid_scaled"],
                pm_obs["best_ask_scaled"],
                pm_obs["best_bid_size_scaled"],
                pm_obs["best_ask_size_scaled"],
                ks_obs["best_bid_scaled"],
                ks_obs["best_ask_scaled"],
                ks_obs["best_bid_size_scaled"],
                ks_obs["best_ask_size_scaled"],
                simulator.LEG_PM_YES_KS_NO,
                old_market_db.to_scaled(Decimal("0.70")),
                old_market_db.to_scaled(net_edge),
                old_market_db.to_scaled(Decimal("100")),
                old_market_db.to_scaled(net_edge * Decimal("100")),
            ),
        )
        return int(cursor.lastrowid)

    def config(self) -> daily_report.DailyReportConfig:
        return daily_report.DailyReportConfig(
            start_ts_utc="2026-06-29T00:00:00+00:00",
            end_ts_utc="2026-06-29T00:10:00+00:00",
            min_net_edge=Decimal("0.02"),
            notional=Decimal("1000"),
            max_notional=Decimal("1000"),
            latencies=[5, 15, 30],
            window_gap_seconds=60,
            sanity_edge=Decimal("0.05"),
        )

    def test_daily_report_dedupes_continuous_opportunity_windows(self) -> None:
        pair_id = self.add_pair()
        for ts in [
            "2026-06-29T00:00:00+00:00",
            "2026-06-29T00:00:10+00:00",
            "2026-06-29T00:00:55+00:00",
            "2026-06-29T00:02:10+00:00",
            "2026-06-29T00:03:00+00:00",
        ]:
            self.add_pm_observation("pm-token", ts)
            self.add_ks_observation("KS-MARKET", ts)
        obs = self.conn.execute(
            "SELECT observation_id, venue, collected_ts_utc FROM orderbook_observations ORDER BY observation_id"
        ).fetchall()
        by_ts_venue = {(row["collected_ts_utc"], row["venue"]): int(row["observation_id"]) for row in obs}
        for edge_ts in [
            "2026-06-29T00:00:02+00:00",
            "2026-06-29T00:00:12+00:00",
            "2026-06-29T00:00:55+00:00",
            "2026-06-29T00:02:10+00:00",
        ]:
            base_ts = "2026-06-29T00:02:10+00:00" if edge_ts == "2026-06-29T00:02:10+00:00" else "2026-06-29T00:00:00+00:00"
            self.add_edge_snapshot(
                pair_id,
                edge_ts,
                by_ts_venue[(base_ts, "pm")],
                by_ts_venue[(base_ts, "ks")],
            )
        self.conn.commit()

        report = daily_report.build_daily_report(self.conn, self.config())

        self.assertEqual(len(report.candidates), 4)
        self.assertEqual(len(report.opportunity_windows), 2)
        self.assertEqual(len(report.trade_rows), 6)
        all_latencies = {
            int(row["latency_seconds"])
            for row in report.summary_rows
            if row["view"] == daily_report.VIEW_CLEAN and row["universe"] == "ALL"
        }
        self.assertEqual(all_latencies, {5, 15, 30})
        clean_all_5s = next(
            row
            for row in report.summary_rows
            if row["view"] == daily_report.VIEW_CLEAN
            and row["universe"] == "ALL"
            and int(row["latency_seconds"]) == 5
        )
        self.assertEqual(clean_all_5s["trade_count"], 2)
        self.assertIn("total_realized_profit", clean_all_5s)
        self.assertIn("estimated_30d_profit", clean_all_5s)

    def test_daily_report_marks_outliers_separately_and_writes_csvs(self) -> None:
        pair_id = self.add_pair()
        for ts in [
            "2026-06-29T00:00:00+00:00",
            "2026-06-29T00:00:40+00:00",
            "2026-06-29T00:02:00+00:00",
            "2026-06-29T00:02:40+00:00",
        ]:
            self.add_pm_observation("pm-token", ts)
            self.add_ks_observation("KS-MARKET", ts)
        obs = self.conn.execute(
            "SELECT observation_id, venue, collected_ts_utc FROM orderbook_observations ORDER BY observation_id"
        ).fetchall()
        by_ts_venue = {(row["collected_ts_utc"], row["venue"]): int(row["observation_id"]) for row in obs}
        self.add_edge_snapshot(
            pair_id,
            "2026-06-29T00:00:02+00:00",
            by_ts_venue[("2026-06-29T00:00:00+00:00", "pm")],
            by_ts_venue[("2026-06-29T00:00:00+00:00", "ks")],
            net_edge=Decimal("0.03"),
        )
        self.add_edge_snapshot(
            pair_id,
            "2026-06-29T00:02:00+00:00",
            by_ts_venue[("2026-06-29T00:02:00+00:00", "pm")],
            by_ts_venue[("2026-06-29T00:02:00+00:00", "ks")],
            net_edge=Decimal("0.06"),
        )
        self.conn.commit()

        report = daily_report.build_daily_report(self.conn, self.config())

        outlier_rows = [row for row in report.summary_rows if row["view"] == daily_report.VIEW_OUTLIER]
        self.assertTrue(outlier_rows)
        outlier_count = next(
            row for row in report.report_rows if row["section"] == "counts" and row["metric"] == "sanity_outlier_windows"
        )
        self.assertEqual(outlier_count["value"], 1)

        trades_path = Path(self.tempdir.name) / "daily_trades.csv"
        summary_path = Path(self.tempdir.name) / "daily_summary.csv"
        report_path = Path(self.tempdir.name) / "daily_report.csv"
        daily_report.write_csv(report.trade_rows, simulator.TRADE_FIELDS, trades_path)
        daily_report.write_csv(report.summary_rows, daily_report.SUMMARY_FIELDS, summary_path)
        daily_report.write_csv(report.report_rows, daily_report.REPORT_FIELDS, report_path)

        with summary_path.open(newline="") as handle:
            summary_csv_rows = list(csv.DictReader(handle))
        with report_path.open(newline="") as handle:
            report_csv_rows = list(csv.DictReader(handle))

        self.assertIn("total_realized_profit", summary_csv_rows[0])
        self.assertIn("estimated_30d_profit", summary_csv_rows[0])
        self.assertTrue(any(row["section"] == "best_scenario" for row in report_csv_rows))
        self.assertTrue(any(row["section"] == "worst_scenario" for row in report_csv_rows))


if __name__ == "__main__":
    unittest.main()
