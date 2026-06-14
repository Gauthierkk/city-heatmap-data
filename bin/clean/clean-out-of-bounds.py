#!/usr/bin/env python3
"""One-shot cleanup: drop places outside their city polygon or with no name.

Background: before the boundary-clip was added to the fetcher, the Geoapify and
Overture providers leaked places outside the city's clip zone (Geoapify clipped
to its own larger "city" boundary, Overture to a bbox). A normal `make load`
re-clips on write, but that needs a full refresh — and Geoapify credits may be
out. This script applies the *same* clip to the already-committed files in place,
with no network calls, so the data matches the polygons immediately.

It reuses fetcher.transform.clip so the result is identical to a fresh load.

Usage:
    python3 bin/clean/clean-out-of-bounds.py            # rewrite files in place
    python3 bin/clean/clean-out-of-bounds.py --dry-run  # report only, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Resolve the repo root from this file's location: bin/clean/<script> -> repo root.
# Absolute paths so the script behaves the same regardless of the caller's cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PLACES_DIR = _REPO_ROOT / 'data' / 'places'
_BOUNDARY_DIR = _REPO_ROOT / 'data' / 'boundaries'

# Make the fetcher package importable so we share the exact same clip logic.
sys.path.insert(0, str(_REPO_ROOT))

from fetcher.transform.clip import clip_to_geometry, load_boundary_geometry  # noqa: E402
from fetcher.transform.geojson_io import drop_unnamed, write_geojson  # noqa: E402


def _clean_file(geojson_path: Path, geom: dict, dry_run: bool) -> tuple[int, int]:
    """Clip one places file to geom and drop unnamed places. Returns (before, after)."""
    data = json.loads(geojson_path.read_text())
    before = len(data['features'])
    # Same two gates the fetcher applies: inside the polygon, and has a name.
    cleaned = clip_to_geometry(data, geom)
    cleaned, _ = drop_unnamed(cleaned)
    after = len(cleaned['features'])

    if after != before and not dry_run:
        # write_geojson reproduces the fetcher's compact format exactly.
        write_geojson(cleaned, str(geojson_path))
    return before, after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Report what would be removed without modifying any file.',
    )
    args = parser.parse_args(argv)

    if not _PLACES_DIR.is_dir():
        print(f'No places dir at {_PLACES_DIR}', file=sys.stderr)
        return 1

    total_dropped = 0
    missing_boundary = False

    # One city per subdir of data/places; clip every dataset file it contains.
    for city_dir in sorted(p for p in _PLACES_DIR.iterdir() if p.is_dir()):
        city_id = city_dir.name
        geom = load_boundary_geometry(_BOUNDARY_DIR, city_id)
        if geom is None:
            print(
                f'! {city_id}: no boundary at '
                f'{_BOUNDARY_DIR / city_id / "boundary.geojson"} — skipping',
                file=sys.stderr,
            )
            missing_boundary = True
            continue

        for geojson_path in sorted(city_dir.glob('*.geojson')):
            before, after = _clean_file(geojson_path, geom, args.dry_run)
            dropped = before - after
            total_dropped += dropped
            verb = 'would drop' if args.dry_run else 'dropped'
            flag = '' if dropped == 0 else f'  <-- {verb} {dropped}'
            rel = geojson_path.relative_to(_REPO_ROOT)
            print(f'  {rel}: {before} -> {after}{flag}')

    mode = 'dry run — no files changed' if args.dry_run else 'done'
    print(f'\n{mode}. Total places {"found" if args.dry_run else "removed"} '
          f'(out-of-bounds + unnamed): {total_dropped}')

    # Non-zero exit if we couldn't clip a city, so a caller notices.
    return 2 if missing_boundary else 0


if __name__ == '__main__':
    sys.exit(main())
