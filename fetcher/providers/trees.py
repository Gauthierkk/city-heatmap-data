"""Paris street-tree fetch - opendata.paris.fr `les-arbres` dataset.

A SEPARATE pipeline from the places (food/fitness) one. Trees are not businesses:
there is nothing to merge on, two distinct trees a few metres apart are NOT
duplicates, and OpenData Paris is the single authoritative source (~218k trees).
So this provider does not feed the source-agnostic aggregator, is not registered
in `ALL_PROVIDERS`, and is driven by its own `fetch-trees` CLI command.

`fetch_trees` builds a GeoJSON **FeatureCollection** of Point features (the shape
the boundary clip + guards operate on), each point carrying its species in French
and English. Before writing, `to_columnar` collapses that to the compact
**`trees-columnar-v1`** JSON object the front end actually ships - a species
lookup table + parallel coordinate/index arrays (see `to_columnar`), ~5–7×
smaller because the repeated species strings and per-feature GeoJSON boilerplate
are gone. `trees-columnar-v1` is the documented contract with the front-end repo
(see `fetcher/README.md`).

`species_fr` is the dataset's `libellefrancais` (French common name, e.g.
Marronnier, Platane, Tilleul); `species_en` is its English common name via
`tree_species_en.english_name()`. A tree with no recorded species gets empty
strings. Coordinates are kept to 4 dp (≈11 m) - a density layer needs no
sub-metre precision and it trims the ~200k-point file.

PARIS-ONLY: returns an empty FeatureCollection for every other city (no equivalent
source wired up yet), mirroring how the SIRENE provider gates on Paris.
"""

from __future__ import annotations

import sys
import urllib.parse
from typing import Any

from ..cities import CityDef
from ..http import get_json
from .tree_species_en import english_name

# Opendatasoft Explore v2.1 bulk export - returns the whole dataset in one JSON
# array (the paginated /records endpoint caps at offset 10000, unusable for 218k
# rows). `select` trims the payload to the coordinate column plus the species name.
_EXPORT_URL = (
    'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/'
    'les-arbres/exports/json'
)
_SELECT = 'geo_point_2d,libellefrancais'

# Generous timeout: the full export is tens of MB.
_TIMEOUT_S = 300

# Coordinate precision - 4 dp ≈ 11 m. The package elsewhere keeps 6 dp, but a
# tree density layer (default 25 m spread) needs nothing finer, and 4 dp trims
# ~30% off a 200k-point file.
_COORD_DP = 4


def fetch_trees(city: CityDef) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of every Paris tree (point + species).

    Each feature is a Point whose `properties` carry the species in French
    (`species_fr`, the dataset's `libellefrancais`) and English (`species_en`),
    so every coordinate is directly bound to its species name.

    Empty for any city other than Paris (no tree source wired up elsewhere).
    """
    if city.id != 'paris':
        print(f'  trees: no dataset for {city.id} (Paris-only)', file=sys.stderr)
        return {'type': 'FeatureCollection', 'features': []}

    url = f'{_EXPORT_URL}?{urllib.parse.urlencode({"select": _SELECT})}'
    print(f'Querying OpenData Paris (les-arbres) - {city.id} ...')
    rows = get_json(url, timeout=_TIMEOUT_S)

    print(f'  Retrieved {len(rows)} tree records for {city.id}', file=sys.stderr)

    # Translate each distinct French name once (235-ish), not per tree.
    en_cache: dict[str, str] = {}
    features: list[dict[str, Any]] = []
    for row in rows:
        point = row.get('geo_point_2d') or {}
        lon = point.get('lon')
        lat = point.get('lat')
        if lon is None or lat is None:
            continue  # a handful of rows lack coordinates
        fr = (row.get('libellefrancais') or '').strip()
        en = en_cache.get(fr)
        if en is None:
            en = en_cache[fr] = english_name(fr)
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [round(float(lon), _COORD_DP), round(float(lat), _COORD_DP)],
            },
            'properties': {'species_fr': fr, 'species_en': en},
        })

    print(f'  {len(en_cache)} distinct tree species for {city.id}', file=sys.stderr)

    return {'type': 'FeatureCollection', 'features': features}


def to_columnar(fc: dict[str, Any]) -> dict[str, Any]:
    """Convert a tree FeatureCollection to the compact `trees-columnar-v1` shape.

    The species strings repeat on every one of ~192k features (e.g. "Plane tree"
    ~39k times), so a FeatureCollection bloats to tens of MB and stalls the client
    on `JSON.parse` + GPU upload. This drops the per-feature GeoJSON boilerplate by
    going columnar and replaces the repeated strings with a deduplicated species
    lookup table + integer index:

        {
          "format": "trees-columnar-v1",
          "species": [{"fr": "Platane", "en": "Plane tree"}, ...],
          "coordinates": [[lon, lat], ...],
          "speciesIndex": [0, 1, ...]
        }

    `species` is sorted by frequency (index 0 = most common). Trees with no
    recorded species share one real entry `{"fr": "", "en": ""}` - there is no
    sentinel, every tree gets a valid index. `coordinates[i]` and
    `speciesIndex[i]` are parallel (one entry per tree). Indices are only stable
    within a single generated file; the front end reads them per file.
    """
    # Single pass over ~192k features: tally species frequency while collecting
    # each tree's coordinate and species key (empty strings form their own key,
    # so unnamed trees collapse to a single real table entry).
    counts: dict[tuple[str, str], int] = {}
    keys: list[tuple[str, str]] = []
    coordinates: list[list[float]] = []
    for f in fc['features']:
        props = f['properties']
        key = (props['species_fr'], props['species_en'])
        counts[key] = counts.get(key, 0) + 1
        keys.append(key)
        coordinates.append(f['geometry']['coordinates'])

    # Most frequent first; tie-break on the name pair for deterministic output.
    ordered = sorted(counts, key=lambda k: (-counts[k], k))
    index_of = {key: i for i, key in enumerate(ordered)}
    species = [{'fr': fr, 'en': en} for fr, en in ordered]
    species_index = [index_of[k] for k in keys]

    return {
        'format': 'trees-columnar-v1',
        'species': species,
        'coordinates': coordinates,
        'speciesIndex': species_index,
    }
