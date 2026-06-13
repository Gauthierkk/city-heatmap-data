"""Merge OSM and Overture fitness feature collections.

An Overture place is considered a duplicate of an OSM feature when:
  1. The two points are within 100 m of each other, AND
  2. The names roughly match: one normalised name contains the other,
     OR token-Jaccard similarity ≥ 0.5.

Matching is done across ALL fitness types (not per-type) because the same
business may be typed 'gym' in one source and 'yoga' in the other.
When a duplicate is found, the OSM feature wins.

Merged output: all OSM features + non-duplicate Overture features.
Every feature gets a 'source' property: 'osm' or 'overture'.
"""

from __future__ import annotations

import math
from typing import Any

from .conflate import _haversine_m, _norm_name  # reuse helpers

# Maximum distance (metres) for two features to be considered co-located.
MERGE_RADIUS_M = 100.0


# ---------------------------------------------------------------------------
# Name similarity helpers
# ---------------------------------------------------------------------------

def _tokens(norm: str) -> frozenset[str]:
    """Split a normalised name into a frozenset of whitespace-delimited tokens."""
    return frozenset(norm.split()) if norm else frozenset()


def _names_match(n1: str | None, n2: str | None) -> bool:
    """Return True when the two names are "roughly" the same.

    Rules (applied to normalised forms):
      - One normalised string contains the other (substring containment), OR
      - Token-Jaccard similarity ≥ 0.5.
    """
    a = _norm_name(n1)
    b = _norm_name(n2)
    if not a or not b:
        return False  # unnamed features are never matched
    # Substring containment
    if a in b or b in a:
        return True
    # Token-Jaccard
    ta = _tokens(a)
    tb = _tokens(b)
    union = ta | tb
    if not union:
        return False
    jaccard = len(ta & tb) / len(union)
    return jaccard >= 0.5


# ---------------------------------------------------------------------------
# Spatial bucket index
# ---------------------------------------------------------------------------

# Bucket size (degrees) — ~9 km at mid-latitudes; we only need to look one
# bucket in every direction to cover the 100 m merge radius.
_BUCKET_DEG = 0.1


def _bucket_key(lon: float, lat: float) -> tuple[int, int]:
    return (int(math.floor(lon / _BUCKET_DEG)), int(math.floor(lat / _BUCKET_DEG)))


def _build_index(features: list[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    idx: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for f in features:
        lon, lat = f['geometry']['coordinates']
        key = _bucket_key(lon, lat)
        idx.setdefault(key, []).append(f)
    return idx


def _candidates(
    lon: float,
    lat: float,
    idx: dict[tuple[int, int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return all OSM features whose bucket neighbours the query point."""
    bx, by = _bucket_key(lon, lat)
    result: list[dict[str, Any]] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            result.extend(idx.get((bx + dx, by + dy), []))
    return result


# ---------------------------------------------------------------------------
# Main merge function
# ---------------------------------------------------------------------------

def merge_fitness(
    osm_fc: dict[str, Any],
    overture_fc: dict[str, Any],
) -> dict[str, Any]:
    """Return a merged FeatureCollection: all OSM + non-duplicate Overture features.

    Side effects: adds/overwrites the 'source' property on every output feature.
    Logs: OSM count, Overture candidates, duplicates dropped, final total.
    """
    osm_features: list[dict[str, Any]] = list(osm_fc.get('features') or [])
    overture_features: list[dict[str, Any]] = list(overture_fc.get('features') or [])

    # Tag all OSM features as source='osm'
    for f in osm_features:
        f['properties']['source'] = 'osm'

    print(f'OSM features:          {len(osm_features)}')
    print(f'Overture candidates:   {len(overture_features)}')

    # Build spatial index over OSM features
    osm_index = _build_index(osm_features)

    kept_overture: list[dict[str, Any]] = []
    duplicate_count = 0

    for ov in overture_features:
        lon, lat = ov['geometry']['coordinates']
        ov_name = ov['properties'].get('name')

        is_dup = False
        for osm in _candidates(lon, lat, osm_index):
            osm_lon, osm_lat = osm['geometry']['coordinates']
            dist = _haversine_m(lat, lon, osm_lat, osm_lon)
            if dist <= MERGE_RADIUS_M and _names_match(ov_name, osm['properties'].get('name')):
                is_dup = True
                break

        if is_dup:
            duplicate_count += 1
        else:
            kept_overture.append(ov)

    merged = osm_features + kept_overture
    print(f'Duplicates dropped:    {duplicate_count}')
    print(f'Final total:           {len(merged)}')

    return {'type': 'FeatureCollection', 'features': merged}
