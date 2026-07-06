#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any

import old_market_db


getcontext().prec = 36

LEG_PM_YES_KS_NO = "PM_YES_KS_NO"
LEG_PM_NO_KS_YES = "PM_NO_KS_YES"
ONE = Decimal("1")
ZERO = Decimal("0")
EPSILON = Decimal("0.000000000001")

TRADE_FIELDS = [
    "ts_utc",
    "t0_book_ts_utc",
    "paired_contract_id",
    "edge_snapshot_id",
    "universe",
    "sport",
    "match_name",
    "event_date",
    "best_leg",
    "net_edge",
    "latency_seconds",
    "requested_notional",
    "t0_fill_shares",
    "delayed_fill_shares",
    "t0_fill_notional",
    "delayed_fill_notional",
    "t0_avg_cost",
    "delayed_avg_cost",
    "theoretical_profit",
    "realized_profit",
    "profit_capture_ratio",
    "fill_capture_ratio",
    "t0_pm_observation_id",
    "t0_ks_observation_id",
    "delayed_pm_observation_id",
    "delayed_ks_observation_id",
    "delayed_book_ts_utc",
    "net_edge_bucket",
    "book_depth_bucket",
    "reason",
    "bbo_warning",
]

SUMMARY_FIELDS = [
    "latency_seconds",
    "universe",
    "sport",
    "net_edge_bucket",
    "book_depth_bucket",
    "count",
    "count_with_profit_capture",
    "median_profit_capture_ratio",
    "p25_profit_capture_ratio",
    "p75_profit_capture_ratio",
    "count_with_fill_capture",
    "median_fill_capture_ratio",
    "p25_fill_capture_ratio",
    "p75_fill_capture_ratio",
    "total_theoretical_profit",
    "total_realized_profit",
]


@dataclass(frozen=True)
class BboQuote:
    action: str
    source_side: str
    source_price: Decimal
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class FillResult:
    shares: Decimal
    fill_notional: Decimal
    avg_cost: Decimal | None
    profit: Decimal
    gross_cost: Decimal | None


def parse_decimal(value: str, arg_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{arg_name} must be a decimal number") from exc
    if parsed < ZERO:
        raise argparse.ArgumentTypeError(f"{arg_name} must be non-negative")
    return parsed


def parse_latency_csv(value: str) -> list[int]:
    latencies: list[int] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            latency = int(part)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--latencies must be comma-separated integer seconds") from exc
        if latency < 0:
            raise argparse.ArgumentTypeError("--latencies values must be non-negative")
        latencies.append(latency)
    if not latencies:
        raise argparse.ArgumentTypeError("--latencies must include at least one value")
    return sorted(dict.fromkeys(latencies))


def parse_iso_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def ts_plus_seconds(value: str, seconds: int) -> str:
    return (parse_iso_ts(value) + timedelta(seconds=seconds)).isoformat()


def max_ts(left: str, right: str) -> str:
    return max(parse_iso_ts(left), parse_iso_ts(right)).isoformat()


def scaled_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return old_market_db.scaled_to_decimal(int(value))


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return old_market_db.format_decimal(value, places=6)
    return str(value)


def fee(rate: Decimal, price: Decimal) -> Decimal:
    return rate * price * (ONE - price)


def leg_actions(best_leg: str) -> tuple[str, str]:
    if best_leg == LEG_PM_YES_KS_NO:
        return "pm_buy_yes", "ks_buy_no"
    if best_leg == LEG_PM_NO_KS_YES:
        return "pm_buy_no", "ks_buy_yes"
    raise ValueError(f"unsupported best_leg={best_leg!r}")


def source_side_for_action(action: str) -> str:
    if action == "pm_buy_yes":
        return "ask"
    if action == "pm_buy_no":
        return "bid"
    if action == "ks_buy_no":
        return "yes_bid"
    if action == "ks_buy_yes":
        return "no_bid"
    raise ValueError(f"unsupported action={action!r}")


def buy_price_from_source(action: str, source_price: Decimal) -> Decimal:
    if action == "pm_buy_yes":
        return source_price
    if action in {"pm_buy_no", "ks_buy_no", "ks_buy_yes"}:
        return ONE - source_price
    raise ValueError(f"unsupported action={action!r}")


def best_source_price(prices: list[Decimal], action: str) -> Decimal:
    if action == "pm_buy_yes":
        return min(prices)
    if action in {"pm_buy_no", "ks_buy_no", "ks_buy_yes"}:
        return max(prices)
    raise ValueError(f"unsupported action={action!r}")


def observation_buy_price_and_size(obs: sqlite3.Row, action: str) -> tuple[Decimal | None, Decimal | None]:
    if action == "pm_buy_yes":
        return scaled_decimal(obs["best_ask_scaled"]), scaled_decimal(obs["best_ask_size_scaled"])
    if action == "pm_buy_no":
        bid = scaled_decimal(obs["best_bid_scaled"])
        return (ONE - bid) if bid is not None else None, scaled_decimal(obs["best_bid_size_scaled"])
    if action == "ks_buy_no":
        yes_bid = scaled_decimal(obs["best_bid_scaled"])
        return (ONE - yes_bid) if yes_bid is not None else None, scaled_decimal(obs["best_bid_size_scaled"])
    if action == "ks_buy_yes":
        return scaled_decimal(obs["best_ask_scaled"]), scaled_decimal(obs["best_ask_size_scaled"])
    raise ValueError(f"unsupported action={action!r}")


def quote_for_action(
    conn: sqlite3.Connection,
    obs: sqlite3.Row,
    action: str,
    label: str,
) -> tuple[BboQuote | None, str, str]:
    side = source_side_for_action(action)
    rows = conn.execute(
        """
        SELECT price_scaled, size_scaled
        FROM orderbook_levels
        WHERE observation_id = ?
          AND side = ?
        """,
        (obs["observation_id"], side),
    ).fetchall()
    levels: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        source_price = scaled_decimal(row["price_scaled"])
        size = scaled_decimal(row["size_scaled"])
        if source_price is None or size is None:
            continue
        if source_price < ZERO or source_price > ONE:
            continue
        levels.append((source_price, size))
    if not levels:
        return None, f"missing_{label}_depth", ""

    best_price = best_source_price([price for price, _size in levels], action)
    best_size = sum((size for price, size in levels if price == best_price), ZERO)
    buy_price = buy_price_from_source(action, best_price)
    if buy_price < ZERO or buy_price > ONE:
        return None, f"invalid_{label}_bbo_price", ""
    if best_size <= ZERO:
        return None, f"zero_{label}_bbo_size", ""

    warnings: list[str] = []
    obs_price, obs_size = observation_buy_price_and_size(obs, action)
    if obs_price is not None and abs(obs_price - buy_price) > EPSILON:
        warnings.append(f"{label}_bbo_price_levels_preferred")
    if obs_size is not None and abs(obs_size - best_size) > EPSILON:
        warnings.append(f"{label}_bbo_size_levels_preferred")

    return BboQuote(action, side, best_price, buy_price, best_size), "", ";".join(warnings)


def bbo_capped_fill(
    pm_quote: BboQuote,
    ks_quote: BboQuote,
    requested_notional: Decimal,
    *,
    max_shares: Decimal | None = None,
) -> FillResult:
    gross_cost = pm_quote.price + ks_quote.price
    if gross_cost <= ZERO:
        return FillResult(ZERO, ZERO, None, ZERO, gross_cost)

    size_cap = min(pm_quote.size, ks_quote.size)
    notional_cap = requested_notional / gross_cost
    caps = [size_cap, notional_cap]
    if max_shares is not None:
        caps.append(max_shares)
    shares = min(caps)
    if shares <= ZERO:
        return FillResult(ZERO, ZERO, None, ZERO, gross_cost)

    fill_notional = shares * gross_cost
    net_edge = ONE - gross_cost - fee(old_market_db.PM_FEE_RATE, pm_quote.price) - fee(old_market_db.KS_FEE_RATE, ks_quote.price)
    profit = shares * net_edge
    return FillResult(shares, fill_notional, gross_cost, profit, gross_cost)


def next_observation(
    conn: sqlite3.Connection,
    *,
    venue: str,
    instrument_id: str,
    target_ts_utc: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM orderbook_observations
        WHERE venue = ?
          AND instrument_id = ?
          AND status = 'ok'
          AND collected_ts_utc >= ?
        ORDER BY collected_ts_utc ASC, observation_id ASC
        LIMIT 1
        """,
        (venue, instrument_id, target_ts_utc),
    ).fetchone()


def load_alert_candidates(conn: sqlite3.Connection, min_net_edge: Decimal, limit: int = 0) -> list[sqlite3.Row]:
    params: list[Any] = [old_market_db.to_scaled(min_net_edge)]
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
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
          AND pm_obs.status = 'ok'
          AND ks_obs.status = 'ok'
        ORDER BY edge.ts_utc ASC, edge.edge_snapshot_id ASC
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def net_edge_bucket(net_edge: Decimal) -> str:
    if net_edge < ZERO:
        return "negative"
    if net_edge < Decimal("0.005"):
        return "0-0.5%"
    if net_edge < Decimal("0.01"):
        return "0.5-1%"
    if net_edge < Decimal("0.02"):
        return "1-2%"
    if net_edge < Decimal("0.03"):
        return "2-3%"
    if net_edge < Decimal("0.05"):
        return "3-5%"
    return "5%+"


def book_depth_bucket(fill_notional: Decimal, requested_notional: Decimal) -> str:
    if requested_notional <= ZERO or fill_notional <= ZERO:
        return "none"
    ratio = fill_notional / requested_notional
    if ratio < Decimal("0.25"):
        return "0-25%"
    if ratio < Decimal("0.50"):
        return "25-50%"
    if ratio < Decimal("0.999999"):
        return "50-100%"
    return "full"


def capture_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or abs(denominator) <= EPSILON:
        return None
    return numerator / denominator


def empty_fill() -> FillResult:
    return FillResult(ZERO, ZERO, None, ZERO, None)


def result_row(
    candidate: sqlite3.Row,
    *,
    latency_seconds: int,
    requested_notional: Decimal,
    t0_book_ts_utc: str,
    t0_fill: FillResult,
    delayed_fill: FillResult | None,
    delayed_pm_observation_id: int | None,
    delayed_ks_observation_id: int | None,
    delayed_book_ts_utc: str,
    reason: str,
    bbo_warning: str,
) -> dict[str, Any]:
    net_edge = scaled_decimal(candidate["net_edge_scaled"]) or ZERO
    profit_capture = capture_ratio(
        delayed_fill.profit if delayed_fill is not None else None,
        t0_fill.profit,
    )
    fill_capture = capture_ratio(
        delayed_fill.fill_notional if delayed_fill is not None else None,
        t0_fill.fill_notional,
    )
    return {
        "ts_utc": candidate["ts_utc"],
        "t0_book_ts_utc": t0_book_ts_utc,
        "paired_contract_id": candidate["paired_contract_id"],
        "edge_snapshot_id": candidate["edge_snapshot_id"],
        "universe": candidate["universe"],
        "sport": candidate["category"],
        "match_name": candidate["match_name"],
        "event_date": candidate["event_date"],
        "best_leg": candidate["best_leg"],
        "net_edge": net_edge,
        "latency_seconds": latency_seconds,
        "requested_notional": requested_notional,
        "t0_fill_shares": t0_fill.shares,
        "delayed_fill_shares": delayed_fill.shares if delayed_fill is not None else None,
        "t0_fill_notional": t0_fill.fill_notional,
        "delayed_fill_notional": delayed_fill.fill_notional if delayed_fill is not None else None,
        "t0_avg_cost": t0_fill.avg_cost,
        "delayed_avg_cost": delayed_fill.avg_cost if delayed_fill is not None else None,
        "theoretical_profit": t0_fill.profit,
        "realized_profit": delayed_fill.profit if delayed_fill is not None else None,
        "profit_capture_ratio": profit_capture,
        "fill_capture_ratio": fill_capture,
        "t0_pm_observation_id": candidate["pm_observation_id"],
        "t0_ks_observation_id": candidate["ks_observation_id"],
        "delayed_pm_observation_id": delayed_pm_observation_id,
        "delayed_ks_observation_id": delayed_ks_observation_id,
        "delayed_book_ts_utc": delayed_book_ts_utc,
        "net_edge_bucket": net_edge_bucket(net_edge),
        "book_depth_bucket": book_depth_bucket(t0_fill.fill_notional, requested_notional),
        "reason": reason,
        "bbo_warning": bbo_warning,
    }


def simulate_candidate(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    *,
    latencies: list[int],
    requested_notional: Decimal,
) -> list[dict[str, Any]]:
    pm_action, ks_action = leg_actions(str(candidate["best_leg"]))
    t0_book_ts_utc = max_ts(str(candidate["pm_t0_ts_utc"]), str(candidate["ks_t0_ts_utc"]))
    pm_t0_obs = conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (candidate["pm_observation_id"],)).fetchone()
    ks_t0_obs = conn.execute("SELECT * FROM orderbook_observations WHERE observation_id = ?", (candidate["ks_observation_id"],)).fetchone()

    warnings: list[str] = []
    pm_quote, pm_reason, pm_warning = quote_for_action(conn, pm_t0_obs, pm_action, "t0_pm")
    ks_quote, ks_reason, ks_warning = quote_for_action(conn, ks_t0_obs, ks_action, "t0_ks")
    warnings.extend(warning for warning in (pm_warning, ks_warning) if warning)

    if pm_reason or ks_reason:
        reason = ";".join(reason for reason in (pm_reason, ks_reason) if reason)
        return [
            result_row(
                candidate,
                latency_seconds=latency,
                requested_notional=requested_notional,
                t0_book_ts_utc=t0_book_ts_utc,
                t0_fill=empty_fill(),
                delayed_fill=None,
                delayed_pm_observation_id=None,
                delayed_ks_observation_id=None,
                delayed_book_ts_utc="",
                reason=reason,
                bbo_warning=";".join(warnings),
            )
            for latency in latencies
        ]

    t0_fill = bbo_capped_fill(pm_quote, ks_quote, requested_notional)
    if t0_fill.shares <= ZERO:
        return [
            result_row(
                candidate,
                latency_seconds=latency,
                requested_notional=requested_notional,
                t0_book_ts_utc=t0_book_ts_utc,
                t0_fill=t0_fill,
                delayed_fill=None,
                delayed_pm_observation_id=None,
                delayed_ks_observation_id=None,
                delayed_book_ts_utc="",
                reason="no_t0_bbo_fill",
                bbo_warning=";".join(warnings),
            )
            for latency in latencies
        ]

    rows: list[dict[str, Any]] = []
    for latency in latencies:
        target_ts = ts_plus_seconds(t0_book_ts_utc, latency)
        delayed_pm = next_observation(
            conn,
            venue="pm",
            instrument_id=str(candidate["pm_token_id"]),
            target_ts_utc=target_ts,
        )
        delayed_ks = next_observation(
            conn,
            venue="ks",
            instrument_id=str(candidate["ks_market_ticker"]),
            target_ts_utc=target_ts,
        )
        missing = []
        if delayed_pm is None:
            missing.append("pm")
        if delayed_ks is None:
            missing.append("ks")
        if missing:
            rows.append(
                result_row(
                    candidate,
                    latency_seconds=latency,
                    requested_notional=requested_notional,
                    t0_book_ts_utc=t0_book_ts_utc,
                    t0_fill=t0_fill,
                    delayed_fill=None,
                    delayed_pm_observation_id=None,
                    delayed_ks_observation_id=None,
                    delayed_book_ts_utc="",
                    reason=f"missing_delayed_{'_'.join(missing)}_snapshot",
                    bbo_warning=";".join(warnings),
                )
            )
            continue

        delayed_warnings = list(warnings)
        delayed_pm_quote, delayed_pm_reason, delayed_pm_warning = quote_for_action(conn, delayed_pm, pm_action, "delayed_pm")
        delayed_ks_quote, delayed_ks_reason, delayed_ks_warning = quote_for_action(conn, delayed_ks, ks_action, "delayed_ks")
        delayed_warnings.extend(warning for warning in (delayed_pm_warning, delayed_ks_warning) if warning)
        delayed_book_ts_utc = max_ts(str(delayed_pm["collected_ts_utc"]), str(delayed_ks["collected_ts_utc"]))
        if delayed_pm_reason or delayed_ks_reason:
            reason = ";".join(reason for reason in (delayed_pm_reason, delayed_ks_reason) if reason)
            rows.append(
                result_row(
                    candidate,
                    latency_seconds=latency,
                    requested_notional=requested_notional,
                    t0_book_ts_utc=t0_book_ts_utc,
                    t0_fill=t0_fill,
                    delayed_fill=None,
                    delayed_pm_observation_id=int(delayed_pm["observation_id"]),
                    delayed_ks_observation_id=int(delayed_ks["observation_id"]),
                    delayed_book_ts_utc=delayed_book_ts_utc,
                    reason=reason,
                    bbo_warning=";".join(delayed_warnings),
                )
            )
            continue

        delayed_fill = bbo_capped_fill(
            delayed_pm_quote,
            delayed_ks_quote,
            requested_notional,
            max_shares=t0_fill.shares,
        )
        if delayed_fill.shares <= ZERO:
            reason = "no_delayed_bbo_fill"
        elif delayed_fill.shares < t0_fill.shares - EPSILON:
            reason = "partial_delayed_bbo_fill"
        else:
            reason = "ok"
        rows.append(
            result_row(
                candidate,
                latency_seconds=latency,
                requested_notional=requested_notional,
                t0_book_ts_utc=t0_book_ts_utc,
                t0_fill=t0_fill,
                delayed_fill=delayed_fill,
                delayed_pm_observation_id=int(delayed_pm["observation_id"]),
                delayed_ks_observation_id=int(delayed_ks["observation_id"]),
                delayed_book_ts_utc=delayed_book_ts_utc,
                reason=reason,
                bbo_warning=";".join(delayed_warnings),
            )
        )
    return rows


def simulate(
    conn: sqlite3.Connection,
    *,
    min_net_edge: Decimal,
    notional: Decimal,
    max_notional: Decimal,
    latencies: list[int],
    limit: int = 0,
) -> list[dict[str, Any]]:
    requested_notional = min(notional, max_notional)
    rows: list[dict[str, Any]] = []
    for candidate in load_alert_candidates(conn, min_net_edge, limit=limit):
        rows.extend(
            simulate_candidate(
                conn,
                candidate,
                latencies=latencies,
                requested_notional=requested_notional,
            )
        )
    return rows


def percentile(values: list[Decimal], q: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * Decimal(len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - Decimal(lower_index)
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["latency_seconds"],
            row["universe"],
            row["sport"],
            row["net_edge_bucket"],
            row["book_depth_bucket"],
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: (int(item[0]), str(item[1]), str(item[2]), str(item[3]), str(item[4]))):
        group = grouped[key]
        profit_ratios = [row["profit_capture_ratio"] for row in group if isinstance(row["profit_capture_ratio"], Decimal)]
        fill_ratios = [row["fill_capture_ratio"] for row in group if isinstance(row["fill_capture_ratio"], Decimal)]
        theoretical_profit = sum((row["theoretical_profit"] for row in group if isinstance(row["theoretical_profit"], Decimal)), ZERO)
        realized_profit = sum((row["realized_profit"] for row in group if isinstance(row["realized_profit"], Decimal)), ZERO)
        summary_rows.append(
            {
                "latency_seconds": key[0],
                "universe": key[1],
                "sport": key[2],
                "net_edge_bucket": key[3],
                "book_depth_bucket": key[4],
                "count": len(group),
                "count_with_profit_capture": len(profit_ratios),
                "median_profit_capture_ratio": percentile(profit_ratios, Decimal("0.5")),
                "p25_profit_capture_ratio": percentile(profit_ratios, Decimal("0.25")),
                "p75_profit_capture_ratio": percentile(profit_ratios, Decimal("0.75")),
                "count_with_fill_capture": len(fill_ratios),
                "median_fill_capture_ratio": percentile(fill_ratios, Decimal("0.5")),
                "p25_fill_capture_ratio": percentile(fill_ratios, Decimal("0.25")),
                "p75_fill_capture_ratio": percentile(fill_ratios, Decimal("0.75")),
                "total_theoretical_profit": theoretical_profit,
                "total_realized_profit": realized_profit,
            }
        )
    return summary_rows


def write_csv(rows: list[dict[str, Any]], fields: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: format_value(row.get(field)) for field in fields})


def formatted_row(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: format_value(row.get(field)) for field in fields}


def insert_formatted_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    run_column: str,
    run_id: int,
    rows: list[dict[str, Any]],
    fields: list[str],
) -> None:
    if not rows:
        return
    columns = [run_column, *fields]
    placeholders = ",".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    values = [
        (run_id, *(formatted_row(row, fields)[field] for field in fields))
        for row in rows
    ]
    conn.executemany(
        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
        values,
    )


def write_db_outputs(
    conn: sqlite3.Connection,
    *,
    rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    min_net_edge: Decimal,
    notional: Decimal,
    max_notional: Decimal,
    latencies: list[int],
    output_path: str,
    summary_output_path: str,
) -> int:
    requested_notional = min(notional, max_notional)
    started_ts = old_market_db.utc_now()
    cursor = conn.execute(
        """
        INSERT INTO paper_trade_runs (
            started_ts_utc, min_net_edge_scaled, notional_scaled, max_notional_scaled,
            requested_notional_scaled, latencies_csv, output_path, summary_output_path,
            status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ok')
        """,
        (
            started_ts,
            old_market_db.to_scaled(min_net_edge),
            old_market_db.to_scaled(notional),
            old_market_db.to_scaled(max_notional),
            old_market_db.to_scaled(requested_notional),
            ",".join(str(latency) for latency in latencies),
            output_path,
            summary_output_path,
        ),
    )
    run_id = int(cursor.lastrowid)
    insert_formatted_rows(
        conn,
        table="paper_trade_results",
        run_column="paper_trade_run_id",
        run_id=run_id,
        rows=rows,
        fields=TRADE_FIELDS,
    )
    insert_formatted_rows(
        conn,
        table="paper_trade_summary",
        run_column="paper_trade_run_id",
        run_id=run_id,
        rows=summary_rows,
        fields=SUMMARY_FIELDS,
    )
    conn.execute(
        """
        UPDATE paper_trade_runs
        SET finished_ts_utc = ?,
            result_count = ?,
            summary_count = ?
        WHERE paper_trade_run_id = ?
        """,
        (old_market_db.utc_now(), len(rows), len(summary_rows), run_id),
    )
    conn.commit()
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 3 no-slippage BBO-capped PM/KS paper-trade simulator.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--min-net-edge", type=lambda value: parse_decimal(value, "--min-net-edge"), default=Decimal("0"))
    parser.add_argument("--notional", type=lambda value: parse_decimal(value, "--notional"), default=Decimal("100"))
    parser.add_argument("--max-notional", type=lambda value: parse_decimal(value, "--max-notional"), default=Decimal("1000"))
    parser.add_argument("--latencies", type=parse_latency_csv, default=parse_latency_csv("5,15,30"))
    parser.add_argument("--output", default="data/old/old_week3_paper_trades.csv")
    parser.add_argument("--summary-output", default="data/old/old_week3_paper_trade_summary.csv")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-db-output", action="store_true", help="Only write CSV files; default also writes paper_trade_* tables.")
    args = parser.parse_args()

    if args.notional <= ZERO:
        parser.error("--notional must be positive")
    if args.max_notional <= ZERO:
        parser.error("--max-notional must be positive")
    if args.limit < 0:
        parser.error("--limit must be >= 0")

    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        rows = simulate(
            conn,
            min_net_edge=args.min_net_edge,
            notional=args.notional,
            max_notional=args.max_notional,
            latencies=args.latencies,
            limit=args.limit,
        )
        summary_rows = summarize(rows)
        run_id = 0
        if not args.no_db_output:
            run_id = write_db_outputs(
                conn,
                rows=rows,
                summary_rows=summary_rows,
                min_net_edge=args.min_net_edge,
                notional=args.notional,
                max_notional=args.max_notional,
                latencies=args.latencies,
                output_path=args.output,
                summary_output_path=args.summary_output,
            )

    write_csv(rows, TRADE_FIELDS, Path(args.output))
    write_csv(summary_rows, SUMMARY_FIELDS, Path(args.summary_output))
    print(
        "no-slippage paper trade simulation complete: "
        f"rows={len(rows)}; summary_rows={len(summary_rows)}; "
        f"db_run_id={run_id}; "
        f"min_net_edge={old_market_db.format_decimal(args.min_net_edge)}; "
        f"latencies={','.join(str(latency) for latency in args.latencies)}; "
        f"output={args.output}; summary_output={args.summary_output}"
    )


if __name__ == "__main__":
    main()
