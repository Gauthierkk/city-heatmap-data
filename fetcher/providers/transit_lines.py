"""Paris transit-line geometry - IDFM `traces-du-reseau-ferre-idf`.

The route geometry (LineStrings) of the rail network - one segment per row, each
carrying its mode, line designation (`indice_lig`) and the official line colour
(`colourweb_hexa`). The front end draws these as coloured lines beneath the
transit station dots, so the colour comes straight from the source (no colour map).

A SEPARATE Paris-only pipeline (`fetch-transit-lines`), like trees/transit. We
keep **metro + RER + tram** (mainline TER/TRAIN, navettes and cable excluded) and
only the segments that enter the Paris bbox - segments extending past the city are
clipped by the map's `maxBounds` at view time, so no geometric polygon clip is
needed (and point-in-polygon doesn't apply to LineStrings anyway).

Output is a FeatureCollection of LineString/MultiLineString features with
`properties = {mode, line, color}`.
"""

from __future__ import annotations

import sys
import urllib.parse
from typing import Any

from ..cities import CityDef

from ..http import get_json

# Opendatasoft Explore v2.1 GeoJSON export (geometry comes back as the feature
# geometry; `select` trims the properties).
_EXPORT_URL = (
    'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/'
    'traces-du-reseau-ferre-idf/exports/geojson'
)
_SELECT = 'geo_shape,mode,indice_lig,colourweb_hexa'

_TIMEOUT_S = 120

# Line vertices kept to 5 dp (~1 m) - plenty for a schematic overlay and it trims
# the many-vertex LineStrings.
_LINE_DP = 5

# Urban-rail modes we draw → canonical category (matches transit.py).
_MODE_TO_CATEGORY: dict[str, str] = {
    'METRO': 'metro',
    'RER': 'rer',
    'TRAMWAY': 'tram',
    'TRAM': 'tram',
}

_FALLBACK_COLOR = '#888888'


def _in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _iter_coords(geom: dict[str, Any]):
    """Yield [lon, lat] vertices of a LineString or MultiLineString."""
    if geom.get('type') == 'LineString':
        yield from geom.get('coordinates') or []
    elif geom.get('type') == 'MultiLineString':
        for line in geom.get('coordinates') or []:
            yield from line


def _touches_bbox(geom: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    return any(_in_bbox(c[0], c[1], bbox) for c in _iter_coords(geom))


def _round_coords(coords: Any) -> Any:
    """Round a (possibly nested) coordinate array to _LINE_DP."""
    if coords and isinstance(coords[0], (int, float)):
        return [round(coords[0], _LINE_DP), round(coords[1], _LINE_DP)]
    return [_round_coords(c) for c in coords]


def _color(hexa: str | None) -> str:
    h = (hexa or '').strip().lstrip('#')
    return f'#{h.lower()}' if h else _FALLBACK_COLOR


def fetch_transit_lines(city: CityDef) -> dict[str, Any]:
    """Return a FeatureCollection of Paris rail-line geometry (metro/rer/tram).

    Empty for any city other than Paris. Each feature keeps its LineString/
    MultiLineString geometry and carries `properties = {mode, line, color}`.
    """
    if city.id != 'paris':
        print(f'  transit-lines: no dataset for {city.id} (Paris-only)', file=sys.stderr)
        return {'type': 'FeatureCollection', 'features': []}

    url = f'{_EXPORT_URL}?{urllib.parse.urlencode({"select": _SELECT})}'
    print(f'Querying IDF Mobilités (traces-du-reseau-ferre-idf) - {city.id} ...')
    raw = get_json(url, timeout=_TIMEOUT_S)
    src = raw.get('features') or []
    print(f'  Retrieved {len(src)} line segments for {city.id}', file=sys.stderr)

    features: list[dict[str, Any]] = []
    for f in src:
        props = f.get('properties') or {}
        mode_cat = _MODE_TO_CATEGORY.get(props.get('mode') or '')
        if mode_cat is None:
            continue  # keep metro / rer / tram only
        geom = f.get('geometry') or {}
        if geom.get('type') not in ('LineString', 'MultiLineString'):
            continue
        if not _touches_bbox(geom, city.bbox):
            continue
        features.append({
            'type': 'Feature',
            'geometry': {'type': geom['type'], 'coordinates': _round_coords(geom['coordinates'])},
            'properties': {
                'mode': mode_cat,
                'line': (props.get('indice_lig') or '').strip(),
                'color': _color(props.get('colourweb_hexa')),
            },
        })

    return {'type': 'FeatureCollection', 'features': features}
