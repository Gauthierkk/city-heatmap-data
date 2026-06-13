# fetcher — Python data-fetch package

Queries several geomapping APIs for the same city + dataset, normalises each into
one schema, **merges them into a single duplicate-free set**, and writes compact
GeoJSON into the **front-end repo's** `public/data/<city>/` (default: the sibling
`city-heatmap-front/` clone). This package lives in the separate `city-heatmap-data`
worker repo so the front end carries no Python; see that repo's top-level
`README.md` for the weekly-refresh runbook.

## Providers

Each provider is queried for the same data; results are merged by the
**source-agnostic** aggregator (`transform/aggregate.py`) — no provider is ranked.

| Provider | `name` | Datasets | Notes |
|---|---|---|---|
| OpenStreetMap (Overpass) | `osm` | food, fitness | Comprehensive backbone; the run aborts if it returns nothing. Stdlib only. |
| Overture Maps | `overture` | fitness | S3 + DuckDB. Needs `duckdb`. |
| Geoapify Places | `geoapify` | food, fitness | Needs `GEOAPIFY_KEY`. Fitness coverage is gyms + dojos only. |

### Merge / dedup

Two records are the same business when within ~100 m **and** their names roughly
match (containment or token-Jaccard ≥ 0.5); matching is across all types. For each
duplicate cluster the **most-complete** record is kept (most populated
name/address/type fields; ties → lowest `id`) and its missing `name`/address
subfields are **backfilled** from the others. Nothing in the merge prefers a
provider. Provenance is **not** recorded in the output.

### Feature schema

```jsonc
"properties": {
  "id": "node/123",   // representative record's native id (opaque stable key)
  "name": "Monoprix",
  "shop": "supermarket",
  "address": { "housenumber": "12", "street": "Rue de Rivoli", "postcode": "75001", "city": "Paris" }
}
```
`name` and `address` may be `null`; `address` holds only the populated subset of
`{housenumber, street, postcode, city}`.

## Requirements

- **Python 3.11+** (tested on 3.14). Stdlib only for the OSM provider.
- **`duckdb`** — required **only** by the Overture provider (fitness). Install once:
  ```bash
  pip3 install duckdb --user --break-system-packages   # Python 3.11+
  # or for Python 3.10:
  pip3.10 install duckdb --user
  ```
- **`GEOAPIFY_KEY`** — required by the Geoapify provider. Put it in a repo-root
  `.env` (see `.env.example`) or export it. Get a free key at
  https://myprojects.geoapify.com/.
- Any provider whose dependency/key is missing is **skipped with a warning** (use
  `--no-overture` / `--no-geoapify` / `--providers` to control this explicitly);
  the run still proceeds on the others as long as OSM returns data.

## Commands

```bash
# Fetch store data — defaults to paris food
python3 -m fetcher fetch-stores
python3 -m fetcher fetch-stores nyc
python3 -m fetcher fetch-stores nyc fitness
python3 -m fetcher fetch-stores paris fitness

# Fetch all cities × datasets (paris food, paris fitness, nyc food, ...)
# Sleeps ~10 s between provider rounds to be polite
python3 -m fetcher fetch-stores --all

# Restrict which providers are queried
python3 -m fetcher fetch-stores paris food --providers osm,geoapify
python3 -m fetcher fetch-stores paris fitness --no-geoapify   # skip one provider
python3 -m fetcher fetch-stores paris food --providers osm    # OSM only

# Fetch city admin boundary — defaults to paris
python3 -m fetcher fetch-boundary
python3 -m fetcher fetch-boundary nyc
python3 -m fetcher fetch-boundary austin

# Write to an explicit out-dir (the weekly wrapper passes the front-end repo)
python3 -m fetcher fetch-stores --all --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-stores nyc fitness --out-dir /tmp/out
```

### Output files

Nested per city — `<out-dir>/<city>/<name>.geojson`:

| Command | Output file |
|---|---|
| `fetch-stores <city> food` | `<city>/food.geojson` |
| `fetch-stores <city> fitness` | `<city>/fitness.geojson` |
| `fetch-boundary <city>` | `<city>/boundary.geojson` |

### Guards

`fetch-stores` exits non-zero (refuses to overwrite) if: OSM returns nothing; the
**aggregated** total is below the per-dataset minimum (food: 100, fitness: 50); or
the new total drops below 70 % of the committed file (a likely provider outage).
`fetch-boundary` aborts if the simplified polygon's area falls outside the
per-city plausible range. Output is timestamp-free and sorted by `id`, so an
unchanged week produces no diff / no commit.

## Intended schedule

Run weekly via `../weekly-refresh.sh` (commits + pushes the front-end repo).
Boundaries are excluded from the weekly job — refresh them by hand when needed:

```bash
python3 -m fetcher fetch-boundary paris  --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-boundary nyc    --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-boundary austin --out-dir ../city-heatmap-front/public/data
```

## Sync notes

These files live in the **`city-heatmap-front`** repo; keep them in sync when
either side changes:

- **`fetcher/cities.py` ↔ `src/cities.ts`** whenever city ids, wikidata ids, OSM
  relation ids, or **bboxes** change (bbox now lives in `cities.py` and feeds the
  Overture + Geoapify providers).
- **Canonical `shop` types** — every provider's category map must emit only types
  the front end knows (`src/storeTypes.ts`):
  - `fetcher/providers/overpass.py` `SHOP_TYPES` + `normalise_food`/`normalise_fitness`
  - `fetcher/providers/overture.py` `_CATEGORY_TO_TYPE` (fitness)
  - `fetcher/providers/geoapify.py` `_CATEGORY_TO_TYPE` (food + fitness)
- **`fetcher/providers/boundary.py` area ranges and tolerance values** match the per-city
  comments in `fetcher/cities.py`. NYC's OSM admin polygon legitimately extends
  into harbour/bay water (~1,223 km²), so its range is wider than the land area.
