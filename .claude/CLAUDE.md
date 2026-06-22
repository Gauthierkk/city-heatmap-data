# city-heatmap-data

Data-refresh **worker** for the `city-heatmap-front` app: a Python `fetcher/`
package that queries OSM/Overpass + other providers and writes compact GeoJSON
(stores, boundaries, Paris trees/transit). This repo generates data only - it
contains no front-end code.

## Cross-repo assumption (important)

- This repo and `city-heatmap-front` are **separate git repositories** and, in
  production, **run on separate machines**. Do not assume a shared filesystem.
- Generated data reaches the front end by **manual copy** (`cp`/upload) into the
  front repo's `data/` tree. There is intentionally **no automated pipeline,
  sync, symlink, rsync/scp step, or git hook** wiring the two together - do not
  add one.
- `weekly-refresh.sh` and the README runbook describe a *local, same-machine
  convenience* (writing straight into a sibling clone). Treat that as local-only
  scaffolding, not the prod path; never extend code to write into or push the
  sibling repo automatically.
- The `--out-dir` flag exists precisely so output is decoupled from any sibling
  path - generate into a local folder, then move the files by hand.

## Commands

See [README.md](../README.md) and [fetcher/README.md](../fetcher/README.md) for
the full command set, guards, and provider notes. nyc and austin are
soft-deprecated: fetching them is skipped unless `--force` is passed.
