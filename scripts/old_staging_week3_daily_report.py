#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import old_market_db
import old_staging_paper_trade_simulator as simulator


VIEW_ALL = "all"
VIEW_CLEAN = "clean_le_5pct"
VIEW_OUTLIER = "outlier_gt_5pct"

SUMMARY_FIELDS = [
    "view",
    "latency_seconds",
    "universe",
    "trade_count",
    "filled_trade_count",
    "partial_trade_count",
    "missing_trade_count",
    "negative_realized_count",
    "requested_notional",
    "total_requested_notional",
    "total_t0_fill_notional",
    "total_delayed_fill_notional",
    "total_theoretical_profit",
    "total_realized_profit",
    "profit_capture_ratio",
    "fill_capture_ratio",
    "estimated_30d_profit",
    "avg_net_edge",
    "max_net_edge",
]

REPORT_FIELDS = [
    "section",
    "rank",
    "view",
    "latency_seconds",
    "universe",
    "metric",
    "value",
    "details",
]


@dataclass(frozen=True)
class DailyReportConfig:
    start_ts_utc: str
    end_ts_utc: str
    min_net_edge: Decimal
    notional: Decimal
    max_notional: Decimal
    latencies: list[int]
    window_gap_seconds: int
    sanity_edge: Decimal
    limit: int = 0


@dataclass(frozen=True)
class DailyReport:
    candidates: list[sqlite3.Row]
    opportunity_windows: list[sqlite3.Row]
    trade_rows: list[dict[str, Any]]
    summary_rows: list[dict[str, Any]]
    report_rows: list[dict[str, Any]]


def parse_iso_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return None
    return Decimal(str(value))


def candidate_net_edge(candidate: sqlite3.Row) -> Decimal:
    return simulator.scaled_decimal(candidate["net_edge_scaled"]) or Decimal("0")


def row_net_edge(row: dict[str, Any]) -> Decimal:
    return decimal_value(row.get("net_edge")) or Decimal("0")


def format_value(value: Any) -> str:
    if isinstance(value, Decimal):
        return old_market_db.format_decimal(value, places=6)
    if value is None:
        return ""
    return str(value)


def write_csv(rows: list[dict[str, Any]], fields: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_value(row.get(field)) for field in fields})


def load_window_candidates(conn: sqlite3.Connection, config: DailyReportConfig) -> list[sqlite3.Row]:
    params: list[Any] = [
        old_market_db.to_scaled(config.min_net_edge),
        config.start_ts_utc,
        config.end_ts_utc,
    ]
    limit_clause = ""
    if config.limit > 0:
        limit_clause = "LIMIT ?"
        params.append(config.limit)
    return conn.execute(
        f"""
        SELECT
            edge.edge_snapshot_id,
            edge.ts_utc,
            edge.paired_contract_id,
            edge.pm_observation_id,
            edge.ks_observation_id,
            edge.best_leg,
            edge.net_edge_scaled,
            pair.universe,
            pair.category,
            pair.match_name,
            pair.event_date,
            pair.pm_token_id,
            pair.ks_market_ticker,
            pm_obs.collected_ts_utc AS pm_t0_ts_utc,
            ks_obs.collected_ts_utc AS ks_t0_ts_utc
        FROM edge_snapshots edge
        JOIN paired_contracts pair ON pair.paired_contract_id = edge.paired_contract_id
        JOIN orderbook_observations pm_obs ON pm_obs.observation_id = edge.pm_observation_id
        JOIN orderbook_observations ks_obs ON ks_obs.observation_id = edge.ks_observation_id
        WHERE pair.safe_paired = 1
          AND edge.best_leg IN ('PM_YES_KS_NO', 'PM_NO_KS_YES')
          AND edge.net_edge_scaled > ?
          AND edge.ts_utc >= ?
          AND edge.ts_utc < ?
          AND pm_obs.status = 'ok'
          AND ks_obs.status = 'ok'
        ORDER BY edge.ts_utc ASC, edge.edge_snapshot_id ASC
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def dedupe_opportunity_windows(candidates: list[sqlite3.Row], gap_seconds: int) -> list[sqlite3.Row]:
    selected: list[sqlite3.Row] = []
    last_seen_by_key: dict[tuple[int, str], datetime] = {}
    for candidate in candidates:
        key = (int(candidate["paired_contract_id"]), str(candidate["best_leg"]))
        ts = parse_iso_ts(str(candidate["ts_utc"]))
        last_seen = last_seen_by_key.get(key)
        if last_seen is None or (ts - last_seen).total_seconds() > gap_seconds:
            selected.append(candidate)
        last_seen_by_key[key] = ts
    return selected


def simulate_windows(
    conn: sqlite3.Connection,
    windows: list[sqlite3.Row],
    config: DailyReportConfig,
) -> list[dict[str, Any]]:
    requested_notional = min(config.notional, config.max_notional)
    rows: list[dict[str, Any]] = []
    for candidate in windows:
        rows.extend(
            simulator.simulate_candidate(
                conn,
                candidate,
                latencies=config.latencies,
                requested_notional=requested_notional,
            )
        )
    return rows


def in_view(row: dict[str, Any], view: str, sanity_edge: Decimal) -> bool:
    net_edge = row_net_edge(row)
    if view == VIEW_ALL:
        return True
    if view == VIEW_CLEAN:
        return net_edge <= sanity_edge
    if view == VIEW_OUTLIER:
        return net_edge > sanity_edge
    raise ValueError(f"unsupported view={view!r}")


def summarize_group(rows: list[dict[str, Any]], *, view: str, latency: int, universe: str) -> dict[str, Any]:
    requested_notional = rows[0]["requested_notional"] if rows else Decimal("0")
    theoretical = sum((decimal_value(row.get("theoretical_profit")) or Decimal("0") for row in rows), Decimal("0"))
    realized = sum((decimal_value(row.get("realized_profit")) or Decimal("0") for row in rows), Decimal("0"))
    t0_notional = sum((decimal_value(row.get("t0_fill_notional")) or Decimal("0") for row in rows), Decimal("0"))
    delayed_notional = sum((decimal_value(row.get("delayed_fill_notional")) or Decimal("0") for row in rows), Decimal("0"))
    net_edges = [row_net_edge(row) for row in rows]
    return {
        "view": view,
        "latency_seconds": latency,
        "universe": universe,
        "trade_count": len(rows),
        "filled_trade_count": sum(1 for row in rows if (decimal_value(row.get("delayed_fill_notional")) or Decimal("0")) > 0),
        "partial_trade_count": sum(1 for row in rows if row.get("reason") == "partial_delayed_bbo_fill"),
        "missing_trade_count": sum(1 for row in rows if decimal_value(row.get("realized_profit")) is None),
        "negative_realized_count": sum(1 for row in rows if (decimal_value(row.get("realized_profit")) or Decimal("0")) < 0),
        "requested_notional": requested_notional,
        "total_requested_notional": requested_notional * Decimal(len(rows)),
        "total_t0_fill_notional": t0_notional,
        "total_delayed_fill_notional": delayed_notional,
        "total_theoretical_profit": theoretical,
        "total_realized_profit": realized,
        "profit_capture_ratio": simulator.capture_ratio(realized, theoretical),
        "fill_capture_ratio": simulator.capture_ratio(delayed_notional, t0_notional),
        "estimated_30d_profit": realized * Decimal("30"),
        "avg_net_edge": (sum(net_edges, Decimal("0")) / Decimal(len(net_edges))) if net_edges else None,
        "max_net_edge": max(net_edges) if net_edges else None,
    }


def build_summary_rows(trade_rows: list[dict[str, Any]], config: DailyReportConfig) -> list[dict[str, Any]]:
    summary_rows: list[dict[str, Any]] = []
    views = [VIEW_ALL, VIEW_CLEAN, VIEW_OUTLIER]
    for view in views:
        view_rows = [row for row in trade_rows if in_view(row, view, config.sanity_edge)]
        if not view_rows:
            continue
        for latency in config.latencies:
            latency_rows = [row for row in view_rows if int(row["latency_seconds"]) == latency]
            if not latency_rows:
                continue
            summary_rows.append(summarize_group(latency_rows, view=view, latency=latency, universe="ALL"))
            universes = sorted({str(row["universe"]) for row in latency_rows})
            for universe in universes:
                universe_rows = [row for row in latency_rows if str(row["universe"]) == universe]
                summary_rows.append(summarize_group(universe_rows, view=view, latency=latency, universe=universe))
    return summary_rows


def build_report_rows(
    *,
    candidates: list[sqlite3.Row],
    windows: list[sqlite3.Row],
    trade_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    config: DailyReportConfig,
) -> list[dict[str, Any]]:
    report_rows: list[dict[str, Any]] = []

    def add(section: str, metric: str, value: Any, details: str = "", *, rank: str = "", view: str = "", latency: str = "", universe: str = "") -> None:
        report_rows.append(
            {
                "section": section,
                "rank": rank,
                "view": view,
                "latency_seconds": latency,
                "universe": universe,
                "metric": metric,
                "value": value,
                "details": details,
            }
        )

    outlier_windows = [row for row in windows if candidate_net_edge(row) > config.sanity_edge]
    add("metadata", "start_ts_utc", config.start_ts_utc)
    add("metadata", "end_ts_utc", config.end_ts_utc)
    add("metadata", "min_net_edge", config.min_net_edge)
    add("metadata", "notional", min(config.notional, config.max_notional))
    add("metadata", "latencies", ",".join(str(latency) for latency in config.latencies))
    add("counts", "raw_positive_snapshots", len(candidates), "Rows before opportunity-window dedupe.")
    add("counts", "opportunity_windows", len(windows), f"Gap threshold: {config.window_gap_seconds}s.")
    add("counts", "simulated_trade_rows", len(trade_rows), "Opportunity windows multiplied by latency scenarios.")
    add("counts", "sanity_outlier_windows", len(outlier_windows), f"net_edge > {config.sanity_edge}")

    all_summaries = [row for row in summary_rows if row["universe"] == "ALL"]
    ranked = sorted(all_summaries, key=lambda row: decimal_value(row["total_realized_profit"]) or Decimal("0"), reverse=True)
    for rank, row in enumerate(ranked[:2], start=1):
        add(
            "best_scenario",
            "total_realized_profit",
            row["total_realized_profit"],
            f"30d={format_value(row['estimated_30d_profit'])}; trades={row['trade_count']}; capture={format_value(row['profit_capture_ratio'])}",
            rank=str(rank),
            view=str(row["view"]),
            latency=str(row["latency_seconds"]),
            universe="ALL",
        )
    for rank, row in enumerate(reversed(ranked[-2:]), start=1):
        add(
            "worst_scenario",
            "total_realized_profit",
            row["total_realized_profit"],
            f"30d={format_value(row['estimated_30d_profit'])}; trades={row['trade_count']}; capture={format_value(row['profit_capture_ratio'])}",
            rank=str(rank),
            view=str(row["view"]),
            latency=str(row["latency_seconds"]),
            universe="ALL",
        )

    clean_by_universe = [
        row
        for row in summary_rows
        if row["view"] == VIEW_CLEAN and row["universe"] != "ALL"
    ]
    for row in clean_by_universe:
        add(
            "by_universe_clean",
            "total_realized_profit",
            row["total_realized_profit"],
            f"30d={format_value(row['estimated_30d_profit'])}; trades={row['trade_count']}; filled={row['filled_trade_count']}",
            view=str(row["view"]),
            latency=str(row["latency_seconds"]),
            universe=str(row["universe"]),
        )

    return report_rows


def build_daily_report(conn: sqlite3.Connection, config: DailyReportConfig) -> DailyReport:
    candidates = load_window_candidates(conn, config)
    windows = dedupe_opportunity_windows(candidates, config.window_gap_seconds)
    trade_rows = simulate_windows(conn, windows, config)
    summary_rows = build_summary_rows(trade_rows, config)
    report_rows = build_report_rows(
        candidates=candidates,
        windows=windows,
        trade_rows=trade_rows,
        summary_rows=summary_rows,
        config=config,
    )
    return DailyReport(candidates, windows, trade_rows, summary_rows, report_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Week 3 daily PM/KS paper-trade report from local edge_snapshots.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--start-ts")
    parser.add_argument("--end-ts")
    parser.add_argument("--min-net-edge", type=lambda value: simulator.parse_decimal(value, "--min-net-edge"), default=Decimal("0.02"))
    parser.add_argument("--notional", type=lambda value: simulator.parse_decimal(value, "--notional"), default=Decimal("1000"))
    parser.add_argument("--max-notional", type=lambda value: simulator.parse_decimal(value, "--max-notional"), default=Decimal("1000"))
    parser.add_argument("--latencies", type=simulator.parse_latency_csv, default=simulator.parse_latency_csv("5,15,30"))
    parser.add_argument("--window-gap-seconds", type=int, default=60)
    parser.add_argument("--sanity-edge", type=lambda value: simulator.parse_decimal(value, "--sanity-edge"), default=Decimal("0.05"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--trades-output", default="data/old/old_week3_daily_trades_latest.csv")
    parser.add_argument("--summary-output", default="data/old/old_week3_daily_summary_latest.csv")
    parser.add_argument("--report-output", default="data/old/old_week3_daily_report_latest.csv")
    parser.add_argument("--dry-run", action="store_true", help="Build the report and print counts without writing CSV files.")
    args = parser.parse_args()
    if args.lookback_hours <= 0:
        parser.error("--lookback-hours must be positive")
    if args.window_gap_seconds < 0:
        parser.error("--window-gap-seconds must be non-negative")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    return args


def config_from_args(args: argparse.Namespace) -> DailyReportConfig:
    end_dt = parse_iso_ts(args.end_ts) if args.end_ts else parse_iso_ts(old_market_db.utc_now())
    start_dt = parse_iso_ts(args.start_ts) if args.start_ts else end_dt - timedelta(hours=args.lookback_hours)
    if start_dt >= end_dt:
        raise SystemExit("--start-ts must be before --end-ts")
    return DailyReportConfig(
        start_ts_utc=format_ts(start_dt),
        end_ts_utc=format_ts(end_dt),
        min_net_edge=args.min_net_edge,
        notional=args.notional,
        max_notional=args.max_notional,
        latencies=args.latencies,
        window_gap_seconds=args.window_gap_seconds,
        sanity_edge=args.sanity_edge,
        limit=args.limit,
    )


def main() -> None:
    args = parse_args()
    config = config_from_args(args)
    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        report = build_daily_report(conn, config)

    if not args.dry_run:
        write_csv(report.trade_rows, simulator.TRADE_FIELDS, Path(args.trades_output))
        write_csv(report.summary_rows, SUMMARY_FIELDS, Path(args.summary_output))
        write_csv(report.report_rows, REPORT_FIELDS, Path(args.report_output))

    print(
        "week3 daily report complete: "
        f"raw_snapshots={len(report.candidates)}; "
        f"opportunity_windows={len(report.opportunity_windows)}; "
        f"trade_rows={len(report.trade_rows)}; "
        f"summary_rows={len(report.summary_rows)}; "
        f"start={config.start_ts_utc}; end={config.end_ts_utc}; "
        f"trades_output={args.trades_output}; summary_output={args.summary_output}; "
        f"report_output={args.report_output}; dry_run={args.dry_run}"
    )


if __name__ == "__main__":
    main()
