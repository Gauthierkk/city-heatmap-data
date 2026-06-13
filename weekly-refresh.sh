#!/usr/bin/env bash
#
# weekly-refresh.sh — refresh the city-heatmap store data and publish it.
#
# Runs from this worker repo (city-heatmap-data). It regenerates all six
# GeoJSON store files into the sibling front-end repo's public/data/, then
# commits + pushes them so GitHub Pages redeploys. Boundaries are NOT part of
# the weekly job (they rarely change — refresh them manually when needed):
#
#     python3 -m data fetch-boundary <city> --out-dir ../city-heatmap-front/public/data
#
# Designed to be idempotent and cron-safe. Intended cron entry (weekly):
#
#     0 4 * * 0 /path/to/city-heatmap-data/weekly-refresh.sh >> /path/to/refresh.log 2>&1
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONT_DIR="$(cd "$SCRIPT_DIR/../city-heatmap-front" 2>/dev/null && pwd || true)"
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

# --- Sanity: the sibling front-end repo must be present -----------------------
if [ -z "$FRONT_DIR" ] || [ ! -d "$FRONT_DIR/.git" ]; then
  echo "Front-end repo not found at ../city-heatmap-front (expected a sibling git clone)." >&2
  exit 1
fi

# --- Sync the front-end clone to latest main ----------------------------------
echo "--- syncing $FRONT_DIR to origin/main ---"
git -C "$FRONT_DIR" checkout main
if ! git -C "$FRONT_DIR" pull --ff-only; then
  echo "git pull --ff-only failed (front-end clone has diverged). Aborting." >&2
  exit 1
fi

# --- Regenerate all store data into the front-end repo ------------------------
echo "--- fetching all cities x datasets ---"
cd "$SCRIPT_DIR"
python3 -m data fetch-stores --all --out-dir "$FRONT_DIR/public/data"

# --- Commit + push only if something actually changed -------------------------
git -C "$FRONT_DIR" add public/data
if git -C "$FRONT_DIR" diff --cached --quiet; then
  echo "No data changes this run — nothing to commit."
  exit 0
fi

echo "--- committing + pushing refreshed data ---"
git -C "$FRONT_DIR" commit -m "chore(data): weekly refresh $(date -u +%Y-%m-%d)"
git -C "$FRONT_DIR" push origin main
echo "Done — pushed; GitHub Pages will redeploy."
