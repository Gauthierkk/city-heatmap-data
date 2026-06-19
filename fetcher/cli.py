"""Command-line interface for the fetcher package.

Commands:
  fetch-stores [city] [dataset]   — refresh store data from Overpass (+ Overture for fitness)
  fetch-boundary [city]           — refresh city admin boundary from OSM
  fetch-trees [city]              — refresh the Paris street-tree layer (Paris-only)
  fetch-transit [city]            — refresh the Paris transit-station layer (Paris-only)

Defaults: paris, food
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .cities import CITIES, city_by_id
from .providers import PROVIDER_NAMES, providers_for
from .providers.overpass import DATASETS, dataset_by_id
from .providers.boundary import fetch_boundary
from .providers.trees import fetch_trees, to_columnar
from .providers.transit import fetch_transit
from .transform.aggregate import aggregate
from .transform.clip import clip_to_geometry, load_boundary_geometry
from .transform.geojson_io import check_guard, print_counts, write_geojson


# Default output dirs: local folders under data/ at the repo root. Resolved to
# absolute paths so the write target is unambiguous regardless of the process's
# working directory. Layout from this file: fetcher/cli.py → fetcher/ → <repo root>/data .
# Stores go to data/places/, city boundaries to data/boundaries/.
_DATA_ROOT = Path(__file__).resolve().parent.parent / 'data'
_DATA_DIR = _DATA_ROOT / 'places'
_BOUNDARY_DIR = _DATA_ROOT / 'boundaries'

# Minimum trees expected (after clipping to the Paris boundary) — guards against a
# partial/empty export. The raw dataset holds ~218k; clipping drops the Paris-owned
# cemeteries outside the admin polygon, so this stays well below the live total.
_TREES_MIN = 150_000

# Minimum transit stations expected inside Paris (~297 live) — guards a partial fetch.
_TRANSIT_MIN = 200

# Drop guard: refuse to write if the new aggregated total is below this fraction
# of the committed file's feature count (protects against a silent provider outage).
_DROP_GUARD_FRACTION = 0.70


def _is_fetch_blocked(city_id: str, force: bool) -> bool:
    """Return True if fetching for this city should be skipped.

    Soft-deprecated cities (CityDef.deprecated) keep their committed data but are
    not refreshed unless `--force` is passed. Prints a one-line notice and returns
    True when a fetch is skipped; returns False when the fetch should proceed.
    """
    city = city_by_id(city_id)
    if not city.deprecated:
        return False
    if force:
        print(f'{city_id} is deprecated; fetching anyway because --force was passed.')
        return False
    print(
        f'Skipping {city_id}: this city is deprecated. Its committed data is kept '
        f'as-is. Pass --force to refresh it anyway.'
    )
    return True


def _check_drop_guard(merged_geojson: dict, out_file: Path, city_id: str, dataset_id: str) -> None:
    """Refuse to write if the new total dropped below 70 % of the committed file."""
    if not out_file.exists():
        return  # no committed baseline — nothing to check
    try:
        existing = json.loads(out_file.read_text())
        # Trees ship as `trees-columnar-v1` (no `features`; one entry per
        # coordinate); every other layer is a FeatureCollection.
        existing_count = len(existing.get('features') or existing.get('coordinates') or [])
    except Exception:
        return  # can't read existing file — skip guard

    new_count = len(merged_geojson.get('features') or merged_geojson.get('coordinates') or [])
    threshold = existing_count * _DROP_GUARD_FRACTION
    if new_count < threshold:
        print(
            f'Drop guard triggered for {city_id}/{dataset_id}: '
            f'new total {new_count} < {_DROP_GUARD_FRACTION:.0%} of '
            f'committed {existing_count} ({threshold:.0f}). Refusing to write — '
            'a provider may be down. Investigate, or narrow --providers and retry.',
            file=sys.stderr,
        )
        sys.exit(1)


def _clip_to_city_or_warn(fc: dict, city_id: str, noun: str = 'features') -> dict:
    """Clip a FeatureCollection to the committed city boundary.

    Returns the clipped collection, or the input unchanged (with a warning) when no
    boundary file exists yet. `noun` labels the dropped count (features/trees/stations).
    """
    boundary_geom = load_boundary_geometry(_BOUNDARY_DIR, city_id)
    if boundary_geom is None:
        print(
            f'Warning: no boundary at {_BOUNDARY_DIR / city_id / "boundary.geojson"}; '
            f'skipping clip for {city_id}. Run `make boundary {city_id}` first.',
            file=sys.stderr,
        )
        return fc
    before = len(fc['features'])
    clipped = clip_to_geometry(fc, boundary_geom)
    print(f'  clipped to boundary: dropped {before - len(clipped["features"])} of '
          f'{before} {noun} outside {city_id}')
    return clipped


def _prepare_out_file(out_dir: Path, city_id: str, filename: str) -> Path:
    """Resolve <out_dir>/<city_id>/<filename> and ensure its parent dir exists."""
    out_file = out_dir / city_id / filename
    out_file.parent.mkdir(parents=True, exist_ok=True)
    return out_file


def _fetch_stores_one(
    city_id: str,
    dataset_id: str,
    out_dir: Path,
    allow: set[str] | None,
    deny: set[str],
) -> None:
    """Fetch one city + dataset from every selected provider, aggregate, and write."""
    city = city_by_id(city_id)
    dataset = dataset_by_id(dataset_id)

    selected = providers_for(dataset_id, allow, deny)
    print(f'Providers for {city_id}/{dataset_id}: {", ".join(p.name for p in selected) or "(none)"}')

    collections = []
    osm_ok = False
    for provider in selected:
        try:
            fc = provider.fetch(city, dataset_id)
        except Exception as exc:
            # A secondary provider failing is non-fatal — log and carry on so the
            # run still produces data from the others.
            print(f'Warning: provider "{provider.name}" failed: {exc}', file=sys.stderr)
            continue
        collections.append(fc)
        if provider.name == 'osm' and fc.get('features'):
            osm_ok = True

    # OSM is the comprehensive backbone — its min-features guard still applies.
    if not osm_ok:
        print(f'Refusing to write {city_id}/{dataset_id}: OSM returned no data.', file=sys.stderr)
        sys.exit(1)

    # Fitness types (gym/yoga/...) are alternate labels for one venue, so match
    # across types there; food types are distinct categories, so require same type.
    final_geojson = aggregate(collections, cross_type=(dataset_id == 'fitness'))

    # Clip to the city's boundary polygon. Only OSM restricts its query to the
    # admin area server-side; Geoapify (its own city boundary) and Overture (bbox)
    # leak places outside the zone the front end draws. This is the single
    # source-agnostic gate that keeps every provider inside the clip zone.
    final_geojson = _clip_to_city_or_warn(final_geojson, city_id)

    check_guard(final_geojson, city_id, dataset_id, dataset['min_features'])

    # Nested layout: <out-dir>/<city>/<dataset>.geojson  (e.g. data/places/paris/food.geojson)
    out_file = _prepare_out_file(out_dir, city_id, f'{dataset_id}.geojson')

    _check_drop_guard(final_geojson, out_file, city_id, dataset_id)
    print_counts(final_geojson, city_id, dataset_id)
    write_geojson(final_geojson, str(out_file))


def cmd_fetch_stores(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _DATA_DIR

    # Provider selection: --providers is an allowlist; --no-overture / --no-geoapify
    # are convenience deny flags layered on top.
    allow: set[str] | None = None
    if args.providers:
        allow = {p.strip() for p in args.providers.split(',') if p.strip()}
        unknown = allow - set(PROVIDER_NAMES)
        if unknown:
            raise ValueError(f'Unknown provider(s): {", ".join(sorted(unknown))}. '
                             f'Available: {", ".join(PROVIDER_NAMES)}')
    deny: set[str] = set()
    if getattr(args, 'no_overture', False):
        deny.add('overture')
    if getattr(args, 'no_geoapify', False):
        deny.add('geoapify')
    if getattr(args, 'no_sirene', False):
        deny.add('sirene')

    if args.all:
        # All cities × datasets with a polite ~10 s sleep between provider rounds
        combos = [(c, d) for c in CITIES for d in DATASETS]
        first = True
        for city_id, dataset_id in combos:
            # Skip deprecated cities entirely (across every dataset) unless --force.
            if _is_fetch_blocked(city_id, args.force):
                continue
            if not first:
                print('Sleeping 10 s between provider rounds ...')
                time.sleep(10)
            first = False
            print(f'--- {city_id}/{dataset_id} ---')
            _fetch_stores_one(city_id, dataset_id, out_dir, allow, deny)
    else:
        city_id = args.city or 'paris'
        dataset_id = args.dataset or 'food'
        # Validate early so we get a clean error before hitting the network
        city_by_id(city_id)
        dataset_by_id(dataset_id)
        if _is_fetch_blocked(city_id, args.force):
            return
        _fetch_stores_one(city_id, dataset_id, out_dir, allow, deny)


def cmd_fetch_boundary(args: argparse.Namespace) -> None:
    city_id = args.city or 'paris'
    city = city_by_id(city_id)
    if _is_fetch_blocked(city_id, args.force):
        return
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _BOUNDARY_DIR

    feature = fetch_boundary(city)

    # Nested layout: <out-dir>/<city>/boundary.geojson  (e.g. data/boundaries/paris/boundary.geojson)
    out_file = _prepare_out_file(out_dir, city_id, 'boundary.geojson')
    write_geojson(feature, str(out_file))


def cmd_fetch_trees(args: argparse.Namespace) -> None:
    """Fetch the Paris tree layer, clip to the city boundary, and write it.

    Separate from `fetch-stores`: no providers/aggregation/OSM backbone — one
    authoritative source, one Point feature per tree carrying its species in
    French + English. Reuses the feature-based clip + guards since it is a normal
    FeatureCollection.
    """
    city_id = args.city or 'paris'
    city = city_by_id(city_id)
    # Trees are a separate pipeline but land alongside the store layers, so the
    # front end loads every <city>/*.geojson from one folder.
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _DATA_DIR

    fc = fetch_trees(city)

    # Clip to the committed boundary polygon — the export includes Paris-owned
    # cemeteries (Pantin, Bagneux, Thiais) that sit outside the admin area the
    # front end draws.
    fc = _clip_to_city_or_warn(fc, city_id, noun='trees')

    check_guard(fc, city_id, 'trees', _TREES_MIN)

    out_file = _prepare_out_file(out_dir, city_id, 'trees.geojson')

    _check_drop_guard(fc, out_file, city_id, 'trees')

    print(f'Fetched {len(fc["features"])} trees for {city_id}')
    # Ship the compact `trees-columnar-v1` shape (species lookup table + parallel
    # coordinate/index arrays), ~5–7× smaller than the FeatureCollection the
    # clip + guards above operate on. See providers/trees.py:to_columnar.
    write_geojson(to_columnar(fc), str(out_file))


def cmd_fetch_transit(args: argparse.Namespace) -> None:
    """Fetch the Paris transit-station layer, clip to the boundary, and write it.

    Separate from `fetch-stores`: a single authoritative source, one point per
    station, a `categories` list per station (no merge, no OSM backbone). Reuses
    the feature-based clip + guards since it is a normal FeatureCollection.
    """
    city_id = args.city or 'paris'
    city = city_by_id(city_id)
    # Lands alongside the store layers so the front end loads one folder per city.
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _DATA_DIR

    fc = fetch_transit(city)

    # Clip to the committed boundary — the source is region-wide (Île-de-France),
    # so this keeps only the ~297 stations inside Paris intra-muros.
    fc = _clip_to_city_or_warn(fc, city_id, noun='stations')

    check_guard(fc, city_id, 'transit', _TRANSIT_MIN)

    out_file = _prepare_out_file(out_dir, city_id, 'transit.geojson')

    _check_drop_guard(fc, out_file, city_id, 'transit')

    # Per-category station count (a multimodal station counts in each category).
    counts: dict[str, int] = {}
    for f in fc['features']:
        for c in f['properties']['categories']:
            counts[c] = counts.get(c, 0) + 1
    print(f'Fetched {len(fc["features"])} transit stations for {city_id}/transit:')
    for c, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {c:<14} {cnt}')

    write_geojson(fc, str(out_file))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python3 -m fetcher',
        description='Fetch store / boundary data from Overpass and write GeoJSON.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # --- fetch-stores ---
    p_stores = sub.add_parser(
        'fetch-stores',
        help='Refresh store data from Overpass API (+ Overture for fitness)',
    )
    p_stores.add_argument(
        'city',
        nargs='?',
        default=None,
        help=f'City id (default: paris). Available: {", ".join(CITIES)}',
    )
    p_stores.add_argument(
        'dataset',
        nargs='?',
        default=None,
        help=f'Dataset id (default: food). Available: {", ".join(DATASETS)}',
    )
    p_stores.add_argument(
        '--all',
        action='store_true',
        help='Fetch all cities × datasets (with ~10 s sleep between calls)',
    )
    p_stores.add_argument(
        '--out-dir',
        default=None,
        metavar='DIR',
        help='Write GeoJSON files here instead of the default data/ folder',
    )
    p_stores.add_argument(
        '--providers',
        default=None,
        metavar='LIST',
        help=(
            'Comma-separated allowlist of providers to query '
            f'(available: {", ".join(PROVIDER_NAMES)}). Default: all that serve the dataset.'
        ),
    )
    p_stores.add_argument(
        '--no-overture',
        action='store_true',
        default=False,
        help='Skip the Overture provider (e.g. when DuckDB/S3 is unavailable).',
    )
    p_stores.add_argument(
        '--no-geoapify',
        action='store_true',
        default=False,
        help='Skip the Geoapify provider (e.g. when the API key is unset or over quota).',
    )
    p_stores.add_argument(
        '--no-sirene',
        action='store_true',
        default=False,
        help='Skip the SIRENE provider (Paris-only food/fitness enrichment from data.gouv).',
    )
    p_stores.add_argument(
        '--force',
        action='store_true',
        default=False,
        help='Fetch deprecated cities (e.g. nyc, austin) too. By default they are skipped.',
    )

    # --- fetch-boundary ---
    p_boundary = sub.add_parser(
        'fetch-boundary',
        help='Refresh city admin boundary from OSM',
    )
    p_boundary.add_argument(
        'city',
        nargs='?',
        default=None,
        help=f'City id (default: paris). Available: {", ".join(CITIES)}',
    )
    p_boundary.add_argument(
        '--out-dir',
        default=None,
        metavar='DIR',
        help='Write GeoJSON file here instead of the default data/boundaries/ folder',
    )
    p_boundary.add_argument(
        '--force',
        action='store_true',
        default=False,
        help='Fetch deprecated cities (e.g. nyc, austin) too. By default they are skipped.',
    )

    # --- fetch-trees ---
    p_trees = sub.add_parser(
        'fetch-trees',
        help='Refresh the Paris street-tree layer from opendata.paris.fr (Paris-only)',
    )
    p_trees.add_argument(
        'city',
        nargs='?',
        default=None,
        help='City id (default: paris). Only paris has a tree dataset wired up.',
    )
    p_trees.add_argument(
        '--out-dir',
        default=None,
        metavar='DIR',
        help='Write GeoJSON file here instead of the default data/places/ folder',
    )

    # --- fetch-transit ---
    p_transit = sub.add_parser(
        'fetch-transit',
        help='Refresh the Paris public-transit station layer from IDF Mobilités (Paris-only)',
    )
    p_transit.add_argument(
        'city',
        nargs='?',
        default=None,
        help='City id (default: paris). Only paris has a transit dataset wired up.',
    )
    p_transit.add_argument(
        '--out-dir',
        default=None,
        metavar='DIR',
        help='Write GeoJSON file here instead of the default data/places/ folder',
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == 'fetch-stores':
            cmd_fetch_stores(args)
        elif args.command == 'fetch-boundary':
            cmd_fetch_boundary(args)
        elif args.command == 'fetch-trees':
            cmd_fetch_trees(args)
        elif args.command == 'fetch-transit':
            cmd_fetch_transit(args)
        else:
            parser.print_help()
            sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
