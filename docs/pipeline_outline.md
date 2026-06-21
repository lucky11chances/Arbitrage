# Data Pipeline Outline

## Goal

Build a reusable cross-venue betting data pipeline that can monitor the same contract across Polymarket and Kalshi, capture orderbook snapshots, calculate candidate arbitrage signals, and preserve enough raw identifiers to debug bad pairings later.

The first implementation targets Week 1-2 sports data, but the shape should also work for politics, elections, entertainment, crypto, or any other betting domain.

## Core Flow

```text
discover markets
  -> normalize event contracts
  -> pair equivalent contracts across venues
  -> fetch orderbook snapshots
  -> compute fees / edge / liquidity signals
  -> write latest snapshot
  -> append history
  -> emit pairing and data-quality warnings
```

## Two-Layer Design

### 1. Generic Pipeline Layer

This should be domain-agnostic. It should not know what MLB, NBA, Valorant, or an election is.

Responsibilities:

- Fetch venue data through source clients.
- Accept normalized contracts from domain adapters.
- Pair contracts using adapter-provided canonical keys.
- Fetch PM CLOB and Kalshi orderbook BBO/depth.
- Calculate taker-only net edge for binary Yes/No contracts.
- Write latest CSV snapshots.
- Append daily history files.
- Print or persist warnings for unmatched, ambiguous, stale, or invalid markets.

Reusable outputs:

- `*_snapshot_latest.csv`
- `data/history/*_snapshot_YYYY-MM-DD.csv`
- pairing warning logs or warning rows

### 2. Domain Adapter Layer

Each sport or betting domain translates platform-specific events into a standard contract.

Examples:

- MLB adapter
- NBA adapter
- Esports adapter
- Soccer / World Cup adapter
- Polymarket sports inventory adapter
- Future politics adapter

The sports implementation now uses one adapter module per Polymarket Sports category. Inventory-only adapters write PM coverage rows only; paired adapters additionally produce `PairedContract` rows for the common binary arb engine.

Adapter responsibilities:

- Decide which venue events are in-scope.
- Parse titles, slugs, tickers, dates, teams, candidates, or outcomes.
- Map names to canonical entities.
- Define `market_type`, such as `game_winner`, `series_winner`, `election_winner`, or `nominee`.
- Detect invalid formats, such as 3-way soccer markets or BO3/BO5 mismatch.
- Build a canonical pairing key.

## Normalized Contract Shape

Minimum common fields:

```text
domain
subdomain
event_date
canonical_event_id
market_type
outcome_name
venue
venue_event_id
venue_market_id
venue_token_or_ticker
question
title
rules_or_resolution_source
close_time
status
```

Sports-specific fields:

```text
sport
league
team_a
team_b
team_a_id
team_b_id
game_id
match_format
home_away
official_schedule_source
```

Politics-specific fields later:

```text
country
state
office
election_date
candidate
party
resolution_source
```

## Pairing Strategy

Pairing must be stricter than discovery.

Preferred order:

1. Official schedule or official entity IDs when available.
2. Canonical date + normalized participant set + market type.
3. Explicit alias table for known naming differences.
4. Manual review for ambiguous or high-edge pairs.

Do not use broad fuzzy matching for production pairing. A missed pair is less dangerous than a false pair.

## Snapshot Types

### Polymarket Sports Inventory Snapshot

Used for all Polymarket Sports categories, including categories that are not yet safe to pair with Kalshi.

Fields include:

```text
category_key
category_label
tag_slug
event_slug
event_title
market_id
market_question
market_type_guess
outcomes
arb_universe
arb_enabled
kalshi_series_ticker
pairing_status
schedule_source
risk_level
```

Inventory rows do not fetch BBO and do not compute `net_edge`. They are the coverage/debug source that tells us which sports markets exist and which ones still need official schedules or Kalshi adapters.

### Binary Arb Snapshot

Used when both venues represent the same binary Yes/No contract.

Fields:

```text
ts_utc
domain / sport
match_name
canonical_event_id
market_type
pm_yes_team / pm_yes_outcome
ks_yes_team / ks_yes_outcome
pm_bid / pm_ask / pm_bid_sz / pm_ask_sz
ks_bid / ks_ask / ks_bid_sz / ks_ask_sz
net_edge
best_leg
gross_cost
best_leg_bbo_size
net_profit_at_bbo
raw PM identifiers
raw KS identifiers
```

`best_leg_bbo_size` is the smaller available size across the two BBO levels needed by the selected leg. It is a conservative top-of-book capacity, not a promise of realized fill.

### Compatibility Snapshot

Used when a universe exists but cannot safely use the binary formula.

Example: World Cup group-stage soccer with Team A / Tie / Team B.

Fields:

```text
ts_utc
source
event_id
title
event_date
market_count
outcomes
is_binary_candidate
skip_binary_arb
reason
```

## Current Week 1-2 Universes

From `ONBOARDING.md`:

- MLB: `KXMLBGAME`, PM tag `mlb`
- NBA: `KXNBAGAME`, PM tag `nba`
- Esports:
  - LoL: `KXLOLGAME`
  - CS2: `KXCS2GAME`
  - Valorant: `KXVALORANTGAME`
- World Cup / soccer: `KXWCGAME`, PM tag `soccer`

Current handling:

- MLB: binary arb snapshot at `data/mlb_arb_snapshot_latest.csv`.
- NBA: binary arb snapshot at `data/nba_arb_snapshot_latest.csv`, currently header-only when no active paired game markets exist.
- CS2: header-only until a Valve or official organizer schedule adapter is configured. Name-only PM/Kalshi pairing is disabled.
- LoL: binary esports snapshot at `data/lol_arb_snapshot_latest.csv`; pairs only when PM and Kalshi both match a concrete Riot LoL Esports official schedule match.
- Valorant: binary esports snapshot at `data/valorant_arb_snapshot_latest.csv`; pairs only when PM and Kalshi both match a concrete Riot Valorant Esports official schedule match.
- World Cup / soccer: binary outcome snapshot. The adapter pairs PM/Kalshi Team A win, Draw/Tie, and Team B win contracts through a local FIFA World Cup 2026 group-stage schedule.
- Polymarket Sports inventory: all listed sports categories are tracked at `data/polymarket_sports_inventory_latest.csv`; only registry entries with `arb_enabled=true` flow into binary arb rows.
- Per-sport inventory: every adapter writes `data/sports/<sport>_latest.csv`.
- Formula 1 is keyed by Polymarket `/sports` metadata (`sport=f1`, `tag_id=435`, `series=11635`) rather than a guessed `formula-1` tag slug.

Current command:

```bash
python3 scripts/build_all_snapshots.py --show-warnings
```

All-sports inventory command:

```bash
python3 scripts/build_all_snapshots.py --sports all --show-warnings
```

Single-adapter debug command:

```bash
python3 scripts/run_sport_snapshot.py --sport formula_1 --show-warnings
```

Validation command:

```bash
python3 scripts/validate_snapshots.py
```

The standalone discovery/pair/build scripts have been consolidated into the unified runner plus shared modules:

- `scripts/build_all_snapshots.py`
- `scripts/run_snapshot_loop.py`
- `scripts/run_sport_snapshot.py`
- `scripts/sports_adapters/`
- `scripts/pipeline_core.py`
- `scripts/universe_adapters.py`
- `scripts/nba_common.py`

## Week 1-2 Exit Criteria

- Each onboarding universe has a script.
- Each script can run once and write a snapshot.
- Active binary universes write BBO + net-edge rows.
- Non-binary universes write compatibility rows and explicit skip reasons.
- World Cup soccer outcome contracts write BBO + net-edge rows for Team A win, Draw/Tie, and Team B win.
- Unmatched or ambiguous pairs produce warnings.
- Latest snapshots are overwritten.
- Daily history is appended.
- `data/alerts/` contains the current opportunity CSV plus a human-readable summary or no-alert marker.
- A unified runner can execute all current universes without changing their separate outputs.
