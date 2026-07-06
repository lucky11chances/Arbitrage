#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

echo "tmux:"
tmux list-sessions 2>/dev/null | grep '^arb_ws_orderbook:' || true

echo
echo "status CSV:"
cat data/new/new_latest_ws_status.csv 2>/dev/null || true

echo
echo "latest DB rows:"
sqlite3 -readonly data/new/new_arb_research.sqlite \
  "select max(collected_ts_utc), count(*) from orderbook_observations; select venue, count(distinct instrument_id), count(*) from orderbook_observations group by venue;" \
  2>/dev/null || true

echo
echo "recent log:"
tail -n 40 data/new/new_ws_orderbook_tmux.log 2>/dev/null || true
