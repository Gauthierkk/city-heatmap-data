"""Source-agnostic aggregation of features from N providers into one deduped set.

Replaces the old pairwise conflate()/merge_fitness() pair. Every provider is
treated identically - the aggregator NEVER ranks osm/overture/geoapify.

Two features are the same business when:
  1. they are within `radius_m` of each other, AND
  2. their names "roughly" match (substring containment OR token-Jaccard >= 0.5).
Unnamed features never match anything (we can't tell them apart safely).
Matching is across all `shop` types, since the same venue may be typed
differently by different sources (e.g. gym vs yoga).

For each cluster of duplicates we keep ONE representative:
  - the most COMPLETE record wins - score = (name? ) + (#address subfields) + (shop?)
  - ties broken by lowest `id` (lexicographic) - deterministic, source-neutral
Then we BACKFILL the representative's missing `name` / address subfields with the
most-detailed value found anywhere in the cluster (longest non-empty string,
tie-break by id). No provider preference enters anywhere.

Output is sorted by `id` so multi-source / tiled fetch order can't churn the file.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from ..geo import haversine_m
from .geojson_io import ADDRESS_FIELDS, is_named

# Max distance (m) for two features to be considered the same place.
DEFAULT_RADIUS_M = 100.0

# Spatial bucket size (degrees) - ~9 km at mid-latitudes; one bucket in each
# direction covers the merge radius comfortably.
_BUCKET_DEG = 0.1


# ---------------------------------------------------------------------------
# Name normalisation + similarity (was conflate.py / merge.py)
# ---------------------------------------------------------------------------

def _norm_name(name: str | None) -> str:
    """Lowercase, strip diacritics, collapse punctuation/whitespace."""
    if not name:
        return ''
    nfd = unicodedata.normalize('NFD', str(name))
    stripped = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
    no_punct = re.sub(r'[^\w\s]', ' ', stripped.lower())
    return re.sub(r'\s+', ' ', no_punct).strip()


def _tokens(norm: str) -> frozenset[str]:
    return frozenset(norm.split()) if norm else frozenset()


def _names_match(n1: str | None, n2: str | None) -> bool:
    """True when two names are roughly the same (containment or Jaccard >= 0.5)."""
    a = _norm_name(n1)
    b = _norm_name(n2)
    if not a or not b:
        return False  # unnamed features never match
    if a in b or b in a:
        return True
    ta, tb = _tokens(a), _tokens(b)
    union = ta | tb
    return bool(union) and len(ta & tb) / len(union) >= 0.5


def _bucket_key(lon: float, lat: float) -> tuple[int, int]:
    return (int(math.floor(lon / _BUCKET_DEG)), int(math.floor(lat / _BUCKET_DEG)))


# ---------------------------------------------------------------------------
# Completeness scoring + field backfill (source-agnostic)
# ---------------------------------------------------------------------------

def _completeness(props: dict[str, Any]) -> int:
    """Higher = more complete. Counts name, each address subfield, and shop."""
    score = 1 if props.get('name') else 0
    score += 1 if props.get('shop') else 0
    addr = props.get('address') or {}
    score += sum(1 for k in ADDRESS_FIELDS if addr.get(k))
    return score


def _best_value(values: list[tuple[str, str]]) -> str | None:
    """Pick the most-detailed value source-neutrally: longest, tie-break by id.

    `values` is a list of (feat_id, value) for non-empty candidates.
    """
    if not values:
        return None
    # longest value wins; ties broken by lowest id
    return min(values, key=lambda iv: (-len(iv[1]), iv[0]))[1]


def _merge_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse a duplicate cluster into one representative + backfilled fields."""
    if len(cluster) == 1:
        return cluster[0]

    # Representative: most complete, then lowest id.
    rep = min(
        cluster,
        key=lambda f: (-_completeness(f['properties']), f['properties']['id']),
    )
    rep = {  # shallow copy so we don't mutate the input feature
        'type': 'Feature',
        'geometry': rep['geometry'],
        'properties': dict(rep['properties']),
    }
    props = rep['properties']

    # Backfill name from the most-detailed name in the cluster if rep lacks one.
    if not props.get('name'):
        names = [(f['properties']['id'], f['properties']['name'])
                 for f in cluster if f['properties'].get('name')]
        props['name'] = _best_value(names)

    # Backfill each address subfield independently (source-neutral, most detailed).
    merged_addr: dict[str, str] = dict(props.get('address') or {})
    for field in ADDRESS_FIELDS:
        if merged_addr.get(field):
            continue
        candidates = [
            (f['properties']['id'], (f['properties'].get('address') or {})[field])
            for f in cluster
            if (f['properties'].get('address') or {}).get(field)
        ]
        val = _best_value(candidates)
        if val:
            merged_addr[field] = val
    # Re-emit in canonical field order for stable, tidy output.
    props['address'] = {k: merged_addr[k] for k in ADDRESS_FIELDS if merged_addr.get(k)} or None

    return rep


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(
    collections: list[dict[str, Any]],
    radius_m: float = DEFAULT_RADIUS_M,
    cross_type: bool = False,
) -> dict[str, Any]:
    """Merge several FeatureCollections into one deduped, source-agnostic set.

    cross_type=False (default): two records merge only if they share the same
    `shop` type - so a coarse-category source can't overwrite a finer one
    (e.g. Geoapify 'bakery' must not absorb OSM 'pastry'). Use this for food.

    cross_type=True: match across types, because the same venue is legitimately
    labelled differently by different sources (e.g. gym vs yoga). Use for fitness.
    """
    # Flatten, then build clusters incrementally against a spatial index of
    # cluster representatives (first feature added to each cluster).
    all_features: list[dict[str, Any]] = []
    for fc in collections:
        all_features.extend(fc.get('features') or [])

    # Drop unnamed places from every source up front. They never merge with
    # anything (an unnamed feature always forms its own singleton cluster), and
    # an entry with no name isn't useful to show - so remove them outright.
    incoming = len(all_features)
    all_features = [f for f in all_features if is_named(f)]
    dropped_unnamed = incoming - len(all_features)

    # Sort by id up front so clustering is independent of provider order and of
    # each provider's (not-guaranteed-stable) internal ordering - otherwise a
    # reshuffle from Overpass/Geoapify could churn the committed file week to week.
    all_features.sort(key=lambda f: f['properties']['id'])

    clusters: list[list[dict[str, Any]]] = []
    index: dict[tuple[int, int], list[int]] = {}  # bucket -> cluster indices

    for feat in all_features:
        lon, lat = feat['geometry']['coordinates']
        name = feat['properties'].get('name')
        shop = feat['properties'].get('shop')
        bx, by = _bucket_key(lon, lat)

        joined = None
        if name:  # unnamed features never merge; always start their own cluster
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for ci in index.get((bx + dx, by + dy), []):
                        anchor = clusters[ci][0]
                        if not cross_type and anchor['properties'].get('shop') != shop:
                            continue  # same-type required
                        a_lon, a_lat = anchor['geometry']['coordinates']
                        if (haversine_m(lon, lat, a_lon, a_lat) <= radius_m
                                and _names_match(name, anchor['properties'].get('name'))):
                            joined = ci
                            break
                    if joined is not None:
                        break
                if joined is not None:
                    break

        if joined is not None:
            clusters[joined].append(feat)
        else:
            ci = len(clusters)
            clusters.append([feat])
            index.setdefault((bx, by), []).append(ci)

    merged = [_merge_cluster(c) for c in clusters]
    merged.sort(key=lambda f: f['properties']['id'])

    dupes = len(all_features) - len(merged)
    print(f'Aggregated {len(all_features)} named features from {len(collections)} source(s) '
          f'→ {len(merged)} unique ({dupes} duplicate(s) merged, '
          f'{dropped_unnamed} unnamed dropped)')

    return {'type': 'FeatureCollection', 'features': merged}
