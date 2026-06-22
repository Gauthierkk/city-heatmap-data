#!/usr/bin/env bash
#
# weekly-refresh.sh - refresh the city-heatmap store data into a local folder.
#
# Runs from this worker repo (city-heatmap-data). It regenerates all six
# GeoJSON store files into this repo's local data/ folder. Boundaries are NOT
# part of the weekly job (they rarely change - refresh them manually when
# needed), e.g.:
#
#     python3 -m fetcher fetch-boundary <city> --out-dir "$(pwd)/data"
#
# Designed to be idempotent and cron-safe. Intended cron entry (weekly):
#
#     0 4 * * 0 /path/to/city-heatmap-data/weekly-refresh.sh >> /path/to/refresh.log 2>&1
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
LOCK_DIR="$SCRIPT_DIR/.refresh.lock"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/refresh-$(date -u +%Y%m%dT%H%M%SZ).log"

mkdir -p "$LOG_DIR"
# Tee everything to a timestamped log as well as stdout (which cron may capture).
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== weekly-refresh $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# --- Single-instance lock (portable: mkdir is atomic on macOS + Linux) --------
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another refresh is already running ($LOCK_DIR exists). Aborting." >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

# --- Regenerate all store data into the local data/ folder --------------------
echo "--- fetching all cities x datasets → $DATA_DIR ---"
cd "$SCRIPT_DIR"
python3 -m fetcher fetch-stores --all --out-dir "$DATA_DIR"

echo "Done - data written to $DATA_DIR."
