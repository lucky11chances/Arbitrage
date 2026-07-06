# Code Cleanup Audit

This audit documents the current conservative cleanup boundary after splitting the project into legacy HTTP and new WebSocket pipelines.

## Active Legacy HTTP

Keep these files. They write to `data/old/old_arb_research.sqlite` through `scripts/old_market_db.py`.

- `scripts/old_staging_run_market_db_loop.py`
- `scripts/old_staging_collect_market_db.py`
- `scripts/old_staging_normalize_market_db.py`
- `scripts/old_staging_pair_from_db.py`
- `scripts/old_staging_collect_paired_orderbooks.py`
- `scripts/old_staging_compute_edges_from_db.py`
- `scripts/old_staging_edge_snapshot_builder.py`
- `scripts/old_staging_paper_trade_simulator.py`
- `scripts/old_staging_pair_refresh_loop.py`
- `scripts/old_staging_targeted_metadata_refresh.py`
- `scripts/old_staging_week3_daily_report.py`
- `scripts/old_market_db.py`

## Active New WebSocket

Keep these files. They write to `data/new/new_arb_research.sqlite` through `scripts/new_ws_orderbook_core.py`.

- `scripts/new_ws_orderbook_core.py`
- `scripts/new_bootstrap_ws_database.py`
- `scripts/new_run_ws_orderbook_collector.py`
- `scripts/new_start_ws_orderbook_sync.sh`
- `scripts/new_run_ws_orderbook_supervisor.sh`
- `scripts/new_run_ws_orderbook_service.sh`
- `scripts/new_run_ws_orderbook_launchd.sh`
- `scripts/new_start_ws_orderbook_tmux.sh`
- `scripts/new_status_ws_orderbook_service.sh`
- `scripts/new_status_ws_orderbook_tmux.sh`
- `scripts/new_validate_ws_top3_writer_benchmark.py`
- `scripts/new_export_ws_metadata.py`

## Shared Core, Reporting, And Adapters

Keep these until a narrower owner is proven. They are imported by active staging tests, reports, or adapter flows.

- `scripts/old_pipeline_core.py`
- `scripts/old_sports_taxonomy.py`
- `scripts/old_sports_registry.py`
- `scripts/old_sports_pairing.py`
- `scripts/old_sports_inventory.py`
- `scripts/old_universe_adapters.py`
- `scripts/old_sports_adapters/*`
- `scripts/old_validate_market_db.py`
- `scripts/old_validate_pairing_repair.py`
- `scripts/old_validate_tennis_pairing_repair.py`
- `scripts/old_staging_generate_human_review.py`
- `scripts/old_staging_build_human_review_workbook.mjs`
- `scripts/old_staging_style_human_review_workbook.mjs`
- `scripts/old_staging_prune_non_binary_markets.py`

## Deletion Decision

No additional code was deleted in this pass. The working tree already shows legacy CSV snapshot files and old loop scripts as deleted. Those deletions are treated as pre-existing user work, not as new cleanup performed here.

Future deletion candidates must satisfy the deletion rules in `AGENTS.md`: no imports, no tests, no active entrypoint, a clear replacement, and passing tests after removal.
