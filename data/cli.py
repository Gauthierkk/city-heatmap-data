"""Command-line interface for the data package.

Commands:
  fetch-stores [city] [dataset]   — refresh store data from Overpass (+ Overture for fitness)
  fetch-boundary [city]           — refresh city admin boundary from OSM

Defaults: paris, food  (same as the retired Node scripts)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .cities import CITIES, city_by_id
from .overpass import DATASETS, dataset_by_id, fetch_overpass
from .geojson_io import check_guard, print_counts, to_geojson, write_geojson
from .boundary import fetch_boundary


# Default output dir: the sibling front-end repo's public/data/. Standard layout
# is  <parent>/city-heatmap-data/  and  <parent>/city-heatmap-front/ , so from this
# file:  data/ → city-heatmap-data/ → <parent>/ → city-heatmap-front/public/data .
# The weekly-refresh wrapper always passes --out-dir explicitly; this is the fallback.
_PUBLIC_DATA = Path(__file__).parent.parent.parent / 'city-heatmap-front' / 'public' / 'data'

# Drop guard: refuse to write if new merged total is below this fraction of
# the committed file's feature count (protects against silent Overture outages).
_DROP_GUARD_FRACTION = 0.70


def _check_drop_guard(merged_geojson: dict, out_file: Path, city_id: str, dataset_id: str) -> None:
    """Refuse to write if merged total dropped below 70 % of the committed file."""
    if not out_file.exists():
        return  # no committed baseline — nothing to check
    try:
        existing = json.loads(out_file.read_text())
        existing_count = len(existing.get('features', []))
    except Exception:
        return  # can't read existing file — skip guard

    new_count = len(merged_geojson.get('features', []))
    threshold = existing_count * _DROP_GUARD_FRACTION
    if new_count < threshold:
        print(
            f'Drop guard triggered for {city_id}/{dataset_id}: '
            f'new total {new_count} < {_DROP_GUARD_FRACTION:.0%} of '
            f'committed {existing_count} ({threshold:.0f}). '
            'Refusing to write. Re-run with --no-overture if Overture S3 is unavailable.',
            file=sys.stderr,
        )
        sys.exit(1)


def _fetch_stores_one(city_id: str, dataset_id: str, out_dir: Path, no_overture: bool = False) -> None:
    """Fetch one city + dataset combination and write the GeoJSON file."""
    city = city_by_id(city_id)
    dataset = dataset_by_id(dataset_id)

    query = dataset['build_query'](city)
    raw = fetch_overpass(query)
    osm_geojson = to_geojson(raw, dataset['normalise'])

    # OSM guard applied BEFORE merging
    check_guard(osm_geojson, city_id, dataset_id, dataset['min_features'])

    if dataset_id == 'fitness' and not no_overture:
        # Overture merge path
        from .overture import fetch_overture_fitness
        from .merge import merge_fitness

        try:
            overture_geojson = fetch_overture_fitness(city)
        except ImportError as exc:
            print(f'Warning: {exc}', file=sys.stderr)
            print('Falling back to OSM-only (as if --no-overture were set).', file=sys.stderr)
            overture_geojson = {'type': 'FeatureCollection', 'features': []}

        final_geojson = merge_fitness(osm_geojson, overture_geojson)
    else:
        # OSM-only path (food, or fitness with --no-overture)
        final_geojson = osm_geojson

    # Nested layout: public/data/<city>/<dataset>.geojson  (e.g. paris/food.geojson)
    out_file = out_dir / city_id / f'{dataset_id}.geojson'
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Drop guard: compare against committed baseline (fitness only, where merge can fluctuate).
    # Skip when --no-overture is set: the caller knows the output will be OSM-only.
    if dataset_id == 'fitness' and not no_overture:
        _check_drop_guard(final_geojson, out_file, city_id, dataset_id)

    print_counts(final_geojson, city_id, dataset_id)
    write_geojson(final_geojson, str(out_file))


def cmd_fetch_stores(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir) if args.out_dir else _PUBLIC_DATA
    no_overture = getattr(args, 'no_overture', False)

    if args.all:
        # All cities × datasets with a polite ~10 s sleep between Overpass calls
        combos = [(c, d) for c in CITIES for d in DATASETS]
        for i, (city_id, dataset_id) in enumerate(combos):
            if i > 0:
                print('Sleeping 10 s between Overpass calls ...')
                time.sleep(10)
            print(f'--- {city_id}/{dataset_id} ---')
            _fetch_stores_one(city_id, dataset_id, out_dir, no_overture=no_overture)
    else:
        city_id = args.city or 'paris'
        dataset_id = args.dataset or 'food'
        # Validate early so we get a clean error before hitting the network
        city_by_id(city_id)
        dataset_by_id(dataset_id)
        _fetch_stores_one(city_id, dataset_id, out_dir, no_overture=no_overture)


def cmd_fetch_boundary(args: argparse.Namespace) -> None:
    city_id = args.city or 'paris'
    city = city_by_id(city_id)
    out_dir = Path(args.out_dir) if args.out_dir else _PUBLIC_DATA

    feature = fetch_boundary(city)

    # Nested layout: public/data/<city>/boundary.geojson
    out_file = out_dir / city_id / 'boundary.geojson'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    write_geojson(feature, str(out_file))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python3 -m data',
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
        help='Write GeoJSON files here instead of public/data/',
    )
    p_stores.add_argument(
        '--no-overture',
        action='store_true',
        default=False,
        help=(
            'Skip the Overture merge step and use OSM data only. '
            'Useful when DuckDB/S3 is unavailable or for debugging.'
        ),
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
        help='Write GeoJSON file here instead of public/data/',
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
        else:
            parser.print_help()
            sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
