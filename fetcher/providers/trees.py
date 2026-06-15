"""Paris street-tree fetch — opendata.paris.fr `les-arbres` dataset.

A SEPARATE pipeline from the places (food/fitness) one, with its own minimal
output format. Trees are not businesses: there is nothing to merge on, two
distinct trees a few metres apart are NOT duplicates, and OpenData Paris is the
single authoritative source (~218k trees). So this provider does not feed the
source-agnostic aggregator, is not registered in `ALL_PROVIDERS`, and is driven
by its own `fetch-trees` CLI command.

A tree layer is pure point density, so it carries no per-feature properties (no
id / name / shop / address) — just coordinates. The output is therefore a single
GeoJSON **MultiPoint** geometry:

    {"type": "MultiPoint", "coordinates": [[lon, lat], [lon, lat], ...]}

— still valid GeoJSON (loads straight into a MapLibre source) but really just a
list of coords, which keeps a 200k-point file as small as possible.

PARIS-ONLY: returns an empty MultiPoint for every other city (no equivalent
source wired up yet), mirroring how the SIRENE provider gates on Paris.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from typing import Any

from ..cities import CityDef

USER_AGENT = 'city-heatmap-data/0.1 (trees fetch worker)'

# Opendatasoft Explore v2.1 bulk export — returns the whole dataset in one JSON
# array (the paginated /records endpoint caps at offset 10000, unusable for 218k
# rows). `select` trims the payload to just the coordinate column.
_EXPORT_URL = (
    'https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/'
    'les-arbres/exports/json'
)
_SELECT = 'geo_point_2d'

# Generous timeout: the full export is tens of MB.
_TIMEOUT_S = 300

# Coordinate precision — 5 dp ≈ 1.1 m. The package elsewhere keeps 6 dp, but a
# tree density layer needs no sub-metre precision, and 5 dp trims ~10% off a
# 200k-point file.
_COORD_DP = 5


def fetch_trees(city: CityDef) -> dict[str, Any]:
    """Return a GeoJSON MultiPoint of every Paris tree's [lon, lat].

    Empty (`coordinates: []`) for any city other than Paris (no tree source wired
    up elsewhere). No per-feature properties — a tree layer is pure density.
    """
    if city.id != 'paris':
        print(f'  trees: no dataset for {city.id} (Paris-only)', file=sys.stderr)
        return {'type': 'MultiPoint', 'coordinates': []}

    url = f'{_EXPORT_URL}?{urllib.parse.urlencode({"select": _SELECT})}'
    print(f'Querying OpenData Paris (les-arbres) — {city.id} ...')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        rows = json.loads(resp.read())

    print(f'  Retrieved {len(rows)} tree records for {city.id}', file=sys.stderr)

    coordinates: list[list[float]] = []
    for row in rows:
        point = row.get('geo_point_2d') or {}
        lon = point.get('lon')
        lat = point.get('lat')
        if lon is None or lat is None:
            continue  # a handful of rows lack coordinates
        coordinates.append([round(float(lon), _COORD_DP), round(float(lat), _COORD_DP)])

    return {'type': 'MultiPoint', 'coordinates': coordinates}
