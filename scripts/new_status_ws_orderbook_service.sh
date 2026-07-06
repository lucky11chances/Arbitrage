#!/usr/bin/env bash
set -euo pipefail

SERVICE_ROOT="${ARB_WS_SERVICE_ROOT:-$HOME/Library/Application Support/ArbitrageWS}"

launchctl list | grep com.arbitrage.ws-orderbook || true
echo
echo "Status CSV:"
cat "$SERVICE_ROOT/data/new/new_latest_ws_status.csv" 2>/dev/null || true
echo
echo "Latest DB rows:"
sqlite3 -readonly "$SERVICE_ROOT/data/new/new_arb_research.sqlite" \
  "select max(collected_ts_utc), count(*) from orderbook_observations; select venue, count(distinct instrument_id), count(*) from orderbook_observations group by venue;" \
  2>/dev/null || true
echo
echo "Recent stdout:"
tail -n 20 "$SERVICE_ROOT/logs/new_ws_orderbook.out.log" 2>/dev/null || true
echo
echo "Recent stderr:"
tail -n 20 "$SERVICE_ROOT/logs/new_ws_orderbook.err.log" 2>/dev/null || true
