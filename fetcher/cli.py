"""Command-line interface for the fetcher package.

Commands:
  fetch-stores [city] [dataset]   — refresh store data from Overpass (+ Overture for fitness)
  fetch-boundary [city]           — refresh city admin boundary from OSM

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
from .transform.aggregate import aggregate
from .transform.geojson_io import check_guard, print_counts, write_geojson


# Default output dir: the sibling front-end repo's public/data/. Standard layout
# is  <parent>/city-heatmap-data/  and  <parent>/city-heatmap-front/ , so from this
# file:  fetcher/ → city-heatmap-data/ → <parent>/ → city-heatmap-front/public/data .
# The weekly-refresh wrapper always passes --out-dir explicitly; this is the fallback.
_PUBLIC_DATA = Path(__file__).parent.parent.parent / 'city-heatmap-front' / 'public' / 'data'

# Drop guard: refuse to write if the new aggregated total is below this fraction
# of the committed file's feature count (protects against a silent provider outage).
_DROP_GUARD_FRACTION = 0.70


def _check_drop_guard(merged_geojson: dict, out_file: Path, city_id: str, dataset_id: str) -> None:
    """Refuse to write if the new total dropped below 70 % of the committed file."""
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
            f'committed {existing_count} ({threshold:.0f}). Refusing to write — '
            'a provider may be down. Investigate, or narrow --providers and retry.',
            file=sys.stderr,
        )
        sys.exit(1)


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
    check_guard(final_geojson, city_id, dataset_id, dataset['min_features'])

    # Nested layout: public/data/<city>/<dataset>.geojson  (e.g. paris/food.geojson)
    out_file = out_dir / city_id / f'{dataset_id}.geojson'
    out_file.parent.mkdir(parents=True, exist_ok=True)

    _check_drop_guard(final_geojson, out_file, city_id, dataset_id)
    print_counts(final_geojson, city_id, dataset_id)
    write_geojson(final_geojson, str(out_file))


def cmd_fetch_stores(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir) if args.out_dir else _PUBLIC_DATA

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

    if args.all:
        # All cities × datasets with a polite ~10 s sleep between provider rounds
        combos = [(c, d) for c in CITIES for d in DATASETS]
        for i, (city_id, dataset_id) in enumerate(combos):
            if i > 0:
                print('Sleeping 10 s between provider rounds ...')
                time.sleep(10)
            print(f'--- {city_id}/{dataset_id} ---')
            _fetch_stores_one(city_id, dataset_id, out_dir, allow, deny)
    else:
        city_id = args.city or 'paris'
        dataset_id = args.dataset or 'food'
        # Validate early so we get a clean error before hitting the network
        city_by_id(city_id)
        dataset_by_id(dataset_id)
        _fetch_stores_one(city_id, dataset_id, out_dir, allow, deny)


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
        help='Write GeoJSON files here instead of public/data/',
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
