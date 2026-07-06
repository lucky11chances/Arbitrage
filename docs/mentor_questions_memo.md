# Mentor Questions Memo

Date: 2026-06-22

## 1. `--pair-refresh-seconds` Should Be What Value?

Current production command:

```bash
python3 -u scripts/run_snapshot_loop.py --sports all --interval 5 --bbo-workers 20 --show-warnings
```

`--pair-refresh-seconds` is not passed in production, so the script uses the default:

```python
parser.add_argument("--pair-refresh-seconds", type=float, default=300.0)
```

Current behavior:

- BBO/orderbook refresh target: every 5 seconds via `--interval 5`
- PM/KS market discovery refresh: every 300 seconds
- PM/KS semantic pairing refresh: every 300 seconds
- pairing diagnostics refresh: every 300 seconds

Question for mentor:

What should the production value of `--pair-refresh-seconds` be for sports arb monitoring?

Context for decision:

- Lower value, e.g. 30-60 seconds:
  - Finds newly listed PM/KS markets faster.
  - Updates diagnostics faster.
  - Useful near game start or during pairing development.
  - Increases PM/Kalshi discovery requests.
  - Can slow the production loop and increase 429/API failure risk.

- Higher value, e.g. 600-900 seconds:
  - Reduces API pressure.
  - Keeps BBO loop more stable.
  - Can delay discovery of new safe pairs.

Current default, 300 seconds, is a middle ground. Need mentor input on whether pairing freshness or loop stability matters more for this R&D phase.

## 2. How Do We Actually Meet the 5-Second BBO Requirement?

BBO means Best Bid and Offer:

- best bid = highest current buy price
- best ask = lowest current sell price

Project requirement from `ONBOARDING.md`:

- collect real PM/KS market data
- 5-second polling
- estimate executable edge from current BBO

Current production target:

```text
--interval 5
```

Current observed production performance:

- Recent normal iterations: about 10-14 seconds each.
- Recent 20-iteration average: about 16.8 seconds.
- Pairing/inventory refresh iterations can be much slower.

Why current loop misses 5 seconds:

- `--sports all` runs 19 adapters.
- Only some adapters produce safe arb rows, but all adapters still participate in inventory/diagnostics.
- BBO fetch is batched per adapter.
- Each adapter fetches PM and KS books with thread workers, then the next adapter runs.
- Discovery, diagnostics, inventory, BBO, CSV writes, and warnings all run in one process.
- No phase-level timing exists yet, so we cannot see exact PM vs KS vs adapter bottlenecks.

Question for mentor:

If 5-second BBO is a hard requirement, should we split production into:

- fast loop: only refresh BBO for already-safe pairs
- slow loop: discovery, pairing diagnostics, inventory

Also ask:

- Should all safe pair PM tokens and KS tickers be globally merged before BBO fetch?
- Should we switch BBO fetching to async HTTP or connection pooling?
- What request rate is safe for PM CLOB and Kalshi orderbook endpoints?
- Should all-sports inventory run outside the production BBO loop?

## 3. Why `--bbo-workers 20`?

Current production command:

```bash
--bbo-workers 20
```

Definition in code:

```python
parser.add_argument("--bbo-workers", type=int, default=12)
```

Production overrides the default `12` and uses `20`.

Usage path:

```text
run_snapshot_loop.py
-> run_adapter()
-> core.build_binary_rows(pairs, args.bbo_workers)
-> fetch_bbo_map(..., max_workers=20)
-> ThreadPoolExecutor(max_workers=workers)
```

What workers do:

- They are Python threads.
- They fetch PM token BBOs and KS market BBOs concurrently.
- The code caps workers to the number of identifiers:
  - `workers = min(max(max_workers, 1), len(identifiers))`

Question for mentor:

Why is 20 the right production value?

Context:

- Higher worker count can reduce BBO fetch wall time.
- Higher worker count can increase API pressure and 429/failure risk.
- Lower worker count is gentler but may make the 5-second target impossible.
- Current loop still takes about 10-14 seconds on normal iterations with 20 workers.

Need mentor guidance on:

- PM CLOB safe concurrency.
- Kalshi orderbook safe concurrency.
- Whether PM and KS should have separate worker limits.
- Whether all adapters should share a global worker pool.

## Why Are So Few Sports Actually Usable for Arb Right Now?

Current `data/pairing_diagnostics_latest.csv` summary:

```text
safe paired sports:
- mlb: 70 safe rows
- world_cup: 96 safe rows
- formula_1: 20 safe rows

nba:
- configured as paired
- currently 0 safe rows
```

Main causes:

1. Production arb snapshots only run adapters marked `arb_status="paired"`.

Current paired adapters in `old_sports_registry.py`:

```text
world_cup
mlb
nba
formula_1
```

Most other sports are inventory-first. They write inventory and diagnostics, but they do not produce executable arb snapshots.

2. Many configured Kalshi sports series currently have no open markets.

Observed examples:

```text
tennis: no_ks_open_markets
cricket: no_ks_open_markets
golf: no_ks_open_markets
ufc: no_ks_open_markets
pickleball: no_ks_open_markets
lacrosse: no_ks_open_markets
rugby: no_ks_open_markets
hockey: no_ks_open_markets
table_tennis: no_ks_open_markets
```

3. Some sports have Kalshi markets, but PM/KS semantics do not safely match yet.

Observed examples:

```text
football: Kalshi NFL/NCAAF markets exist, but no safe official schedule adapter is enabled.
boxing: Kalshi boxing markets exist, but official bout-card mapping is pending.
esports: Kalshi CS2/Valorant markets exist, but team/outcome parsing needs stronger match-format handling.
basketball: WNBA futures exist, but no safe PM/KS future mapping is enabled.
baseball: generic baseball adapter sees MLB Kalshi rows, but production-safe MLB pairing lives in the dedicated MLB adapter.
soccer: generic soccer has many PM rows, while World Cup pairing is handled by the dedicated `world_cup` adapter.
```

4. The project intentionally blocks uncertain pairs.

The current safety rule requires alignment on:

```text
sport
event identity
date/time
market type
outcome semantics
```

If any part is uncertain, the system writes inventory or diagnostics only. It avoids fake edge.

Current conclusion:

The small number of usable arb sports comes from three things together:

- Kalshi has no open markets for many configured series right now.
- Several sports lack official schedule or entity mapping adapters.
- The code intentionally excludes uncertain PM/KS matches from executable arb output.

Best next mentor question:

Which sport should be promoted next from inventory/diagnostics to safe pairing?

Candidate choices based on current diagnostics:

- Football: Kalshi has NFL/NCAAF open markets; needs official schedule/team mapping.
- Boxing: Kalshi has fight markets; needs official bout-card source.
- Esports: Kalshi has CS2/Valorant markets; needs better match-format and team alias parsing.
