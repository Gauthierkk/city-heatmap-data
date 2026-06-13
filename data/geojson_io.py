"""Feature building, compact serialisation, guards, and per-type count report.

Mirrors the output format from the retired scripts/fetch-stores.mjs:
  - properties: {id, name, shop}  (name may be null)
  - coordinates rounded to 6 decimal places
  - No `generated` timestamp
  - Compact JSON: json.dumps with separators=(',', ':')
  - Key order: type → geometry → properties  (Feature),
               type → features  (FeatureCollection)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from .conflate import conflate


def _build_feature(
    el: dict[str, Any],
    normalise: Callable[[dict[str, str]], str | None],
) -> dict[str, Any] | None:
    """Convert a single Overpass element to a GeoJSON Feature, or None to skip."""
    lat = el.get('lat') or (el.get('center') or {}).get('lat')
    lon = el.get('lon') or (el.get('center') or {}).get('lon')
    if lat is None or lon is None:
        return None

    tags: dict[str, str] = el.get('tags') or {}
    canonical = normalise(tags)
    if canonical is None:
        return None

    feat_id = f"{el['type']}/{el['id']}"

    return {
        'type': 'Feature',
        'geometry': {
            'type': 'Point',
            'coordinates': [round(float(lon), 6), round(float(lat), 6)],
        },
        'properties': {
            'id': feat_id,
            'name': tags.get('name'),  # None when absent — serialises as null
            'shop': canonical,
            'source': 'osm',
        },
    }


def to_geojson(
    overpass_response: dict[str, Any],
    normalise: Callable[[dict[str, str]], str | None],
) -> dict[str, Any]:
    """Convert a raw Overpass response to a deduplicated GeoJSON FeatureCollection."""
    features: list[dict[str, Any]] = []
    seen: set[str] = set()

    for el in overpass_response.get('elements') or []:
        feat = _build_feature(el, normalise)
        if feat is None:
            continue
        feat_id = feat['properties']['id']
        if feat_id in seen:
            continue
        seen.add(feat_id)
        features.append(feat)

    deduped = conflate(features)

    # No `generated` timestamp (keeps weekly re-runs from churning the committed file)
    return {
        'type': 'FeatureCollection',
        'features': deduped,
    }


def check_guard(geojson: dict[str, Any], city_id: str, dataset_id: str, min_features: int) -> None:
    """Raise SystemExit(1) if the feature count is below the guard threshold."""
    n = len(geojson['features'])
    if n < min_features:
        print(
            f'Refusing to write: only {n} features for {city_id}/{dataset_id} '
            f'(< {min_features}); the Overpass result looks partial or empty.',
            file=sys.stderr,
        )
        sys.exit(1)


def print_counts(geojson: dict[str, Any], city_id: str, dataset_id: str) -> None:
    """Print a per-type count table, sorted descending (mirrors Node script output)."""
    counts: dict[str, int] = {}
    for f in geojson['features']:
        shop = f['properties']['shop']
        counts[shop] = counts.get(shop, 0) + 1

    n = len(geojson['features'])
    print(f'Fetched {n} features for {city_id}/{dataset_id}:')
    for shop, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {shop:<14} {count}')


def write_geojson(geojson: dict[str, Any], out_path: str) -> None:
    """Serialise to compact JSON and write to out_path, creating directories as needed."""
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    compact = json.dumps(geojson, separators=(',', ':'), ensure_ascii=False)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(compact)
    print(f'Wrote {out_path}')
