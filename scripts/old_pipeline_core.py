from __future__ import annotations

import csv
import concurrent.futures
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PM_GAMMA = "https://gamma-api.polymarket.com"
PM_CLOB = "https://clob.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

BINARY_CSV_FIELDS = [
    "ts_utc",
    "universe",
    "category",
    "match_name",
    "event_date",
    "canonical_event_id",
    "market_type",
    "pm_yes_outcome",
    "ks_yes_outcome",
    "pm_bid",
    "pm_ask",
    "pm_bid_sz",
    "pm_ask_sz",
    "ks_bid",
    "ks_ask",
    "ks_bid_sz",
    "ks_ask_sz",
    "alert",
    "alert_threshold",
    "alert_reason",
    "net_edge",
    "best_leg",
    "gross_cost",
    "best_leg_bbo_size",
    "net_profit_at_bbo",
    "pm_event_slug",
    "pm_market_id",
    "pm_token_id",
    "ks_event_ticker",
    "ks_market_ticker",
    "match_format",
    "schedule_source",
]
ALERT_THRESHOLD = 0.0
ALERT_FOLDER_CSV = "old_latest_opportunities.csv"
ALERT_FOLDER_SUMMARY = "old_latest_opportunities.txt"
ALERT_ACTIVE_MARKER = "old_ALERT_ACTIVE.txt"
ALERT_CLEAR_MARKER = "old_NO_CURRENT_ALERTS.txt"

SOCCER_COMPAT_CSV_FIELDS = [
    "ts_utc",
    "source",
    "universe",
    "event_id",
    "title",
    "event_date",
    "market_count",
    "outcomes",
    "is_binary_candidate",
    "skip_binary_arb",
    "reason",
]


@dataclass(frozen=True)
class BBO:
    bid: float | None
    bid_size: float | None
    ask: float | None
    ask_size: float | None


@dataclass(frozen=True)
class NetEdge:
    leg: str | None
    net_edge: float | None
    gross_cost: float | None


@dataclass(frozen=True)
class PMBinaryMarket:
    event_date: str
    event_slug: str
    event_title: str
    market_id: str
    market_question: str
    outcome_a: str
    outcome_b: str
    token_a: str
    token_b: str
    match_format: str = ""


@dataclass(frozen=True)
class KSMarket:
    ticker: str
    yes_outcome: str
    yes_bid: str | None
    yes_ask: str | None
    close_time: str


@dataclass(frozen=True)
class KSEvent:
    universe: str
    event_ticker: str
    title: str
    event_date: str
    team_a: str
    team_b: str
    match_format: str
    markets: list[KSMarket]


@dataclass(frozen=True)
class PairedContract:
    universe: str
    category: str
    match_name: str
    event_date: str
    canonical_event_id: str
    market_type: str
    pm_yes_outcome: str
    pm_event_slug: str
    pm_market_id: str
    pm_token_id: str
    ks_yes_outcome: str
    ks_event_ticker: str
    ks_market_ticker: str
    match_format: str = ""
    schedule_source: str = ""


def get_json(base_url: str, path: str, params: dict[str, Any], timeout: int = 20) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{base_url}{path}?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "arb-research-pipeline/0.2"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(float(exc.headers.get("Retry-After") or 1.0 + attempt))
    raise RuntimeError("unreachable HTTP retry state")


def get_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "arb-research-pipeline/0.2"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(float(exc.headers.get("Retry-After") or 1.0 + attempt))
    raise RuntimeError("unreachable HTTP retry state")


def fetch_polymarket_events(
    tag_slugs: tuple[str, ...],
    limit: int,
    ascending_values: tuple[str, ...] = ("true", "false"),
) -> list[dict[str, Any]]:
    by_slug: dict[str, dict[str, Any]] = {}
    for tag_slug in tag_slugs:
        for ascending in ascending_values:
            events = get_json(
                PM_GAMMA,
                "/events",
                {
                    "tag_slug": tag_slug,
                    "closed": "false",
                    "limit": limit,
                    "order": "startDate",
                    "ascending": ascending,
                },
            )
            for event in events:
                slug = str(event.get("slug") or "")
                if slug:
                    by_slug.setdefault(slug, event)
    return list(by_slug.values())


def fetch_polymarket_events_by_tag_ids(
    tag_ids: tuple[int, ...],
    limit: int,
    ascending_values: tuple[str, ...] = ("true", "false"),
) -> list[dict[str, Any]]:
    by_slug: dict[str, dict[str, Any]] = {}
    for tag_id in tag_ids:
        for ascending in ascending_values:
            events = get_json(
                PM_GAMMA,
                "/events",
                {
                    "tag_id": tag_id,
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "order": "startDate",
                    "ascending": ascending,
                },
            )
            for event in events:
                slug = str(event.get("slug") or "")
                if slug:
                    by_slug.setdefault(slug, event)
    return list(by_slug.values())


def fetch_kalshi_series_markets(series_ticker: str, limit: int) -> list[dict[str, Any]]:
    return get_json(
        KALSHI_API,
        "/markets",
        {
            "series_ticker": series_ticker,
            "status": "open",
            "limit": min(max(limit * 2, 2), 200),
        },
    ).get("markets", [])


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def find_pm_binary_market(event: dict[str, Any]) -> dict[str, Any] | None:
    event_title = str(event.get("title") or "")
    for market in event.get("markets") or []:
        if not market.get("active") or market.get("closed"):
            continue
        if not market.get("enableOrderBook", True):
            continue
        if str(market.get("question") or "") != event_title:
            continue
        outcomes = parse_json_list(market.get("outcomes"))
        token_ids = parse_json_list(market.get("clobTokenIds"))
        if len(outcomes) != 2 or len(token_ids) != 2:
            continue
        if outcomes in (["Yes", "No"], ["Over", "Under"], ["Odd", "Even"]):
            continue
        return market
    return None


def fetch_pm_bbo(token_id: str) -> BBO:
    book = get_json(PM_CLOB, "/book", {"token_id": token_id})
    bids = parse_pm_price_levels(book.get("bids"))
    asks = parse_pm_price_levels(book.get("asks"))
    best_bid = max(bids, key=lambda level: level[0]) if bids else (None, None)
    best_ask = min(asks, key=lambda level: level[0]) if asks else (None, None)
    return BBO(best_bid[0], best_bid[1], best_ask[0], best_ask[1])


def fetch_ks_bbo(market_ticker: str) -> BBO:
    book = get_json(f"{KALSHI_API}", f"/markets/{market_ticker}/orderbook", {"depth": 10}).get("orderbook_fp", {})
    yes_levels = parse_ks_price_levels(book.get("yes_dollars"))
    no_levels = parse_ks_price_levels(book.get("no_dollars"))
    best_yes_bid = max(yes_levels, key=lambda level: level[0]) if yes_levels else (None, None)
    best_no_bid = max(no_levels, key=lambda level: level[0]) if no_levels else (None, None)
    ask = round(1.0 - best_no_bid[0], 4) if best_no_bid[0] is not None else None
    return BBO(best_yes_bid[0], best_yes_bid[1], ask, best_no_bid[1])


def parse_pm_price_levels(levels: Any) -> list[tuple[float, float]]:
    parsed = []
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        if not isinstance(level, dict):
            continue
        try:
            parsed.append((float(level["price"]), float(level["size"])))
        except (KeyError, TypeError, ValueError):
            continue
    return parsed


def parse_ks_price_levels(levels: Any) -> list[tuple[float, float]]:
    parsed = []
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        try:
            parsed.append((float(level[0]), float(level[1])))
        except (TypeError, ValueError):
            continue
    return parsed


def pm_fee(price: float) -> float:
    return 0.03 * price * (1.0 - price)


def ks_fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def best_net(pm_bid: float | None, pm_ask: float | None, ks_bid: float | None, ks_ask: float | None) -> NetEdge:
    if pm_bid is None or pm_ask is None or ks_bid is None or ks_ask is None:
        return NetEdge(None, None, None)
    cost_a = pm_ask + (1.0 - ks_bid)
    net_a = 1.0 - cost_a - pm_fee(pm_ask) - ks_fee(1.0 - ks_bid)
    cost_b = (1.0 - pm_bid) + ks_ask
    net_b = 1.0 - cost_b - pm_fee(1.0 - pm_bid) - ks_fee(ks_ask)
    if net_a >= net_b:
        return NetEdge("PM_YES_KS_NO", round(net_a, 6), round(cost_a, 6))
    return NetEdge("PM_NO_KS_YES", round(net_b, 6), round(cost_b, 6))


def best_leg_bbo_size(best_leg: str | None, pm_bbo: BBO, ks_bbo: BBO) -> float | None:
    if best_leg == "PM_YES_KS_NO":
        return min_optional(pm_bbo.ask_size, ks_bbo.bid_size)
    if best_leg == "PM_NO_KS_YES":
        return min_optional(pm_bbo.bid_size, ks_bbo.ask_size)
    return None


def min_optional(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return min(left, right)


def build_binary_rows(pairs: list[PairedContract], max_workers: int = 12) -> tuple[list[dict[str, Any]], list[str]]:
    ts_utc = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    pm_tokens = sorted({pair.pm_token_id for pair in pairs if pair.pm_token_id})
    ks_tickers = sorted({pair.ks_market_ticker for pair in pairs if pair.ks_market_ticker})
    pm_cache, pm_warnings = fetch_bbo_map(pm_tokens, fetch_pm_bbo, "PM", max_workers)
    ks_cache, ks_warnings = fetch_bbo_map(ks_tickers, fetch_ks_bbo, "KS", max_workers)
    warnings.extend(pm_warnings)
    warnings.extend(ks_warnings)
    for pair in pairs:
        pm_bbo = pm_cache.get(pair.pm_token_id)
        ks_bbo = ks_cache.get(pair.ks_market_ticker)
        if pm_bbo is None or ks_bbo is None:
            warnings.append(
                f"BBO fetch missing: {pair.universe} {pair.canonical_event_id} "
                f"PM={pair.pm_event_slug}/{pair.pm_token_id} KS={pair.ks_market_ticker}"
            )
            continue
        if None in (pm_bbo.bid, pm_bbo.ask, ks_bbo.bid, ks_bbo.ask):
            warnings.append(
                f"incomplete BBO skipped: {pair.universe} {pair.canonical_event_id} "
                f"PM={pair.pm_event_slug}/{pair.pm_token_id} KS={pair.ks_market_ticker}"
            )
            continue
        edge = best_net(pm_bbo.bid, pm_bbo.ask, ks_bbo.bid, ks_bbo.ask)
        bbo_size = best_leg_bbo_size(edge.leg, pm_bbo, ks_bbo)
        net_profit_at_bbo = round(edge.net_edge * bbo_size, 6) if edge.net_edge is not None and bbo_size is not None else None
        alert = is_alert_edge(edge.net_edge)
        rows.append(
            {
                "ts_utc": ts_utc,
                "universe": pair.universe,
                "category": pair.category,
                "match_name": pair.match_name,
                "event_date": pair.event_date,
                "canonical_event_id": pair.canonical_event_id,
                "market_type": pair.market_type,
                "pm_yes_outcome": pair.pm_yes_outcome,
                "ks_yes_outcome": pair.ks_yes_outcome,
                "pm_bid": pm_bbo.bid,
                "pm_ask": pm_bbo.ask,
                "pm_bid_sz": pm_bbo.bid_size,
                "pm_ask_sz": pm_bbo.ask_size,
                "ks_bid": ks_bbo.bid,
                "ks_ask": ks_bbo.ask,
                "ks_bid_sz": ks_bbo.bid_size,
                "ks_ask_sz": ks_bbo.ask_size,
                "alert": "ALERT" if alert else "",
                "alert_threshold": ALERT_THRESHOLD,
                "alert_reason": "net_edge_positive" if alert else "",
                "net_edge": edge.net_edge,
                "best_leg": edge.leg,
                "gross_cost": edge.gross_cost,
                "best_leg_bbo_size": bbo_size,
                "net_profit_at_bbo": net_profit_at_bbo,
                "pm_event_slug": pair.pm_event_slug,
                "pm_market_id": pair.pm_market_id,
                "pm_token_id": pair.pm_token_id,
                "ks_event_ticker": pair.ks_event_ticker,
                "ks_market_ticker": pair.ks_market_ticker,
                "match_format": pair.match_format,
                "schedule_source": pair.schedule_source,
            }
        )
    return rows, warnings


def is_alert_edge(net_edge: float | None, threshold: float = ALERT_THRESHOLD) -> bool:
    return net_edge is not None and net_edge > threshold


def alert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("alert") == "ALERT"]


def write_alert_folder(alerts: list[dict[str, Any]], fieldnames: list[str], alert_dir: Path) -> None:
    alert_dir.mkdir(parents=True, exist_ok=True)
    ts_utc = datetime.now(timezone.utc).isoformat()
    write_latest_csv(alerts, fieldnames, alert_dir / ALERT_FOLDER_CSV)
    (alert_dir / ALERT_FOLDER_SUMMARY).write_text(format_alert_summary(alerts, ts_utc), encoding="utf-8")

    active_marker = alert_dir / ALERT_ACTIVE_MARKER
    clear_marker = alert_dir / ALERT_CLEAR_MARKER
    if alerts:
        active_marker.write_text(format_alert_summary(alerts, ts_utc), encoding="utf-8")
        if clear_marker.exists():
            clear_marker.unlink()
    else:
        clear_marker.write_text(f"{ts_utc}\nNo current positive-edge arbitrage opportunities.\n", encoding="utf-8")
        if active_marker.exists():
            active_marker.unlink()


def format_alert_summary(alerts: list[dict[str, Any]], ts_utc: str) -> str:
    if not alerts:
        return f"{ts_utc}\nNo current positive-edge arbitrage opportunities.\n"

    lines = [
        f"{ts_utc}",
        f"ALERT: {len(alerts)} positive-edge arbitrage opportunities",
        "",
    ]
    sorted_alerts = sorted(alerts, key=lambda row: float(row.get("net_edge") or 0), reverse=True)
    for index, row in enumerate(sorted_alerts, start=1):
        edge = float(row.get("net_edge") or 0)
        lines.extend(
            [
                f"{index}. {row.get('universe')} | {row.get('match_name')} | {row.get('event_date')}",
                f"   market_type={row.get('market_type')} outcome={row.get('pm_yes_outcome')} / {row.get('ks_yes_outcome')}",
                f"   net_edge={edge:.6f} ({edge * 100:.3f}%) best_leg={row.get('best_leg')} gross_cost={row.get('gross_cost')}",
                f"   bbo_size={row.get('best_leg_bbo_size')} net_profit_at_bbo={row.get('net_profit_at_bbo')}",
                f"   PM event={row.get('pm_event_slug')} market={row.get('pm_market_id')} token={row.get('pm_token_id')}",
                f"   KS event={row.get('ks_event_ticker')} market={row.get('ks_market_ticker')}",
                "",
            ]
        )
    return "\n".join(lines)


def fetch_bbo_map(
    identifiers: list[str],
    fetcher,
    label: str,
    max_workers: int,
) -> tuple[dict[str, BBO], list[str]]:
    if not identifiers:
        return {}, []
    workers = min(max(max_workers, 1), len(identifiers))
    results: dict[str, BBO] = {}
    warnings: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_by_id = {executor.submit(fetcher, identifier): identifier for identifier in identifiers}
        for future in concurrent.futures.as_completed(future_by_id):
            identifier = future_by_id[future]
            try:
                results[identifier] = future.result()
            except Exception as exc:  # noqa: BLE001 - keep overnight collector alive on per-book failures.
                warnings.append(f"{label} BBO fetch failed: {identifier}: {exc}")
    return results, warnings


def write_latest_csv(rows: list[dict[str, Any]], fieldnames: list[str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def append_history_csv(rows: list[dict[str, Any]], fieldnames: list[str], history_path: Path) -> Path:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not history_path.exists() or history_path.stat().st_size == 0
    with history_path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    return history_path
