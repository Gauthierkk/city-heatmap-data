"""Shared GeoJSON feature builder, compact serialisation, guards, count report.

Output format (one schema, defined here, used by every provider):
  - properties: {id, name, shop, address}   (name may be null; address may be null)
  - address (when present): object with only the populated subset of
    {housenumber, street, postcode, city}
  - coordinates rounded to 6 decimal places
  - No `generated` timestamp (so unchanged weekly re-runs don't churn the file)
  - Compact JSON: json.dumps with separators=(',', ':')
  - Provenance is intentionally NOT recorded — the merge is source-agnostic.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# Address subfields, in display order. Providers may supply any subset.
ADDRESS_FIELDS = ('housenumber', 'street', 'postcode', 'city')


def clean_address(address: dict[str, Any] | None) -> dict[str, str] | None:
    """Keep only populated address subfields, in canonical order; None if empty."""
    if not address:
        return None
    cleaned = {
        k: str(address[k]).strip()
        for k in ADDRESS_FIELDS
        if address.get(k) is not None and str(address[k]).strip()
    }
    return cleaned or None


def make_feature(
    feat_id: str,
    name: str | None,
    shop: str,
    lon: float,
    lat: float,
    address: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one standard GeoJSON Feature. The single definition of the schema."""
    return {
        'type': 'Feature',
        'geometry': {
            'type': 'Point',
            'coordinates': [round(float(lon), 6), round(float(lat), 6)],
        },
        'properties': {
            # str() guards against providers returning a numeric name (e.g. "1234").
            'id': feat_id,
            'name': str(name).strip() or None if name is not None else None,
            'shop': shop,
            'address': clean_address(address),
        },
    }


def is_named(feature: dict[str, Any]) -> bool:
    """True if a feature has a non-empty name. Unnamed places are dropped everywhere."""
    return bool((feature['properties'].get('name') or '').strip())


def drop_unnamed(geojson: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Remove features with no name. Returns (filtered collection, count removed)."""
    kept = [f for f in geojson['features'] if is_named(f)]
    return {'type': 'FeatureCollection', 'features': kept}, len(geojson['features']) - len(kept)


def check_guard(geojson: dict[str, Any], city_id: str, dataset_id: str, min_features: int) -> None:
    """Raise SystemExit(1) if the feature count is below the guard threshold."""
    n = len(geojson['features'])
    if n < min_features:
        print(
            f'Refusing to write: only {n} features for {city_id}/{dataset_id} '
            f'(< {min_features}); the result looks partial or empty.',
            file=sys.stderr,
        )
        sys.exit(1)


def print_counts(geojson: dict[str, Any], city_id: str, dataset_id: str) -> None:
    """Print a per-type count table, sorted descending."""
    counts: dict[str, int] = {}
    for f in geojson['features']:
        shop = f['properties']['shop']
        counts[shop] = counts.get(shop, 0) + 1

    n = len(geojson['features'])
    with_addr = sum(1 for f in geojson['features'] if f['properties'].get('address'))
    print(f'Fetched {n} features for {city_id}/{dataset_id} ({with_addr} with address):')
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
