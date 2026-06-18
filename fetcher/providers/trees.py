"""Paris street-tree fetch — opendata.paris.fr `les-arbres` dataset.

A SEPARATE pipeline from the places (food/fitness) one. Trees are not businesses:
there is nothing to merge on, two distinct trees a few metres apart are NOT
duplicates, and OpenData Paris is the single authoritative source (~218k trees).
So this provider does not feed the source-agnostic aggregator, is not registered
in `ALL_PROVIDERS`, and is driven by its own `fetch-trees` CLI command.

The output is a GeoJSON **FeatureCollection** of Point features. A heatmap layer
reads a point FeatureCollection directly, and — unlike a bare MultiPoint — each
point carries its own properties, so every coordinate is bound to its species
("type"):

    {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "geometry": {"type": "Point", "coordinates": [lon, lat]},
          "properties": {"species_fr": "Marronnier", "species_en": "Horse chestnut"}
        },
        ...
      ]
    }

`species_fr` is the dataset's `libellefrancais` (French common name, e.g.
Marronnier, Platane, Tilleul); `species_en` is its English common name via
`tree_species_en.english_name()`. A tree with no recorded species gets empty
strings. Coordinates are kept to 5 dp (≈1.1 m) — a density layer needs no
sub-metre precision and it trims the ~200k-feature file.

PARIS-ONLY: returns an empty FeatureCollection for every other city (no equivalent
source wired up yet), mirroring how the SIRENE provider gates on Paris.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from typing import Any

from ..cities import CityDef
from .tree_species_en import english_name

USER_AGENT = 'city-heatmap-data/0.1 (trees fetch worker)'

# Opendatasoft Explore v2.1 bulk export — returns the whole dataset in one JSON
# array (the paginated /records endpoint caps at offset 10000, unusable for 218k
# rows). `select` trims the payload to the coordinate column plus the species name.
_EXPORT_URL = (
    'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/'
    'les-arbres/exports/json'
)
_SELECT = 'geo_point_2d,libellefrancais'

# Generous timeout: the full export is tens of MB.
_TIMEOUT_S = 300

# Coordinate precision — 5 dp ≈ 1.1 m. The package elsewhere keeps 6 dp, but a
# tree density layer needs no sub-metre precision, and 5 dp trims ~10% off a
# 200k-point file.
_COORD_DP = 5


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
    print(f'Querying OpenData Paris (les-arbres) — {city.id} ...')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        rows = json.loads(resp.read())

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
