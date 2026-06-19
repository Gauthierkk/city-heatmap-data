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

## Trees (separate pipeline)

The Paris **street-tree** layer is intentionally **separate** from the places
pipeline above — trees are not businesses, so none of the providers/merge/OSM
machinery applies (nothing to dedup, distinct trees aren't "duplicates", and
[opendata.paris.fr `les-arbres`](https://opendata.paris.fr/explore/dataset/les-arbres/)
is the single authoritative source, ~218k trees). It has its own
`fetch-trees` command and provider (`providers/trees.py`, **not** registered in
`ALL_PROVIDERS`), and shares only the boundary clip + writer.

### Output format — `trees-columnar-v1` (contract with the front-end repo)

Trees do **not** ship as a GeoJSON FeatureCollection. With ~192k points the
species strings repeat on every feature (e.g. "Plane tree" ~39k times), so a
FeatureCollection bloats to ~36 MB and stalls the client on `JSON.parse` + GPU
upload. Instead `trees.geojson` is a plain JSON object — `trees-columnar-v1` —
that replaces the repeated strings with a species lookup table + integer index
and drops the per-feature GeoJSON boilerplate by going columnar (~5–7× smaller,
~5 MB). The front end detects this shape (the top-level `format` field / absence
of `type: "FeatureCollection"`) and reads it instead of the FeatureCollection
path. **This format is the contract between the two repos** — changing it
requires a matching front-end change.

```jsonc
{
  "format": "trees-columnar-v1",
  "species": [
    { "fr": "Platane",    "en": "Plane tree" },      // index 0 = most frequent
    { "fr": "Marronnier", "en": "Horse chestnut" },
    { "fr": "",           "en": "" }                  // trees with no recorded species
    /* ... ~235 distinct species ... */
  ],
  "coordinates":  [[2.37049, 48.83139], /* ... */],   // [lng, lat], 5 dp (~1 m)
  "speciesIndex": [0, 1, /* ... */]                    // indexes into `species`
}
```

- `species` is the **deduplicated** lookup table, each entry keeping both the
  French and English name, **sorted by frequency** (index 0 = most common).
- `coordinates[i]` and `speciesIndex[i]` are **parallel** arrays of equal length
  (one entry per tree); `speciesIndex[i]` indexes into `species`.
- Trees with no recorded species (~3.4k) share **one real table entry**
  `{ "fr": "", "en": "" }` — there is no sentinel; every tree has a valid index.
- Indices are only **stable within a single generated file** — they may renumber
  between regenerations, so the front end must read them per file.

`species[*].fr` is the dataset's `libellefrancais` (French common name);
`species[*].en` is its English common name via
[`providers/tree_species_en.py`](providers/tree_species_en.py), a curated
French→English map (each distinct name translated once, then cached). Names with
no settled English form fall back to the French name.

Internally `fetch_trees` builds a FeatureCollection (the shape the boundary clip +
guards operate on) and `providers/trees.py:to_columnar` collapses it to
`trees-columnar-v1` just before writing.

The export is **clipped to the committed Paris boundary** (dropping the
Paris-owned cemeteries — Pantin, Bagneux, Thiais — that sit outside the admin
polygon), guarded against a partial fetch (`< 150k` trees ⇒ refuse), and written
to `<city>/trees.geojson` (~192k points at 5 dp precision). **Paris-only** —
every other city emits an empty `trees-columnar-v1` object.

## Public transit (separate pipeline)

The Paris **public-transit** station layer is another separate, Paris-only
pipeline (`fetch-transit`, `providers/transit.py`). Source: IDF Mobilités'
[`emplacement-des-gares-idf`](https://www.data.gouv.fr/datasets/gares-et-stations-du-reseau-ferre-dile-de-france-par-ligne)
on the same Opendatasoft API as the trees layer.

The source lists one row per **station × line** (~1240 region-wide); the provider
collapses these to **one point per physical station** — grouped by station name
within an 800 m radius (so a split hub like Gare du Nord's metro/RER zones unify,
but the two distant "Malesherbes" stay separate), positioned at the mean
coordinate. The result is clipped to the Paris boundary (~297 stations).

Each station carries a **list** of categories (no address):

```jsonc
"properties": {
  "id": "transit/73626",
  "name": "Gare de Lyon",
  "categories": ["major_station", "metro", "rer", "train"]
}
```

`categories` holds the station's modes (`metro`, `rer`, `train`, `tram`, `val`,
`cable`); the six Paris mainline terminals (Nord, Est, Lyon, Austerlitz,
Montparnasse, Saint-Lazare — Bercy excluded) also get `major_station`. A guard
refuses to write below 200 stations.

## Pharmacies (separate pipeline)

The Paris **pharmacy** layer is another separate, Paris-only pipeline
(`fetch-pharmacies`, `providers/pharmacies.py`). Source: the Région Île-de-France
open-data register
[`carte-des-pharmacies-de-paris`](https://www.data.gouv.fr/datasets/carte-des-pharmacies-de-paris-idf)
(≈987 establishments, all *département* 75, each with a FINESS id, name and street
address). Unlike trees/transit it needs **no special output format** — it emits a
normal store-shaped FeatureCollection with `shop = "pharmacy"`, so the front end
renders it through the ordinary places machinery (dots + distance overlay +
closest-places). The ALL-CAPS register text is title-cased for display parity:

```jsonc
"properties": {
  "id": "pharmacy/750009227",   // FINESS établissement id
  "name": "Grande Pharmacie La Paix Opera",
  "shop": "pharmacy",
  "address": { "housenumber": "24", "street": "Rue De La Paix", "postcode": "75002", "city": "Paris" }
}
```

The result is clipped to the committed Paris boundary (the register is already
Paris-only, so this is defensive) and guarded against a partial fetch (`< 700`
pharmacies ⇒ refuse).

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
python3 -m fetcher fetch-stores paris fitness

# nyc and austin are soft-deprecated: their committed data is kept, but fetching
# is skipped unless --force is passed.
python3 -m fetcher fetch-stores nyc                  # skipped (prints a notice)
python3 -m fetcher fetch-stores nyc fitness --force  # actually refreshes nyc

# Fetch all cities × datasets. Deprecated cities (nyc, austin) are skipped, so
# this refreshes paris food + paris fitness only; add --force to include them.
# Sleeps ~10 s between provider rounds to be polite.
python3 -m fetcher fetch-stores --all

# Restrict which providers are queried
python3 -m fetcher fetch-stores paris food --providers osm,geoapify
python3 -m fetcher fetch-stores paris fitness --no-geoapify   # skip one provider
python3 -m fetcher fetch-stores paris food --providers osm    # OSM only

# Fetch city admin boundary — defaults to paris
python3 -m fetcher fetch-boundary
python3 -m fetcher fetch-boundary nyc    --force   # deprecated: needs --force
python3 -m fetcher fetch-boundary austin --force   # deprecated: needs --force

# Fetch the Paris street-tree density layer (Paris-only, separate pipeline)
python3 -m fetcher fetch-trees
python3 -m fetcher fetch-trees paris --out-dir ../city-heatmap-front/public/data

# Fetch the Paris public-transit station layer (Paris-only, separate pipeline)
python3 -m fetcher fetch-transit
python3 -m fetcher fetch-transit paris --out-dir ../city-heatmap-front/public/data

# Fetch the Paris pharmacy layer (Paris-only, separate pipeline)
python3 -m fetcher fetch-pharmacies
python3 -m fetcher fetch-pharmacies paris --out-dir ../city-heatmap-front/public/data

# Write to an explicit out-dir (the weekly wrapper passes the front-end repo)
python3 -m fetcher fetch-stores --all --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-stores nyc fitness --force --out-dir /tmp/out
```

### Output files

Nested per city — `<out-dir>/<city>/<name>.geojson`:

| Command | Output file |
|---|---|
| `fetch-stores <city> food` | `<city>/food.geojson` |
| `fetch-stores <city> fitness` | `<city>/fitness.geojson` |
| `fetch-boundary <city>` | `<city>/boundary.geojson` |
| `fetch-trees paris` | `<city>/trees.geojson` (`trees-columnar-v1`: species table + parallel coord/index arrays, Paris-only) |
| `fetch-transit paris` | `<city>/transit.geojson` (FeatureCollection, Paris-only) |
| `fetch-pharmacies paris` | `<city>/pharmacy.geojson` (FeatureCollection, `shop=pharmacy`, Paris-only) |

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
# nyc and austin are deprecated — pass --force to refresh their boundaries:
python3 -m fetcher fetch-boundary nyc    --force --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-boundary austin --force --out-dir ../city-heatmap-front/public/data
```

The **trees**, **transit** and **pharmacies** layers are likewise excluded from
the weekly job (they change slowly and are Paris-only) — refresh by hand when
needed:

```bash
python3 -m fetcher fetch-trees      paris --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-transit    paris --out-dir ../city-heatmap-front/public/data
python3 -m fetcher fetch-pharmacies paris --out-dir ../city-heatmap-front/public/data
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
