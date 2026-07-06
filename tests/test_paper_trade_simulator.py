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
import old_staging_paper_trade_simulator as sim  # noqa: E402


class PaperTradeSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "paper_trade.sqlite"
        self.conn = old_market_db.connect(self.db_path)
        old_market_db.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def add_pair(self, pm_token_id: str = "pm-token", ks_market_ticker: str = "KS-MARKET-AAA") -> int:
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
                ?, 'KS-EVENT-AAA', ?, 'game', 'unit-test', 1, ?, ?)
            """,
            (f"{pm_token_id}:{ks_market_ticker}", pm_token_id, ks_market_ticker, now, now),
        )
        return int(cursor.lastrowid)

    def add_pm_observation(
        self,
        token_id: str,
        ts_utc: str,
        *,
        bids: list[dict[str, str]] | None = None,
        asks: list[dict[str, str]] | None = None,
    ) -> int:
        return old_market_db.record_orderbook_success(
            self.conn,
            "pm",
            token_id,
            {
                "timestamp": "1780000000",
                "bids": bids if bids is not None else [{"price": "0.60", "size": "100"}],
                "asks": asks if asks is not None else [{"price": "0.40", "size": "100"}],
            },
            ts_utc,
            "/book",
            None,
            1,
        )

    def add_ks_observation(
        self,
        ticker: str,
        ts_utc: str,
        *,
        yes_bids: list[list[str]] | None = None,
        no_bids: list[list[str]] | None = None,
    ) -> int:
        return old_market_db.record_orderbook_success(
            self.conn,
            "ks",
            ticker,
            {
                "orderbook_fp": {
                    "yes_dollars": yes_bids if yes_bids is not None else [["0.70", "100"]],
                    "no_dollars": no_bids if no_bids is not None else [["0.50", "100"]],
                }
            },
            ts_utc,
            f"/markets/{ticker}/orderbook",
            10,
            1,
        )

    def add_edge_snapshot(self, pair_id: int, pm_obs_id: int, ks_obs_id: int, best_leg: str) -> int:
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
            ) VALUES (?, '2026-06-29T00:00:02+00:00', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, 700000, 30000, 100000000, 3000000, 'ALERT', 'net_edge_positive', 0, 0)
            """,
            (
                pair_id,
                pm_obs_id,
                ks_obs_id,
                pm_obs["best_bid_scaled"] or 1,
                pm_obs["best_ask_scaled"] or 1,
                pm_obs["best_bid_size_scaled"] if pm_obs["best_bid_size_scaled"] is not None else 0,
                pm_obs["best_ask_size_scaled"] if pm_obs["best_ask_size_scaled"] is not None else 0,
                ks_obs["best_bid_scaled"] or 1,
                ks_obs["best_ask_scaled"] or 1,
                ks_obs["best_bid_size_scaled"] if ks_obs["best_bid_size_scaled"] is not None else 0,
                ks_obs["best_ask_size_scaled"] if ks_obs["best_ask_size_scaled"] is not None else 0,
                best_leg,
            ),
        )
        return int(cursor.lastrowid)

    def candidate_rows(self, min_net_edge: Decimal = Decimal("0.02")):
        return sim.load_alert_candidates(self.conn, min_net_edge)

    def test_no_slippage_bbo_min_size_fill(self) -> None:
        pm_quote = sim.BboQuote("pm_buy_yes", "ask", Decimal("0.40"), Decimal("0.40"), Decimal("10"))
        ks_quote = sim.BboQuote("ks_buy_no", "yes_bid", Decimal("0.70"), Decimal("0.30"), Decimal("5"))

        fill = sim.bbo_capped_fill(pm_quote, ks_quote, Decimal("100"))

        self.assertEqual(fill.shares, Decimal("5"))
        self.assertEqual(fill.fill_notional, Decimal("3.50"))
        self.assertEqual(fill.avg_cost, Decimal("0.70"))

    def test_notional_is_cap_and_deeper_levels_are_not_used(self) -> None:
        pm_obs_id = self.add_pm_observation(
            "pm-depth-token",
            "2026-06-29T00:00:00+00:00",
            asks=[
                {"price": "0.40", "size": "10"},
                {"price": "0.41", "size": "1000"},
            ],
        )
        ks_obs_id = self.add_ks_observation(
            "KS-DEPTH",
            "2026-06-29T00:00:00+00:00",
            yes_bids=[
                ["0.70", "5"],
                ["0.69", "1000"],
            ],
        )

        pm_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (pm_obs_id,)).fetchone()
        ks_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (ks_obs_id,)).fetchone()
        pm_quote, pm_reason, _pm_warning = sim.quote_for_action(self.conn, pm_obs, "pm_buy_yes", "t0_pm")
        ks_quote, ks_reason, _ks_warning = sim.quote_for_action(self.conn, ks_obs, "ks_buy_no", "t0_ks")
        self.assertEqual(pm_reason, "")
        self.assertEqual(ks_reason, "")

        fill = sim.bbo_capped_fill(pm_quote, ks_quote, Decimal("100"))

        self.assertEqual(fill.shares, Decimal("5"))
        self.assertEqual(fill.fill_notional, Decimal("3.50"))

    def test_pm_yes_ks_no_direction(self) -> None:
        pm_obs_id = self.add_pm_observation(
            "pm-direction-a",
            "2026-06-29T00:00:00+00:00",
            asks=[{"price": "0.25", "size": "11"}],
        )
        ks_obs_id = self.add_ks_observation(
            "KS-DIRECTION-A",
            "2026-06-29T00:00:00+00:00",
            yes_bids=[["0.70", "7"]],
        )
        pm_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (pm_obs_id,)).fetchone()
        ks_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (ks_obs_id,)).fetchone()

        pm_action, ks_action = sim.leg_actions(sim.LEG_PM_YES_KS_NO)
        pm_quote, _pm_reason, _pm_warning = sim.quote_for_action(self.conn, pm_obs, pm_action, "t0_pm")
        ks_quote, _ks_reason, _ks_warning = sim.quote_for_action(self.conn, ks_obs, ks_action, "t0_ks")

        self.assertEqual(pm_quote.price, Decimal("0.25"))
        self.assertEqual(pm_quote.size, Decimal("11"))
        self.assertEqual(ks_quote.price, Decimal("0.30"))
        self.assertEqual(ks_quote.size, Decimal("7"))

    def test_pm_no_ks_yes_direction(self) -> None:
        pm_obs_id = self.add_pm_observation(
            "pm-direction-b",
            "2026-06-29T00:00:00+00:00",
            bids=[{"price": "0.80", "size": "9"}],
        )
        ks_obs_id = self.add_ks_observation(
            "KS-DIRECTION-B",
            "2026-06-29T00:00:00+00:00",
            no_bids=[["0.40", "13"]],
        )
        pm_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (pm_obs_id,)).fetchone()
        ks_obs = self.conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (ks_obs_id,)).fetchone()

        pm_action, ks_action = sim.leg_actions(sim.LEG_PM_NO_KS_YES)
        pm_quote, _pm_reason, _pm_warning = sim.quote_for_action(self.conn, pm_obs, pm_action, "t0_pm")
        ks_quote, _ks_reason, _ks_warning = sim.quote_for_action(self.conn, ks_obs, ks_action, "t0_ks")

        self.assertEqual(pm_quote.price, Decimal("0.20"))
        self.assertEqual(pm_quote.size, Decimal("9"))
        self.assertEqual(ks_quote.price, Decimal("0.60"))
        self.assertEqual(ks_quote.size, Decimal("13"))

    def test_delayed_snapshot_selection_uses_nearest_after_target(self) -> None:
        token_id = "pm-delay-token"
        self.add_pm_observation(token_id, "2026-06-29T00:00:00+00:00")
        self.add_pm_observation(token_id, "2026-06-29T00:00:04+00:00")
        expected_obs_id = self.add_pm_observation(token_id, "2026-06-29T00:00:07+00:00")
        self.add_pm_observation(token_id, "2026-06-29T00:00:20+00:00")

        selected = sim.next_observation(
            self.conn,
            venue="pm",
            instrument_id=token_id,
            target_ts_utc="2026-06-29T00:00:05+00:00",
        )

        self.assertIsNotNone(selected)
        self.assertEqual(int(selected["observation_id"]), expected_obs_id)

    def test_missing_delayed_snapshot_writes_reason(self) -> None:
        pair_id = self.add_pair()
        pm_obs_id = self.add_pm_observation(
            "pm-token",
            "2026-06-29T00:00:00+00:00",
            asks=[{"price": "0.40", "size": "10"}],
        )
        ks_obs_id = self.add_ks_observation(
            "KS-MARKET-AAA",
            "2026-06-29T00:00:01+00:00",
            yes_bids=[["0.70", "5"]],
        )
        self.add_edge_snapshot(pair_id, pm_obs_id, ks_obs_id, sim.LEG_PM_YES_KS_NO)
        self.conn.commit()

        rows = sim.simulate(
            self.conn,
            min_net_edge=Decimal("0.02"),
            notional=Decimal("100"),
            max_notional=Decimal("100"),
            latencies=[5],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "missing_delayed_pm_ks_snapshot")
        self.assertEqual(rows[0]["t0_fill_shares"], Decimal("5"))
        self.assertEqual(rows[0]["t0_fill_notional"], Decimal("3.50"))
        self.assertIsNone(rows[0]["realized_profit"])

    def test_missing_depth_or_zero_size_writes_reason(self) -> None:
        pair_id = self.add_pair("pm-zero", "KS-ZERO")
        pm_obs_id = self.add_pm_observation(
            "pm-zero",
            "2026-06-29T00:00:00+00:00",
            asks=[{"price": "0.40", "size": "0"}],
        )
        ks_obs_id = self.add_ks_observation(
            "KS-ZERO",
            "2026-06-29T00:00:01+00:00",
            yes_bids=[["0.70", "5"]],
        )
        self.add_edge_snapshot(pair_id, pm_obs_id, ks_obs_id, sim.LEG_PM_YES_KS_NO)
        self.conn.commit()

        rows = sim.simulate(
            self.conn,
            min_net_edge=Decimal("0.02"),
            notional=Decimal("100"),
            max_notional=Decimal("100"),
            latencies=[5],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "zero_t0_pm_bbo_size")
        self.assertEqual(rows[0]["t0_fill_shares"], Decimal("0"))

    def test_db_output_rows_match_csv_output(self) -> None:
        pair_id = self.add_pair()
        pm_obs_id = self.add_pm_observation(
            "pm-token",
            "2026-06-29T00:00:00+00:00",
            asks=[{"price": "0.40", "size": "10"}],
        )
        ks_obs_id = self.add_ks_observation(
            "KS-MARKET-AAA",
            "2026-06-29T00:00:01+00:00",
            yes_bids=[["0.70", "5"]],
        )
        self.add_edge_snapshot(pair_id, pm_obs_id, ks_obs_id, sim.LEG_PM_YES_KS_NO)
        self.conn.commit()
        rows = sim.simulate(
            self.conn,
            min_net_edge=Decimal("0.02"),
            notional=Decimal("100"),
            max_notional=Decimal("100"),
            latencies=[5],
        )
        summary_rows = sim.summarize(rows)
        trade_csv = Path(self.tempdir.name) / "trades.csv"
        summary_csv = Path(self.tempdir.name) / "summary.csv"

        run_id = sim.write_db_outputs(
            self.conn,
            rows=rows,
            summary_rows=summary_rows,
            min_net_edge=Decimal("0.02"),
            notional=Decimal("100"),
            max_notional=Decimal("100"),
            latencies=[5],
            output_path=str(trade_csv),
            summary_output_path=str(summary_csv),
        )
        sim.write_csv(rows, sim.TRADE_FIELDS, trade_csv)
        sim.write_csv(summary_rows, sim.SUMMARY_FIELDS, summary_csv)

        with trade_csv.open(newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        db_rows = self.conn.execute(
            """
            SELECT *
            FROM paper_trade_results
            WHERE paper_trade_run_id = ?
            ORDER BY paper_trade_result_id
            """,
            (run_id,),
        ).fetchall()
        run = self.conn.execute(
            "SELECT * FROM paper_trade_runs WHERE paper_trade_run_id = ?",
            (run_id,),
        ).fetchone()

        self.assertEqual(len(db_rows), len(csv_rows))
        self.assertEqual(db_rows[0]["reason"], csv_rows[0]["reason"])
        self.assertEqual(db_rows[0]["t0_fill_shares"], csv_rows[0]["t0_fill_shares"])
        self.assertEqual(run["result_count"], len(csv_rows))
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM paper_trade_summary WHERE paper_trade_run_id = ?",
                (run_id,),
            ).fetchone()[0],
            len(summary_rows),
        )


if __name__ == "__main__":
    unittest.main()
