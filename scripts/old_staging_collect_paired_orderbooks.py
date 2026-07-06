#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import old_market_db
import old_pipeline_core as core
import old_staging_edge_snapshot_builder as edge_builder


@dataclass(frozen=True)
class FetchResult:
    venue: str
    instrument_id: str
    payload: dict[str, Any] | None
    collected_ts: str
    request_path: str
    depth: int | None
    latency_ms: int
    error_message: str = ""


@dataclass(frozen=True)
class PairWindowResult:
    paired_contract_id: int
    pm_observation_id: int | None
    ks_observation_id: int | None
    edge_snapshot_id: int | None
    status: str
    reason: str
    pm_latency_ms: int | None
    ks_latency_ms: int | None
    snapshot_skew_seconds: float | None


@dataclass(frozen=True)
class WindowStats:
    snapshot_window_id: int
    safe_pair_count: int
    attempted_count: int
    completed_count: int
    edge_inserted_count: int
    error_count: int
    missed_count: int
    elapsed_seconds: float
    status: str
    reason: str


UNIVERSAL_SNAPSHOT_FIELDS = [
    *core.BINARY_CSV_FIELDS,
    "snapshot_window_id",
    "paired_contract_id",
    "pair_status",
    "pair_reason",
    "pm_observation_id",
    "ks_observation_id",
    "edge_snapshot_id",
    "pm_latency_ms",
    "ks_latency_ms",
    "snapshot_skew_seconds",
    "safe_pair_count",
    "attempted_count",
    "completed_count",
    "error_count",
    "missed_count",
    "window_elapsed_seconds",
    "window_status",
    "window_reason",
]


def requested_universes(value: str) -> set[str]:
    return {part.strip().lower().replace("-", "_") for part in value.split(",") if part.strip()}


def selected_pairs(conn, universes: set[str], limit: int) -> list[Any]:
    params: list[Any] = []
    universe_filter = ""
    if universes and "all" not in universes:
        placeholders = ",".join("?" for _ in universes)
        universe_filter = f"AND pair.universe IN ({placeholders})"
        params.extend(sorted(universes))
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        params.append(limit)
    return conn.execute(
        f"""
        SELECT
            pair.*
        FROM paired_contracts pair
        WHERE pair.safe_paired = 1
          {universe_filter}
        ORDER BY
            pair.universe,
            pair.event_date,
            pair.match_name,
            pair.pm_yes_outcome
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def fetch_payload(fetcher) -> tuple[dict[str, Any] | None, str, int, str]:
    started = time.monotonic()
    try:
        payload = fetcher()
        collected_ts = old_market_db.utc_now()
        latency_ms = int((time.monotonic() - started) * 1000)
        return payload, collected_ts, latency_ms, ""
    except Exception as exc:  # noqa: BLE001 - per-instrument fetch errors are recorded in DB.
        collected_ts = old_market_db.utc_now()
        latency_ms = int((time.monotonic() - started) * 1000)
        return None, collected_ts, latency_ms, str(exc)


def get_json_no_retry(base_url: str, path: str, params: dict[str, Any], timeout: float) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{base_url}{path}?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "arb-research-pipeline/0.2"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_orderbook_request(
    *,
    venue: str,
    instrument_id: str,
    request_path: str,
    params: dict[str, Any],
    depth: int | None,
    request_timeout: float,
) -> FetchResult:
    started = time.monotonic()
    try:
        base_url = core.PM_CLOB if venue == "pm" else core.KALSHI_API
        payload = get_json_no_retry(base_url, request_path, params, timeout=request_timeout)
        collected_ts = old_market_db.utc_now()
        latency_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(venue, instrument_id, payload, collected_ts, request_path, depth, latency_ms)
    except Exception as exc:  # noqa: BLE001 - every per-leg request failure is persisted.
        collected_ts = old_market_db.utc_now()
        latency_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(venue, instrument_id, None, collected_ts, request_path, depth, latency_ms, str(exc) or repr(exc))


def record_payload_or_error(
    conn,
    *,
    venue: str,
    instrument_id: str,
    payload: dict[str, Any] | None,
    collected_ts: str,
    request_path: str,
    depth: int | None,
    latency_ms: int,
    error_message: str,
) -> int:
    if payload is not None:
        return old_market_db.record_orderbook_success(
            conn,
            venue,
            instrument_id,
            payload,
            collected_ts,
            request_path,
            depth,
            latency_ms,
        )
    observation_id = old_market_db.record_orderbook_error(
        conn,
        venue,
        instrument_id,
        collected_ts,
        request_path,
        depth,
        latency_ms,
        error_message,
    )
    old_market_db.record_warning(
        conn,
        "collect_paired_orderbooks",
        f"{venue.upper()} paired orderbook fetch failed: {instrument_id}: {error_message}",
        severity="error",
        context={"venue": venue, "instrument_id": instrument_id},
    )
    return observation_id


def record_fetch_result(conn, result: FetchResult) -> int:
    return record_payload_or_error(
        conn,
        venue=result.venue,
        instrument_id=result.instrument_id,
        payload=result.payload,
        collected_ts=result.collected_ts,
        request_path=result.request_path,
        depth=result.depth,
        latency_ms=result.latency_ms,
        error_message=result.error_message,
    )


def collect_pair(conn, pair, ks_depth: int, sleep_seconds: float = 0.0) -> tuple[int, int]:
    pm_token = str(pair["pm_token_id"])
    ks_ticker = str(pair["ks_market_ticker"])
    pm_payload, pm_collected_ts, pm_latency_ms, pm_error = fetch_payload(
        lambda: core.get_json(core.PM_CLOB, "/book", {"token_id": pm_token})
    )
    if sleep_seconds:
        time.sleep(sleep_seconds)
    ks_payload, ks_collected_ts, ks_latency_ms, ks_error = fetch_payload(
        lambda: core.get_json(core.KALSHI_API, f"/markets/{ks_ticker}/orderbook", {"depth": ks_depth})
    )
    pm_observation_id = record_payload_or_error(
        conn,
        venue="pm",
        instrument_id=pm_token,
        payload=pm_payload,
        collected_ts=pm_collected_ts,
        request_path="/book",
        depth=None,
        latency_ms=pm_latency_ms,
        error_message=pm_error,
    )
    ks_observation_id = record_payload_or_error(
        conn,
        venue="ks",
        instrument_id=ks_ticker,
        payload=ks_payload,
        collected_ts=ks_collected_ts,
        request_path=f"/markets/{ks_ticker}/orderbook",
        depth=ks_depth,
        latency_ms=ks_latency_ms,
        error_message=ks_error,
    )
    return pm_observation_id, ks_observation_id


def observation_by_id(conn, observation_id: int):
    return conn.execute(
        "SELECT * FROM orderbook_observations WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()


def observation_has_complete_bbo(row) -> bool:
    return row is not None and all(
        row[column] is not None
        for column in (
            "best_bid_scaled",
            "best_ask_scaled",
            "best_bid_size_scaled",
            "best_ask_size_scaled",
        )
    )


def timeout_error(message: str) -> bool:
    text = message.lower()
    return "timed out" in text or "timeout" in text


def fetch_failure_status_and_reason(pm_result: FetchResult, ks_result: FetchResult) -> tuple[str, str]:
    failures = []
    timed_out = False
    for result in (pm_result, ks_result):
        if not result.error_message:
            continue
        timed_out = timed_out or timeout_error(result.error_message)
        failures.append(f"{result.venue}_fetch_failed:{result.error_message}")
    return ("timeout" if timed_out else "error"), ";".join(failures)


def missing_depth_reason(pm_obs, ks_obs) -> str:
    missing = []
    for label, obs in (("pm", pm_obs), ("ks", ks_obs)):
        if obs is None:
            missing.append(f"{label}_missing_observation")
            continue
        for column in (
            "best_bid_scaled",
            "best_ask_scaled",
            "best_bid_size_scaled",
            "best_ask_size_scaled",
        ):
            if obs[column] is None:
                missing.append(f"{label}_{column}_missing")
    return ";".join(missing) or "missing_bbo_depth"


def request_specs(pair, ks_depth: int) -> tuple[dict[str, Any], dict[str, Any]]:
    pm_token = str(pair["pm_token_id"])
    ks_ticker = str(pair["ks_market_ticker"])
    return (
        {
            "venue": "pm",
            "instrument_id": pm_token,
            "request_path": "/book",
            "params": {"token_id": pm_token},
            "depth": None,
        },
        {
            "venue": "ks",
            "instrument_id": ks_ticker,
            "request_path": f"/markets/{ks_ticker}/orderbook",
            "params": {"depth": ks_depth},
            "depth": ks_depth,
        },
    )


def fetch_window_orderbooks(
    pairs: list[Any],
    *,
    ks_depth: int,
    pair_workers: int,
    request_timeout: float,
    pair_start_interval_ms: int,
) -> dict[tuple[int, str], FetchResult]:
    results: dict[tuple[int, str], FetchResult] = {}
    if not pairs:
        return results
    max_workers = max(1, pair_workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_pairs: dict[concurrent.futures.Future[tuple[int, FetchResult, FetchResult]], int] = {}
        start_interval = max(0.0, pair_start_interval_ms / 1000.0)
        for index, pair in enumerate(pairs):
            pair_id = int(pair["paired_contract_id"])
            future = executor.submit(
                fetch_pair_orderbooks,
                pair,
                ks_depth=ks_depth,
                request_timeout=request_timeout,
            )
            future_pairs[future] = pair_id
            if start_interval and index < len(pairs) - 1:
                time.sleep(start_interval)
        for future in concurrent.futures.as_completed(future_pairs):
            pair_id = future_pairs[future]
            try:
                _pair_id, pm_result, ks_result = future.result()
                results[(pair_id, "pm")] = pm_result
                results[(pair_id, "ks")] = ks_result
            except Exception as exc:  # noqa: BLE001 - defensive; fetch_orderbook_request normally catches.
                pair = next(row for row in pairs if int(row["paired_contract_id"]) == pair_id)
                results[(pair_id, "pm")] = FetchResult(
                    venue="pm",
                    instrument_id=str(pair["pm_token_id"]),
                    payload=None,
                    collected_ts=old_market_db.utc_now(),
                    request_path="/book",
                    depth=None,
                    latency_ms=0,
                    error_message=str(exc) or repr(exc),
                )
                results[(pair_id, "ks")] = FetchResult(
                    venue="ks",
                    instrument_id=str(pair["ks_market_ticker"]),
                    payload=None,
                    collected_ts=old_market_db.utc_now(),
                    request_path=f"/markets/{pair['ks_market_ticker']}/orderbook",
                    depth=ks_depth,
                    latency_ms=0,
                    error_message=str(exc) or repr(exc),
                )
    return results


def fetch_pair_orderbooks(
    pair,
    *,
    ks_depth: int,
    request_timeout: float,
) -> tuple[int, FetchResult, FetchResult]:
    pair_id = int(pair["paired_contract_id"])
    specs = request_specs(pair, ks_depth)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(fetch_orderbook_request, request_timeout=request_timeout, **spec)
            for spec in specs
        ]
        pm_result = futures[0].result()
        ks_result = futures[1].result()
    return pair_id, pm_result, ks_result


def classify_and_record_pair_result(
    conn,
    *,
    snapshot_window_id: int,
    pair,
    pm_result: FetchResult,
    ks_result: FetchResult,
    compute_edges: bool,
    max_age_seconds: float,
    max_skew_seconds: float,
    dedupe_edges: bool,
) -> PairWindowResult:
    pair_id = int(pair["paired_contract_id"])
    pm_observation_id = record_fetch_result(conn, pm_result)
    ks_observation_id = record_fetch_result(conn, ks_result)
    pm_obs = observation_by_id(conn, pm_observation_id)
    ks_obs = observation_by_id(conn, ks_observation_id)
    skew = old_market_db.seconds_between(str(pm_result.collected_ts), str(ks_result.collected_ts))

    edge_snapshot_id: int | None = None
    if pm_result.error_message or ks_result.error_message:
        status, reason = fetch_failure_status_and_reason(pm_result, ks_result)
    elif not observation_has_complete_bbo(pm_obs) or not observation_has_complete_bbo(ks_obs):
        status = "missing_depth"
        reason = missing_depth_reason(pm_obs, ks_obs)
    elif not compute_edges:
        status = "ok"
        reason = "edge_computation_disabled"
    else:
        outcome = edge_builder.insert_edge_snapshot_detailed(
            conn,
            pair,
            pm_obs,
            ks_obs,
            max_age_seconds=max_age_seconds,
            max_skew_seconds=max_skew_seconds,
            dedupe=dedupe_edges,
            snapshot_window_id=snapshot_window_id,
        )
        edge_snapshot_id = outcome.edge_snapshot_id
        status = outcome.status
        reason = outcome.reason
        if outcome.snapshot_skew_seconds is not None:
            skew = outcome.snapshot_skew_seconds

    old_market_db.record_snapshot_pair_result(
        conn,
        snapshot_window_id=snapshot_window_id,
        paired_contract_id=pair_id,
        pm_observation_id=pm_observation_id,
        ks_observation_id=ks_observation_id,
        edge_snapshot_id=edge_snapshot_id,
        status=status,
        reason=reason,
        pm_latency_ms=pm_result.latency_ms,
        ks_latency_ms=ks_result.latency_ms,
        snapshot_skew_seconds=skew,
    )
    return PairWindowResult(
        paired_contract_id=pair_id,
        pm_observation_id=pm_observation_id,
        ks_observation_id=ks_observation_id,
        edge_snapshot_id=edge_snapshot_id,
        status=status,
        reason=reason,
        pm_latency_ms=pm_result.latency_ms,
        ks_latency_ms=ks_result.latency_ms,
        snapshot_skew_seconds=skew,
    )


def collect_pairs(
    conn,
    pairs: list[Any],
    ks_depth: int,
    sleep_seconds: float,
    commit_every: int,
    *,
    compute_edges: bool,
    max_age_seconds: float,
    max_skew_seconds: float,
    dedupe_edges: bool,
) -> tuple[int, int, edge_builder.InsertStats]:
    pm_count = 0
    ks_count = 0
    edge_stats = edge_builder.InsertStats()
    for index, pair in enumerate(pairs, start=1):
        pm_observation_id, ks_observation_id = collect_pair(conn, pair, ks_depth, sleep_seconds)
        pm_count += 1
        ks_count += 1
        if compute_edges:
            edge_stats = edge_stats.plus(
                edge_builder.insert_edge_snapshot(
                    conn,
                    pair,
                    observation_by_id(conn, pm_observation_id),
                    observation_by_id(conn, ks_observation_id),
                    max_age_seconds=max_age_seconds,
                    max_skew_seconds=max_skew_seconds,
                    dedupe=dedupe_edges,
                )
            )
        if commit_every > 0 and index % commit_every == 0:
            conn.commit()
    conn.commit()
    return pm_count, ks_count, edge_stats


def select_window_pairs(conn, args: argparse.Namespace) -> tuple[list[Any], int]:
    all_pairs = selected_pairs(conn, requested_universes(args.universes), 0)
    if args.max_pairs > 0:
        return all_pairs[: args.max_pairs], len(all_pairs)
    return all_pairs, len(all_pairs)


def window_status(
    *,
    attempted_count: int,
    safe_pair_count: int,
    error_count: int,
    elapsed_seconds: float,
    target_interval_seconds: float,
) -> tuple[str, str]:
    missed_count = max(0, safe_pair_count - attempted_count)
    reasons = []
    if missed_count:
        reasons.append(f"missed={missed_count}")
    if error_count:
        reasons.append(f"errors={error_count}")
    if elapsed_seconds > target_interval_seconds:
        reasons.append(f"elapsed={elapsed_seconds:.3f}s > target={target_interval_seconds:.3f}s")
    if missed_count or error_count:
        return "partial", "; ".join(reasons)
    if elapsed_seconds > target_interval_seconds:
        return "late", "; ".join(reasons)
    return "ok", ""


def collect_snapshot_window(conn, args: argparse.Namespace) -> WindowStats:
    window_started_mono = time.monotonic()
    started_ts = old_market_db.utc_now()
    pairs, safe_pair_count = select_window_pairs(conn, args)
    snapshot_window_id = old_market_db.create_snapshot_window(
        conn,
        started_ts_utc=started_ts,
        target_interval_seconds=args.interval,
        safe_pair_count=safe_pair_count,
    )
    conn.commit()

    fetch_results = fetch_window_orderbooks(
        pairs,
        ks_depth=args.ks_depth,
        pair_workers=args.pair_workers,
        request_timeout=args.request_timeout,
        pair_start_interval_ms=args.pair_start_interval_ms,
    )
    pair_results: list[PairWindowResult] = []
    for pair in pairs:
        pair_id = int(pair["paired_contract_id"])
        pm_result = fetch_results.get((pair_id, "pm"))
        ks_result = fetch_results.get((pair_id, "ks"))
        if pm_result is None:
            pm_result = FetchResult(
                "pm",
                str(pair["pm_token_id"]),
                None,
                old_market_db.utc_now(),
                "/book",
                None,
                0,
                "missing_pm_fetch_result",
            )
        if ks_result is None:
            ks_result = FetchResult(
                "ks",
                str(pair["ks_market_ticker"]),
                None,
                old_market_db.utc_now(),
                f"/markets/{pair['ks_market_ticker']}/orderbook",
                args.ks_depth,
                0,
                "missing_ks_fetch_result",
            )
        pair_results.append(
            classify_and_record_pair_result(
                conn,
                snapshot_window_id=snapshot_window_id,
                pair=pair,
                pm_result=pm_result,
                ks_result=ks_result,
                compute_edges=args.compute_edges,
                max_age_seconds=args.max_age_seconds,
                max_skew_seconds=args.max_skew_seconds,
                dedupe_edges=not args.no_edge_dedupe,
            )
        )

    attempted_count = len(pairs)
    completed_count = sum(1 for result in pair_results if result.status == "ok")
    edge_inserted_count = sum(1 for result in pair_results if result.edge_snapshot_id is not None)
    error_count = sum(1 for result in pair_results if result.status != "ok")
    missed_count = max(0, safe_pair_count - attempted_count)
    elapsed_seconds = time.monotonic() - window_started_mono
    status, reason = window_status(
        attempted_count=attempted_count,
        safe_pair_count=safe_pair_count,
        error_count=error_count,
        elapsed_seconds=elapsed_seconds,
        target_interval_seconds=args.interval,
    )
    old_market_db.update_snapshot_window(
        conn,
        snapshot_window_id=snapshot_window_id,
        finished_ts_utc=old_market_db.utc_now(),
        attempted_count=attempted_count,
        completed_count=completed_count,
        edge_inserted_count=edge_inserted_count,
        error_count=error_count,
        missed_count=missed_count,
        elapsed_seconds=elapsed_seconds,
        status=status,
        reason=reason,
    )
    conn.commit()
    return WindowStats(
        snapshot_window_id=snapshot_window_id,
        safe_pair_count=safe_pair_count,
        attempted_count=attempted_count,
        completed_count=completed_count,
        edge_inserted_count=edge_inserted_count,
        error_count=error_count,
        missed_count=missed_count,
        elapsed_seconds=elapsed_seconds,
        status=status,
        reason=reason,
    )


def format_float(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}".rstrip("0").rstrip(".")


def universal_snapshot_rows(conn, stats: WindowStats) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            pair.universe,
            pair.category,
            pair.match_name,
            pair.event_date,
            pair.canonical_event_id,
            pair.market_type,
            pair.pm_yes_outcome,
            pair.ks_yes_outcome,
            pair.pm_event_slug,
            pair.pm_market_id,
            pair.pm_token_id,
            pair.ks_event_ticker,
            pair.ks_market_ticker,
            pair.match_format,
            pair.schedule_source,
            res.paired_contract_id,
            res.pm_observation_id,
            res.ks_observation_id,
            res.edge_snapshot_id,
            res.status AS pair_status,
            res.reason AS pair_reason,
            res.pm_latency_ms,
            res.ks_latency_ms,
            res.snapshot_skew_seconds AS result_snapshot_skew_seconds,
            pm_obs.best_bid_scaled AS pm_bid_scaled,
            pm_obs.best_ask_scaled AS pm_ask_scaled,
            pm_obs.best_bid_size_scaled AS pm_bid_size_scaled,
            pm_obs.best_ask_size_scaled AS pm_ask_size_scaled,
            ks_obs.best_bid_scaled AS ks_bid_scaled,
            ks_obs.best_ask_scaled AS ks_ask_scaled,
            ks_obs.best_bid_size_scaled AS ks_bid_size_scaled,
            ks_obs.best_ask_size_scaled AS ks_ask_size_scaled,
            edge.ts_utc AS edge_ts_utc,
            edge.best_leg,
            edge.gross_cost_scaled,
            edge.net_edge_scaled,
            edge.best_leg_bbo_size_scaled,
            edge.net_profit_at_bbo_scaled,
            edge.alert,
            edge.alert_reason,
            edge.snapshot_skew_seconds AS edge_snapshot_skew_seconds
        FROM snapshot_pair_results res
        JOIN paired_contracts pair ON pair.paired_contract_id = res.paired_contract_id
        LEFT JOIN edge_snapshots edge ON edge.edge_snapshot_id = res.edge_snapshot_id
        LEFT JOIN orderbook_observations pm_obs ON pm_obs.observation_id = res.pm_observation_id
        LEFT JOIN orderbook_observations ks_obs ON ks_obs.observation_id = res.ks_observation_id
        WHERE res.snapshot_window_id = ?
        ORDER BY pair.universe, pair.event_date, pair.match_name, pair.pm_yes_outcome
        """,
        (stats.snapshot_window_id,),
    ).fetchall()
    output_rows: list[dict[str, Any]] = []
    for row in rows:
        snapshot_skew = (
            row["edge_snapshot_skew_seconds"]
            if row["edge_snapshot_skew_seconds"] is not None
            else row["result_snapshot_skew_seconds"]
        )
        output_rows.append(
            {
                "ts_utc": row["edge_ts_utc"] or "",
                "universe": row["universe"],
                "category": row["category"],
                "match_name": row["match_name"],
                "event_date": row["event_date"],
                "canonical_event_id": row["canonical_event_id"],
                "market_type": row["market_type"],
                "pm_yes_outcome": row["pm_yes_outcome"],
                "ks_yes_outcome": row["ks_yes_outcome"],
                "pm_bid": old_market_db.scaled_to_text(row["pm_bid_scaled"]),
                "pm_ask": old_market_db.scaled_to_text(row["pm_ask_scaled"]),
                "pm_bid_sz": old_market_db.scaled_to_text(row["pm_bid_size_scaled"]),
                "pm_ask_sz": old_market_db.scaled_to_text(row["pm_ask_size_scaled"]),
                "ks_bid": old_market_db.scaled_to_text(row["ks_bid_scaled"]),
                "ks_ask": old_market_db.scaled_to_text(row["ks_ask_scaled"]),
                "ks_bid_sz": old_market_db.scaled_to_text(row["ks_bid_size_scaled"]),
                "ks_ask_sz": old_market_db.scaled_to_text(row["ks_ask_size_scaled"]),
                "alert": row["alert"] or "",
                "alert_threshold": core.ALERT_THRESHOLD,
                "alert_reason": row["alert_reason"] or "",
                "net_edge": old_market_db.scaled_to_text(row["net_edge_scaled"]),
                "best_leg": row["best_leg"] or "",
                "gross_cost": old_market_db.scaled_to_text(row["gross_cost_scaled"]),
                "best_leg_bbo_size": old_market_db.scaled_to_text(row["best_leg_bbo_size_scaled"]),
                "net_profit_at_bbo": old_market_db.scaled_to_text(row["net_profit_at_bbo_scaled"]),
                "pm_event_slug": row["pm_event_slug"],
                "pm_market_id": row["pm_market_id"],
                "pm_token_id": row["pm_token_id"],
                "ks_event_ticker": row["ks_event_ticker"],
                "ks_market_ticker": row["ks_market_ticker"],
                "match_format": row["match_format"] or "",
                "schedule_source": row["schedule_source"] or "",
                "snapshot_window_id": stats.snapshot_window_id,
                "paired_contract_id": row["paired_contract_id"],
                "pair_status": row["pair_status"],
                "pair_reason": row["pair_reason"] or "",
                "pm_observation_id": row["pm_observation_id"] or "",
                "ks_observation_id": row["ks_observation_id"] or "",
                "edge_snapshot_id": row["edge_snapshot_id"] or "",
                "pm_latency_ms": row["pm_latency_ms"] if row["pm_latency_ms"] is not None else "",
                "ks_latency_ms": row["ks_latency_ms"] if row["ks_latency_ms"] is not None else "",
                "snapshot_skew_seconds": format_float(snapshot_skew),
                "safe_pair_count": stats.safe_pair_count,
                "attempted_count": stats.attempted_count,
                "completed_count": stats.completed_count,
                "error_count": stats.error_count,
                "missed_count": stats.missed_count,
                "window_elapsed_seconds": format_float(stats.elapsed_seconds),
                "window_status": stats.status,
                "window_reason": stats.reason,
            }
        )
    return output_rows


def write_snapshot_csv_outputs(conn, stats: WindowStats, args: argparse.Namespace) -> tuple[int, int]:
    rows = universal_snapshot_rows(conn, stats)
    positive_edges = core.alert_rows(rows)
    universal_output = Path(getattr(args, "universal_output", "data/old/old_latest_universal_snapshot.csv"))
    core.write_latest_csv(rows, UNIVERSAL_SNAPSHOT_FIELDS, universal_output)
    core.write_latest_csv(positive_edges, UNIVERSAL_SNAPSHOT_FIELDS, Path(args.output))
    core.write_latest_csv(positive_edges, UNIVERSAL_SNAPSHOT_FIELDS, Path(args.alert_output))
    core.write_alert_folder(positive_edges, UNIVERSAL_SNAPSHOT_FIELDS, Path(args.alert_dir))
    return len(rows), len(positive_edges)


def run_once(conn, args: argparse.Namespace) -> WindowStats:
    return collect_snapshot_window(conn, args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect fresh orderbooks pair-by-pair for DB safe paired contracts.")
    parser.add_argument("--db", default=str(old_market_db.DEFAULT_DB_PATH))
    parser.add_argument("--universes", default="all")
    parser.add_argument("--full-coverage", action="store_true", help="Document intent to attempt every safe pair in each window.")
    parser.add_argument("--max-pairs", type=int, default=0, help="0 means all safe pairs; positive values are smoke-test caps only.")
    parser.add_argument("--pair-workers", type=int, default=48)
    parser.add_argument(
        "--pair-start-interval-ms",
        type=int,
        default=30,
        help="Milliseconds to wait between starting each pair's synchronized PM/KS requests; reduces KS burst 429s.",
    )
    parser.add_argument("--request-timeout", type=float, default=3.0)
    parser.add_argument("--ks-depth", type=int, default=1)
    parser.add_argument("--sleep-between-legs", type=float, default=0.0)
    parser.add_argument("--commit-every", type=int, default=0)
    parser.add_argument(
        "--compute-edges",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Insert edge_snapshots from the exact PM/KS observations collected for each pair.",
    )
    parser.add_argument("--no-edge-dedupe", action="store_true")
    parser.add_argument(
        "--universal-output",
        default="data/old/old_latest_universal_snapshot.csv",
        help="Full latest snapshot with every attempted safe pair, including ok/error/missing-depth rows.",
    )
    parser.add_argument(
        "--output",
        default="data/old/old_latest_edges.csv",
        help="Human-facing latest positive-edge opportunities only.",
    )
    parser.add_argument("--alert-output", default="data/old/old_latest_alerts.csv")
    parser.add_argument("--alert-dir", default="data/old/old_alerts")
    parser.add_argument("--max-age-seconds", type=float, default=10.0)
    parser.add_argument("--max-skew-seconds", type=float, default=5.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--busy-timeout-ms", type=int, default=60000)
    parser.add_argument("--show-warnings", action="store_true")
    args = parser.parse_args()

    if args.max_pairs < 0:
        parser.error("--max-pairs must be >= 0")
    if args.pair_workers <= 0:
        parser.error("--pair-workers must be positive")
    if args.pair_start_interval_ms < 0:
        parser.error("--pair-start-interval-ms must be >= 0")
    if args.request_timeout <= 0:
        parser.error("--request-timeout must be positive")
    if args.ks_depth < 0:
        parser.error("--ks-depth must be >= 0")
    if args.sleep_between_legs < 0 or args.interval <= 0:
        parser.error("sleep/interval values must be non-negative and interval must be positive")
    if args.max_age_seconds <= 0 or args.max_skew_seconds <= 0:
        parser.error("age/skew windows must be positive")
    if args.busy_timeout_ms <= 0:
        parser.error("--busy-timeout-ms must be positive")

    iteration = 1
    with old_market_db.connect(Path(args.db)) as conn:
        old_market_db.init_db(conn)
        conn.execute(f"PRAGMA busy_timeout = {args.busy_timeout_ms}")
        edge_builder.ensure_builder_indexes(conn)
        while True:
            started = time.monotonic()
            stats = run_once(conn, args)
            universal_rows, positive_edge_rows = write_snapshot_csv_outputs(conn, stats, args)
            print(
                "full-coverage paired orderbook window complete: "
                f"iteration={iteration}; window_id={stats.snapshot_window_id}; "
                f"safe_pairs={stats.safe_pair_count}; attempted={stats.attempted_count}; "
                f"completed={stats.completed_count}; edge_inserted={stats.edge_inserted_count}; "
                f"errors={stats.error_count}; missed={stats.missed_count}; "
                f"elapsed_sec={stats.elapsed_seconds:.3f}; status={stats.status}; reason={stats.reason}; "
                f"snapshot_rows={universal_rows}; positive_edges={positive_edge_rows}; "
                f"universal_output={args.universal_output}; output={args.output}; "
                f"alert_output={args.alert_output}",
                flush=True,
            )
            if not args.loop or (args.max_iterations and iteration >= args.max_iterations):
                break
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.interval - elapsed))
            iteration += 1


if __name__ == "__main__":
    main()
