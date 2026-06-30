# Codex Collaboration Guide

## Source Of Truth

`ONBOARDING.md` is the primary project framework. Implementation details can evolve, but the main objective, priority order, risk model, and validation expectations should be interpreted through `ONBOARDING.md` first.

The project goal is PM/KS sports cross-venue arbitrage research: collect real market data, validate PM/KS event and outcome pairing, estimate executable edge, and support a deploy-or-kill decision.

## Naming

- `PM` or `pm` means Polymarket.
- `KS` or `ks` means Kalshi.
- `BBO` means best bid and offer: the best currently visible bid and ask from an order book.

## Code Change Workflow

Do not directly modify or interrupt the currently running production snapshot loop unless the user explicitly asks.

New code should first be developed in an isolated staging path or script. Preferred names:

- General experiments: `scripts/staging_<feature_name>.py`
- Per-sport adapter work: `scripts/sports_adapters/<sport>_staging.py`
- One-off validation helpers: `scripts/validate_<feature_name>.py`

Only merge staging code into the main runner, common core, or production adapter after it passes full validation. For data pulls and CSV validation, do not rely on sampled rows; validate every generated row.

## Production Safety

The production loop is expected to keep writing latest CSVs, history CSVs, and alert files while new work is being developed separately.

Safe paired arb output should only be generated when PM and KS match on sport, event identity, date/time, market type, and outcome semantics. If matching is uncertain, write inventory or warnings only; do not calculate fake edge.

Positive-edge alerts should remain visible in CSV output through `alert=ALERT` and `alert_reason=net_edge_positive`, plus the consolidated alert files under `data/alerts/`.
