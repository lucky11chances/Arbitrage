# Pairing Runtime

`scripts/old_staging_pair_from_db.py` is the PM/KS sports pairing source of truth.
It builds safe pairs from the local staging DB only, using conservative event,
market type, outcome, and UTC start-time checks.

Runtime path:

1. `scripts/old_staging_pair_refresh_loop.py`
   - Calls `old_staging_pair_from_db.refresh_pairs_incremental`.
   - Upserts current safe pairs into `paired_contracts`.
   - Disables stale safe pairs in scope.
   - Does not reset `paired_contracts`.
   - Does not delete historical `edge_snapshots`.

2. `scripts/old_staging_collect_paired_orderbooks.py`
   - Reads `paired_contracts where safe_paired = 1` every snapshot window.
   - Collects PM/KS BBO orderbooks for all safe pairs.
   - Writes snapshot windows, per-pair results, and edge snapshots.
   - Owns the production latest CSVs:
     - `data/old/old_latest_edges.csv`
     - `data/old/old_latest_alerts.csv`
     - `data/old/old_alerts/old_latest_opportunities.csv`
   - `latest_edges.csv` is a universal snapshot: every attempted safe pair gets
     one row, with `pair_status` and `pair_reason` explaining failed fetches.

3. `scripts/old_sports_pairing.py`
   - Legacy import compatibility bridge only.
   - Routes adapter pairing/diagnostics calls to the DB-only staging pairer.
   - The old live-fetch matcher is archived at
     `archive/legacy_pairing_2026-06-30/scripts/old_sports_pairing.py`.

Current tmux testing services should use the staging scripts above. Do not run
archived pairing code in a long-running loop.

`scripts/old_staging_compute_edges_from_db.py` is ad-hoc only. It recomputes edges
from latest DB observations and must not overwrite the production universal CSVs
unless an operator passes `--allow-production-csv-overwrite` intentionally.
