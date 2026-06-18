# city-heatmap-data

Weekly data-refresh **worker** for [city-heatmap-front](../city-heatmap-front).
Keeps the fetch logic (Python) fully separate from the always-on GitHub Pages
front end. This repo runs on a local server; once a week it regenerates the
store GeoJSON, writes it into the front-end repo's `public/data/`, and commits +
pushes so GitHub Pages redeploys. No frontend code, no servers to expose.

## Layout

Both repos are cloned as **siblings** under one parent (locally and on the
server):

```
city-heatmap/
  ├── city-heatmap-front/   ← app + committed data; gets pushed to (deploy-key write access)
  └── city-heatmap-data/    ← this repo: Python `fetcher/` module + weekly-refresh.sh
```

The front end loads data same-origin from `public/data/<city>/{food,fitness,boundary}.geojson`,
so the data must live in the front-end repo — this worker only *generates* it.

## How it works

`weekly-refresh.sh` (cron, weekly):

1. Takes a single-instance lock.
2. `git -C ../city-heatmap-front pull --ff-only` (the worker only ever touches
   `public/data/`; humans own the code, so this stays fast-forward).
3. `python3 -m fetcher fetch-stores --all --out-dir ../city-heatmap-front/public/data`
   — Each file is built by querying every provider for that city+dataset and
   merging into one duplicate-free set; ~10 s between rounds. **nyc and austin
   are soft-deprecated**: their committed data is kept but `--all` skips them, so
   only the paris × food/fitness files refresh. Pass `--force` to refresh the
   deprecated cities too.
4. If `public/data` changed, commit `chore(data): weekly refresh <date>` and
   push `main`; otherwise exit quietly (deterministic, timestamp-free output +
   per-dataset guards mean unchanged weeks produce no commit/redeploy).

Boundaries are **not** in the weekly job (they rarely change) — refresh by hand:
`python3 -m fetcher fetch-boundary <city> --out-dir ../city-heatmap-front/public/data`
(deprecated cities need `--force` here too).

Two Paris-only extra layers run as separate pipelines and are also excluded from
the weekly job — refresh by hand:
- **street trees** (`fetch-trees`; a Point FeatureCollection carrying each tree's species in French + English, not the store schema):
  `python3 -m fetcher fetch-trees paris --out-dir ../city-heatmap-front/public/data`.
- **public transit** (`fetch-transit`; stations with a `categories` list):
  `python3 -m fetcher fetch-transit paris --out-dir ../city-heatmap-front/public/data`.

See [fetcher/README.md](fetcher/README.md) for the fetch package's commands, guards,
Overture/duckdb details, and front-end sync notes.

## One-time server setup

1. **Clone both repos** as siblings under one parent.
2. **Dependencies & keys**: Python 3.11+, plus:
   - `duckdb` for the Overture fitness provider:
     `pip3 install duckdb --user --break-system-packages` (skip with `--no-overture`).
   - `GEOAPIFY_KEY` for the Geoapify provider (food + fitness) — copy `.env.example`
     to `.env` and fill it in (`.env` is gitignored), or export the var (skip with
     `--no-geoapify`). Free key: https://myprojects.geoapify.com/.

   Any provider whose dependency/key is missing is skipped with a warning; the run
   still proceeds on the others as long as OSM (the backbone) returns data.
3. **SSH deploy key** (so the worker can push the front-end repo):
   - `ssh-keygen -t ed25519 -f ~/.ssh/city-heatmap-front -C "city-heatmap data-bot"`
   - Add the **public** key to the `city-heatmap-front` GitHub repo →
     **Settings → Deploy keys**, with **Allow write access**.
   - Pin it via `~/.ssh/config`:
     ```
     Host github-city-heatmap-front
       HostName github.com
       User git
       IdentityFile ~/.ssh/city-heatmap-front
       IdentitiesOnly yes
     ```
   - Point the front-end clone's remote at the alias:
     `git -C ../city-heatmap-front remote set-url origin github-city-heatmap-front:<user>/city-heatmap-front.git`
4. **Commit identity** for the bot (local to the front-end clone):
   ```bash
   git -C ../city-heatmap-front config user.name  "city-heatmap data-bot"
   git -C ../city-heatmap-front config user.email "data-bot@users.noreply.github.com"
   ```
5. **Cron** (weekly, e.g. Sundays 04:00):
   ```cron
   0 4 * * 0 /path/to/city-heatmap/city-heatmap-data/weekly-refresh.sh >> /path/to/refresh.log 2>&1
   ```

Verify the wiring before trusting cron: `ssh -T github-city-heatmap-front`
should authenticate, then run `./weekly-refresh.sh` once by hand and confirm a
commit lands and the GitHub Pages deploy goes green.
