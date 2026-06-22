# city-heatmap-data

Python worker that generates the pre-baked GeoJSON the
[`city-heatmap-front`](../city-heatmap-front) app serves. It queries OpenStreetMap
(Overpass) plus a few other open-data providers, normalises everything into one
compact schema, and writes per-city GeoJSON. No front-end code, nothing to expose.

## Quick start

```bash
uv sync                      # Python 3.11+  (or: pip install -e .)

# generate Paris store + boundary data into a local folder (→ ./out/<city>/<name>.geojson) …
uv run city-heatmap-fetch fetch-stores   paris food    --out-dir ./out
uv run city-heatmap-fetch fetch-stores   paris fitness --out-dir ./out
uv run city-heatmap-fetch fetch-boundary paris         --out-dir ./out

# … then copy into the app at the paths src/cities.ts expects (note the boundary
# is flattened from <city>/boundary.geojson to <city>.geojson):
cp ./out/paris/food.geojson ./out/paris/fitness.geojson ../city-heatmap-front/data/places/paris/
cp ./out/paris/boundary.geojson                          ../city-heatmap-front/data/boundaries/paris.geojson
```

`make load paris` runs the whole Paris set (stores + boundary + the Paris-only
layers) in one go. See **[fetcher/README.md](fetcher/README.md)** for every
command, provider details, guards, and the output schema.

> **The two repos are independent and, in production, run on separate machines.**
> Generated data reaches the app by **manual copy / upload** into the app repo's
> `data/` tree - there is no automated pipeline, sync, or shared filesystem. The
> `--out-dir` flag exists so the output is decoupled from any sibling path. Note
> the app serves data from its repo-root `data/` folder (not `public/data/`):
> store layers at `data/places/<city>/`, boundaries at `data/boundaries/<city>.geojson`.

## What it generates - and what's city-specific

| Output | Cities | Source |
|---|---|---|
| `places/<city>/food.geojson`, `fitness.geojson` | **any city** | OSM/Overpass (+ Overture, Geoapify) |
| `<city>/boundary.geojson` | **any city** | OSM admin relation |
| `places/paris/trees.geojson` | **Paris only** | opendata.paris.fr `les-arbres` |
| `places/paris/transit.geojson` | **Paris only** | IDF Mobilités `emplacement-des-gares-idf` |
| `places/paris/transit-lines.geojson` | **Paris only** | IDF Mobilités `traces-du-reseau-ferre-idf` |
| `places/paris/pharmacy.geojson` | **Paris only** | Région Île-de-France pharmacy register |

The food/fitness pipeline can also pull **SIRENE** (data.gouv) - a **France-only**
enrichment that auto-skips outside France.

So **food, fitness and boundary work for any city**; trees, transit,
transit-lines and pharmacies are wired to Paris / Île-de-France datasets only.

## Use your own city

1. Add a `CityDef` to [`fetcher/cities.py`](fetcher/cities.py): the `wikidata`
   area id, OSM `relation` id, `bbox`, boundary `tolerance_deg` + plausible
   `area_range`, and a `geoapify_query`. Keep it in sync with the app's
   `src/cities.ts`.
2. Generate: `fetch-stores <city> food`, `fetch-stores <city> fitness`,
   `fetch-boundary <city>`.
3. Copy the files into the app repo's `data/` and register the city in
   `src/cities.ts`.
4. Skip the Paris-only commands (`fetch-trees` / `fetch-transit` /
   `fetch-transit-lines` / `fetch-pharmacies`) - they have no equivalent
   elsewhere yet.

## Dependencies & keys

- **Python 3.11+** (`uv sync`, or `pip install -e .`). OSM, boundary and the
  Paris open-data layers are stdlib-only.
- **`duckdb`** - only for the Overture (fitness) and SIRENE providers; installed
  by `uv sync`. Skip with `--no-overture` / `--no-sirene`.
- **`GEOAPIFY_KEY`** - only for the Geoapify provider; put it in `.env` (copy
  `.env.example`) or export it. Skip with `--no-geoapify`. Free key:
  <https://myprojects.geoapify.com/>.

Any provider whose key/dependency is missing is **skipped with a warning**; the
run still proceeds as long as OSM (the backbone) returns data.

## Refreshing the data (optional automation)

Re-run the fetch commands whenever you want fresh data and copy the output to
wherever the app is hosted. Output is timestamp-free and sorted, and per-dataset
guards refuse partial results, so re-runs only change files that actually changed.

`weekly-refresh.sh` is a **local, same-machine convenience** that regenerates the
store data straight into a sibling app clone - handy if you keep both repos on one
box. It is *not* the production path (the repos are independent); treat it as
scaffolding and adapt it to your own deploy and schedule.
