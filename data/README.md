# data — Python data-fetch package

Queries the Overpass API / OSM boundary services, normalises tags, deduplicates,
and writes compact GeoJSON into the **front-end repo's** `public/data/<city>/`
(default: the sibling `city-heatmap-front/` clone). This package lives in the
separate `city-heatmap-data` worker repo so the front end carries no Python; see
that repo's top-level `README.md` for the weekly-refresh runbook.

## Requirements

- **Python 3.11+** (tested on 3.14). Stdlib only for food datasets.
- **`duckdb`** — required **only** for fitness datasets (Overture merge). Install once:
  ```bash
  pip3 install duckdb --user --break-system-packages   # Python 3.11+
  # or for Python 3.10:
  pip3.10 install duckdb --user
  ```
  If duckdb is unavailable, pass `--no-overture` to fall back to OSM-only
  fitness data (the merge step is skipped entirely).

## Commands

```bash
# Fetch store data — defaults to paris food
python3 -m data fetch-stores
python3 -m data fetch-stores nyc
python3 -m data fetch-stores nyc fitness
python3 -m data fetch-stores paris fitness

# Fetch all cities × datasets (paris food, paris fitness, nyc food, ...)
# Sleeps ~10 s between Overpass calls to be polite
python3 -m data fetch-stores --all

# Fetch city admin boundary — defaults to paris
python3 -m data fetch-boundary
python3 -m data fetch-boundary nyc
python3 -m data fetch-boundary austin

# Write to an explicit out-dir (the weekly wrapper passes the front-end repo)
python3 -m data fetch-stores --all --out-dir ../city-heatmap-front/public/data
python3 -m data fetch-stores nyc fitness --out-dir /tmp/out
```

### Output files

Nested per city — `<out-dir>/<city>/<name>.geojson`:

| Command | Output file |
|---|---|
| `fetch-stores <city> food` | `<city>/food.geojson` |
| `fetch-stores <city> fitness` | `<city>/fitness.geojson` |
| `fetch-boundary <city>` | `<city>/boundary.geojson` |

### Guards

`fetch-stores` refuses to overwrite if Overpass returns fewer features than the
per-dataset minimum (food: 100, fitness: 50) and exits non-zero. `fetch-boundary`
aborts if the simplified polygon's area falls outside the per-city plausible range.

## Intended schedule

Run weekly via `../weekly-refresh.sh` (commits + pushes the front-end repo).
Boundaries are excluded from the weekly job — refresh them by hand when needed:

```bash
python3 -m data fetch-boundary paris  --out-dir ../city-heatmap-front/public/data
python3 -m data fetch-boundary nyc    --out-dir ../city-heatmap-front/public/data
python3 -m data fetch-boundary austin --out-dir ../city-heatmap-front/public/data
```

## Sync notes

These files live in the **`city-heatmap-front`** repo; keep them in sync when
either side changes:

- **`data/cities.py` ↔ `src/cities.ts`** whenever city ids, wikidata ids, or
  OSM relation ids change.
- **`data/overpass.py` `SHOP_TYPES` ↔ the food tags in `src/storeTypes.ts`**.
- **`data/overpass.py` fitness sport list and `normalise_fitness` ↔ the fitness
  tags in `src/storeTypes.ts`**.
- **`data/boundary.py` area ranges and tolerance values** match the per-city
  comments in `data/cities.py`. NYC's OSM admin polygon legitimately extends
  into harbour/bay water (~1,223 km²), so its range is wider than the land area.
