# Codex Project Guide

## Definitions

- `PM` means Polymarket.
- `KS` means Kalshi.
- `BBO` means best bid and offer: the visible best bid and best ask from an order book.

## Pipeline Boundaries

There are three independent data stores. Keep their code paths and SQLite files separate.

- Legacy HTTP/API pipeline:
  - Uses HTTP/API-call based collection and pairing logic.
  - Writes only to `data/old/old_arb_research.sqlite`.
  - Primary collector entrypoint: `scripts/old_staging_run_market_db_loop.py`.
  - Legacy helpers include `scripts/old_market_db.py`, `scripts/old_staging_collect_market_db.py`, `scripts/old_staging_normalize_market_db.py`, `scripts/old_staging_pair_from_db.py`, `scripts/old_staging_collect_paired_orderbooks.py`, and downstream staging report/simulator scripts.

- New WebSocket pipeline:
  - Uses WebSocket-based orderbook collection and snapshot writing.
  - Writes only to `data/new/new_arb_research.sqlite`.
  - Primary collector entrypoint: `scripts/new_run_ws_orderbook_collector.py`.
  - New helpers include `scripts/new_ws_orderbook_core.py`, `scripts/new_bootstrap_ws_database.py`, and the `scripts/*ws_orderbook*.sh` service/tmux wrappers.

- Index pipeline:
  - Uses official or vetted schedule sources to create local real-world event records.
  - Writes only to `data/index/index_event_index.sqlite`.
  - Primary sync entrypoint: `scripts/index_sync_event_index.py`.
  - Index helpers include `scripts/index_event_index_db.py`, `scripts/index_event_index_sources.py`, and `scripts/index_event_index_core.py`.
  - Index scripts must not create canonical events from PM or KS market names.

No script should write to more than one database. If a workflow needs data from multiple generations, export read-only artifacts first and keep writes isolated.

## Current Sports Taxonomy

The new WebSocket collector targets these top-level sports only:

- `tennis`
- `esports`
- `baseball`

`valorant` is not a top-level sport. It belongs under `esports`. Esports should include `valorant`, `cs2`, and `lol` where those markets are available.

`baseball` includes MLB, KBO, generic baseball tags, and other recognizable baseball markets where available.

The legacy HTTP/staging registry may still contain older adapter categories such as `mlb`, `nba`, `world_cup`, and other inventory/reporting adapters. Do not use that legacy taxonomy to broaden the WebSocket default collector.

## Collector Scope

Both collectors are sports-only and should write only ordinary binary match/game winner markets:

- PM event/market rows must contain exactly two non-empty participant outcomes and exactly two CLOB token IDs.
- KS event groups must contain exactly two open participant outcome markets for the same `event_ticker`.
- Tennis player-vs-player and esports team-vs-team match winners are in scope when they are ordinary two-outcome winner markets.

Exclude options, perpetuals, futures/outrights, tournament/series/championship winners, spreads, handicaps, totals, props, over/under markets, draw/tie/3-way markets, map/set/period/inning markets, and malformed binary rows. Existing non-binary rows in a DB must also be filtered out before orderbook subscription or HTTP orderbook collection.

## Pairing Policy

Do not calculate edge from uncertain matches. PM events and KS events should map separately to local canonical event IDs before they are treated as pairable. Direct PM-name-to-KS-name string matching is not sufficient evidence.

When matching is ambiguous, stale, malformed, or only name-similar, write diagnostics or warnings only.

## Code Cleanup Policy

Before deleting code, classify it as one of:

- `legacy_http`
- `new_ws`
- `index_or_pipeline_helper`
- `reporting_tools`
- `obsolete_candidate`

Only delete an `obsolete_candidate` when all of these are true:

- It is not imported or invoked by current scripts, tests, service wrappers, or docs that define active workflows.
- It is not part of the legacy HTTP collector, new WebSocket collector, shared pairing/math/schema code, or reporting/simulator outputs still in use.
- A replacement path exists, or the code is a stale generated snippet/artifact.
- Relevant tests and smoke checks pass after deletion.

If any of those are unclear, keep the code and document the uncertainty instead of deleting it.

## Historical Context

`ONBOARDING.md` is useful historical context for objectives, risk model, and market structure. It is not absolute execution law. Prefer the explicit pipeline boundaries and current taxonomy in this file when they conflict with older onboarding text.
