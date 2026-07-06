#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import new_ws_orderbook_core as core


def import_websockets():
    try:
        import websockets  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: websockets. Install it with "
            "`python3 -m pip install websockets` before running the live collector."
        ) from exc
    return websockets


async def ws_connect(websockets: Any, url: str, headers: dict[str, str] | None = None, max_size: int = 16_777_216):
    kwargs = {"ping_interval": 20, "ping_timeout": 20, "close_timeout": 5, "max_size": max_size}
    if headers:
        try:
            return await websockets.connect(url, additional_headers=headers, **kwargs)
        except TypeError:
            return await websockets.connect(url, extra_headers=headers, **kwargs)
    return await websockets.connect(url, **kwargs)


@dataclass
class CollectorState:
    pm_ids: list[str] = field(default_factory=list)
    ks_ids: list[str] = field(default_factory=list)
    pm_books: dict[str, core.PriceBook] = field(default_factory=dict)
    ks_books: dict[str, core.KalshiPriceBook] = field(default_factory=dict)
    pm_last_error: str = ""
    ks_last_error: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def set_ids(self, pm_ids: list[str], ks_ids: list[str]) -> None:
        async with self.lock:
            self.pm_ids = pm_ids
            self.ks_ids = ks_ids

    async def get_pm_ids(self) -> list[str]:
        async with self.lock:
            return list(self.pm_ids)

    async def get_ks_ids(self) -> list[str]:
        async with self.lock:
            return list(self.ks_ids)

    async def set_error(self, venue: str, message: str) -> None:
        async with self.lock:
            if venue == "pm":
                self.pm_last_error = message[:500]
            else:
                self.ks_last_error = message[:500]

    async def get_errors(self) -> tuple[str, str]:
        async with self.lock:
            return self.pm_last_error, self.ks_last_error


def refresh_metadata_and_ids(
    db_path: Path,
    args: argparse.Namespace,
    selected_sports: tuple[str, ...] | None,
) -> tuple[list[str], list[str]]:
    with core.connect_db(db_path) as conn:
        if not args.skip_metadata_refresh:
            try:
                core.discover_pm_sports(
                    conn,
                    page_limit=args.pm_page_limit,
                    max_pages=args.max_pm_pages,
                    selected_sports=selected_sports,
                )
            except Exception as exc:  # noqa: BLE001 - continue with cached metadata during rate limits/outages.
                print(f"{core.utc_now()} metadata warning: PM discovery failed; using cached DB metadata: {exc}", flush=True)
            try:
                core.discover_ks_sports(
                    conn,
                    page_limit=args.ks_page_limit,
                    max_pages=args.max_ks_pages,
                    selected_sports=selected_sports,
                )
            except Exception as exc:  # noqa: BLE001 - continue with cached metadata during rate limits/outages.
                print(f"{core.utc_now()} metadata warning: KS discovery failed; using cached DB metadata: {exc}", flush=True)
        pm_ids = core.active_pm_token_ids(conn, selected_sports)
        ks_ids = core.active_ks_market_tickers(conn, selected_sports)
    if not pm_ids or not ks_ids:
        raise RuntimeError(f"missing metadata after refresh/cache lookup: pm_ids={len(pm_ids)} ks_ids={len(ks_ids)}")
    return pm_ids, ks_ids


def load_cached_ids(db_path: Path, selected_sports: tuple[str, ...] | None) -> tuple[list[str], list[str]]:
    with core.connect_db(db_path) as conn:
        pm_ids = core.active_pm_token_ids(conn, selected_sports)
        ks_ids = core.active_ks_market_tickers(conn, selected_sports)
    if not pm_ids or not ks_ids:
        raise RuntimeError(f"missing cached metadata: pm_ids={len(pm_ids)} ks_ids={len(ks_ids)}")
    return pm_ids, ks_ids


def start_keep_awake() -> subprocess.Popen[Any] | None:
    caffeinate = Path("/usr/bin/caffeinate")
    if not caffeinate.exists():
        return None
    try:
        return subprocess.Popen(  # noqa: S603 - local macOS utility, fixed argv.
            [str(caffeinate), "-dimsu", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001 - keep collector alive even if macOS assertion fails.
        print(f"{core.utc_now()} keep-awake warning: {exc}", flush=True)
        return None


def reconnect_delay(args: argparse.Namespace, failures: int) -> float:
    base = max(args.reconnect_seconds, 0.1)
    delay = min(args.reconnect_max_seconds, base * (2 ** min(max(failures - 1, 0), 5)))
    if args.reconnect_jitter_seconds > 0:
        delay += random.uniform(0, args.reconnect_jitter_seconds)
    return delay


async def metadata_loop(db_path: Path, state: CollectorState, args: argparse.Namespace, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            selected_sports = core.normalize_sports_arg(args.sports)
            pm_ids, ks_ids = await asyncio.to_thread(refresh_metadata_and_ids, db_path, args, selected_sports)
            await state.set_ids(pm_ids, ks_ids)
            print(
                f"{core.utc_now()} metadata ready; pm_tokens={len(pm_ids)}; ks_tickers={len(ks_ids)}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - long-running collector reports and retries.
            await state.set_error("pm", f"metadata_refresh_failed:{exc}")
            await state.set_error("ks", f"metadata_refresh_failed:{exc}")
            print(f"{core.utc_now()} metadata warning: {exc}", flush=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=args.metadata_refresh_seconds)
        except asyncio.TimeoutError:
            continue


async def pm_worker(
    *,
    websockets: Any,
    asset_ids: list[str],
    state: CollectorState,
    args: argparse.Namespace,
    stop_event: asyncio.Event,
    startup_delay: float = 0.0,
) -> None:
    for asset_id in asset_ids:
        state.pm_books.setdefault(asset_id, core.PriceBook()).reset()
    if startup_delay > 0:
        await asyncio.sleep(startup_delay)
    failures = 0
    while not stop_event.is_set():
        try:
            ws = await ws_connect(websockets, args.pm_ws_url, max_size=args.ws_max_size_bytes)
            try:
                await ws.send(
                    json.dumps(
                        {
                            "assets_ids": asset_ids,
                            "type": "market",
                            "custom_feature_enabled": True,
                        }
                    )
                )
                failures = 0
                await state.set_error("pm", "")
                async for raw in ws:
                    core.handle_pm_message(state.pm_books, json.loads(raw))
                    if stop_event.is_set():
                        break
            finally:
                await ws.close()
            if not stop_event.is_set():
                await asyncio.sleep(args.reconnect_seconds)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            await state.set_error("pm", f"pm_ws_error:{exc}")
            for asset_id in asset_ids:
                state.pm_books.setdefault(asset_id, core.PriceBook()).reset()
            await asyncio.sleep(reconnect_delay(args, failures))


async def ks_worker(
    *,
    websockets: Any,
    market_tickers: list[str],
    state: CollectorState,
    args: argparse.Namespace,
    stop_event: asyncio.Event,
    startup_delay: float = 0.0,
) -> None:
    for ticker in market_tickers:
        state.ks_books.setdefault(ticker, core.KalshiPriceBook()).reset()
    if startup_delay > 0:
        await asyncio.sleep(startup_delay)
    failures = 0
    while not stop_event.is_set():
        try:
            headers = core.kalshi_auth_headers("/trade-api/ws/v2")
            ws = await ws_connect(websockets, args.ks_ws_url, headers, max_size=args.ws_max_size_bytes)
            try:
                params: dict[str, Any] = {"channels": ["orderbook_delta"]}
                if len(market_tickers) == 1:
                    params["market_ticker"] = market_tickers[0]
                else:
                    params["market_tickers"] = market_tickers
                await ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": params}))
                failures = 0
                await state.set_error("ks", "")
                async for raw in ws:
                    core.handle_ks_message(state.ks_books, json.loads(raw))
                    if stop_event.is_set():
                        break
            finally:
                await ws.close()
            if not stop_event.is_set():
                await asyncio.sleep(args.reconnect_seconds)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            await state.set_error("ks", f"ks_ws_error:{exc}")
            for ticker in market_tickers:
                state.ks_books.setdefault(ticker, core.KalshiPriceBook()).reset()
            await asyncio.sleep(reconnect_delay(args, failures))


async def subscription_supervisor(
    *,
    name: str,
    get_ids: Callable[[], Awaitable[list[str]]],
    chunk_size: int,
    make_worker: Callable[[list[str], float], asyncio.Task[Any]],
    start_stagger_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    signature: tuple[str, ...] = ()
    tasks: list[asyncio.Task[Any]] = []
    while not stop_event.is_set():
        ids = await get_ids()
        new_signature = tuple(ids)
        if new_signature != signature:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            chunks = [chunk for chunk in core.chunks(ids, chunk_size) if chunk]
            tasks = [make_worker(chunk, index * start_stagger_seconds) for index, chunk in enumerate(chunks)]
            signature = new_signature
            print(f"{core.utc_now()} {name} subscriptions reset; instruments={len(ids)}; tasks={len(tasks)}", flush=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            continue
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def snapshot_loop(db_path: Path, state: CollectorState, args: argparse.Namespace, stop_event: asyncio.Event) -> None:
    with core.connect_db(db_path) as conn:
        while not stop_event.is_set():
            pm_ids = await state.get_pm_ids()
            ks_ids = await state.get_ks_ids()
            batch_ts = core.utc_now()
            pm_stats = core.flush_books(
                conn,
                venue="pm",
                instrument_ids=pm_ids,
                books=state.pm_books,
                depth=args.depth,
                max_stale_seconds=args.max_stale_seconds,
                request_path="wss://polymarket/market",
                write_stale_errors=args.write_stale_errors,
                write_payloads=args.write_payloads,
                collected_ts_utc=batch_ts,
            )
            ks_stats = core.flush_books(
                conn,
                venue="ks",
                instrument_ids=ks_ids,
                books=state.ks_books,
                depth=args.depth,
                max_stale_seconds=args.max_stale_seconds,
                request_path="wss://kalshi/orderbook_delta",
                write_stale_errors=args.write_stale_errors,
                write_payloads=args.write_payloads,
                collected_ts_utc=batch_ts,
            )
            pm_error, ks_error = await state.get_errors()
            core.write_status_csv(
                Path(args.status_output),
                [
                    {
                        "ts_utc": batch_ts,
                        "venue": "pm",
                        "subscribed": pm_stats.subscribed,
                        "ok": pm_stats.ok,
                        "fresh": pm_stats.fresh,
                        "stale": pm_stats.stale,
                        "missing": pm_stats.missing,
                        "last_error": pm_error,
                    },
                    {
                        "ts_utc": batch_ts,
                        "venue": "ks",
                        "subscribed": ks_stats.subscribed,
                        "ok": ks_stats.ok,
                        "fresh": ks_stats.fresh,
                        "stale": ks_stats.stale,
                        "missing": ks_stats.missing,
                        "last_error": ks_error,
                    },
                ],
            )
            print(
                f"{batch_ts} snapshot; pm_ok={pm_stats.ok}/{pm_stats.subscribed}; "
                f"ks_ok={ks_stats.ok}/{ks_stats.subscribed}; "
                f"pm_fresh={pm_stats.fresh}; ks_fresh={ks_stats.fresh}; "
                f"pm_stale={pm_stats.stale}; ks_stale={ks_stats.stale}",
                flush=True,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=args.snapshot_interval)
            except asyncio.TimeoutError:
                continue


async def async_main(args: argparse.Namespace) -> None:
    try:
        selected_sports = core.normalize_sports_arg(args.sports)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        db_path = core.ensure_ws_db_path(args.db)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.snapshot_interval <= 0 or args.metadata_refresh_seconds <= 0:
        raise SystemExit("interval values must be positive.")
    if args.depth <= 0:
        raise SystemExit("--depth must be positive.")
    if args.pm_chunk_size <= 0 or args.ks_chunk_size <= 0:
        raise SystemExit("chunk sizes must be positive.")

    websockets = import_websockets()
    state = CollectorState()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, signame), stop_event.set)
        except NotImplementedError:
            pass

    pm_ids, ks_ids = await asyncio.to_thread(load_cached_ids, db_path, selected_sports)
    if len(pm_ids) + len(ks_ids) > 10_000:
        if args.pm_chunk_size < 2000:
            print(f"{core.utc_now()} tuning: pm_chunk_size {args.pm_chunk_size} -> 2000 for selected sports WS", flush=True)
            args.pm_chunk_size = 2000
        if args.subscription_start_stagger_seconds < 1.0:
            print(
                f"{core.utc_now()} tuning: subscription_start_stagger_seconds "
                f"{args.subscription_start_stagger_seconds} -> 1.0 for selected sports WS",
                flush=True,
            )
            args.subscription_start_stagger_seconds = 1.0
    await state.set_ids(pm_ids, ks_ids)
    sports_label = "all" if selected_sports is None else ",".join(selected_sports)
    print(
        f"{core.utc_now()} cached metadata ready; sports={sports_label}; "
        f"pm_tokens={len(pm_ids)}; ks_tickers={len(ks_ids)}",
        flush=True,
    )
    keep_awake_process = None if args.no_keep_awake else start_keep_awake()
    if keep_awake_process is not None:
        print(f"{core.utc_now()} keep-awake active; caffeinate_pid={keep_awake_process.pid}", flush=True)

    tasks = [
        asyncio.create_task(metadata_loop(db_path, state, args, stop_event)),
        asyncio.create_task(
            subscription_supervisor(
                name="pm",
                get_ids=state.get_pm_ids,
                chunk_size=args.pm_chunk_size,
                start_stagger_seconds=args.subscription_start_stagger_seconds,
                make_worker=lambda chunk, delay: asyncio.create_task(
                    pm_worker(
                        websockets=websockets,
                        asset_ids=chunk,
                        state=state,
                        args=args,
                        stop_event=stop_event,
                        startup_delay=delay,
                    )
                ),
                stop_event=stop_event,
            )
        ),
        asyncio.create_task(
            subscription_supervisor(
                name="ks",
                get_ids=state.get_ks_ids,
                chunk_size=args.ks_chunk_size,
                start_stagger_seconds=args.subscription_start_stagger_seconds,
                make_worker=lambda chunk, delay: asyncio.create_task(
                    ks_worker(
                        websockets=websockets,
                        market_tickers=chunk,
                        state=state,
                        args=args,
                        stop_event=stop_event,
                        startup_delay=delay,
                    )
                ),
                stop_event=stop_event,
            )
        ),
        asyncio.create_task(snapshot_loop(db_path, state, args, stop_event)),
    ]
    try:
        while not stop_event.is_set():
            done = [task for task in tasks if task.done()]
            if done:
                for task in done:
                    if task.cancelled():
                        print(f"{core.utc_now()} fatal task cancellation", flush=True)
                        continue
                    try:
                        exc = task.exception()
                    except asyncio.CancelledError:
                        print(f"{core.utc_now()} fatal task cancellation", flush=True)
                        continue
                    if exc is None:
                        print(f"{core.utc_now()} fatal task exit without exception", flush=True)
                    else:
                        print(f"{core.utc_now()} fatal task failure: {exc}", flush=True)
                stop_event.set()
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if keep_awake_process is not None and keep_awake_process.poll() is None:
            keep_awake_process.terminate()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect PM/KS sports orderbooks via WebSocket and write top-4 depth snapshots.")
    parser.add_argument("--db", default=str(core.DEFAULT_WS_DB_PATH))
    parser.add_argument("--sports", default=core.DEFAULT_WS_SPORTS)
    parser.add_argument("--snapshot-interval", type=float, default=5.0)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--metadata-refresh-seconds", type=float, default=300.0)
    parser.add_argument("--status-output", default="data/new/new_latest_ws_status.csv")
    parser.add_argument("--max-stale-seconds", type=float, default=60.0)
    parser.add_argument("--pm-page-limit", type=int, default=200)
    parser.add_argument("--ks-page-limit", type=int, default=200)
    parser.add_argument("--max-pm-pages", type=int, default=0)
    parser.add_argument("--max-ks-pages", type=int, default=0)
    parser.add_argument("--skip-metadata-refresh", action="store_true")
    parser.add_argument("--write-stale-errors", action="store_true")
    parser.add_argument("--pm-chunk-size", type=int, default=250)
    parser.add_argument("--ks-chunk-size", type=int, default=100)
    parser.add_argument("--subscription-start-stagger-seconds", type=float, default=0.2)
    parser.add_argument("--ws-max-size-bytes", type=int, default=16_777_216)
    parser.add_argument("--reconnect-seconds", type=float, default=10.0)
    parser.add_argument("--reconnect-max-seconds", type=float, default=120.0)
    parser.add_argument("--reconnect-jitter-seconds", type=float, default=5.0)
    parser.add_argument("--write-payloads", action="store_true")
    parser.add_argument("--no-keep-awake", action="store_true")
    parser.add_argument("--pm-ws-url", default=core.PM_WS_URL)
    parser.add_argument("--ks-ws-url", default=core.KS_WS_URL)
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
