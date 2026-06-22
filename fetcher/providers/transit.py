"""Paris public-transit fetch - Île-de-France rail stations (`emplacement-des-gares-idf`).

A SEPARATE pipeline (like trees), with its own minimal output format. The source
is the IDF Mobilités "gares et stations du réseau ferré" dataset on the same
Opendatasoft Explore v2.1 API as the Paris trees layer. It lists one row per
(station × line), ~1240 rows region-wide; we collapse those to **one point per
physical station** (grouped by `id_ref_zdc`, the zone-de-correspondance id that
unifies a multimodal hub's platforms) at the station's mean coordinate.

Each station carries a **list** of categories (no address): a metro+RER hub is
`["metro","rer"]`. The six Paris mainline terminals (Nord, Est, Lyon, Austerlitz,
Montparnasse, Saint-Lazare - Bercy excluded) get an extra `"major_station"`
category, so they form their own band on top of their modes.

Each station also carries `lines` - the actual lines it serves, each with its
canonical mode, its designation (`indice_lig`: `1`, `A`, `T3a`) and the official
IDFM pictogram filename (e.g. `METRO_1.svg`) the front end renders as the
authentic coloured bullet. At a **major station** the `lines` list keeps only
metro + RER lines (the mainline `TRAIN`/Transilien lines are dropped) - the
`categories` list above is left untouched.

PARIS-ONLY semantics like the other Paris-specific providers: it returns every
IDF station and the `fetch-transit` command clips to the Paris boundary, so only
intra-muros stations (~297) survive. Output is a FeatureCollection of points with
`properties = {id, name, categories, lines}`.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from typing import Any

from ..cities import CityDef
from ..geo import COORD_DP, haversine_m
from ..http import get_json

# Opendatasoft Explore v2.1 bulk export (same API family as the trees source).
_EXPORT_URL = (
    'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/'
    'emplacement-des-gares-idf/exports/json'
)
# `indice_lig` is the line designation (1, A, T3a); `picto` the official line
# pictogram (an object carrying the SVG filename the front end renders).
_SELECT = 'geo_point_2d,id_ref_zdc,nom_zdc,id_gares,mode,indice_lig,picto'

_TIMEOUT_S = 120

# A hub's parts can sit under different zone-de-correspondance ids ~100-500 m
# apart (e.g. Gare du Nord's metro/train zone vs its RER zone). We unify rows that
# share a station name AND fall within this radius, which collapses those parts
# into one point without merging genuinely distinct same-name stations region-wide
# (e.g. the two "Malesherbes" are 66 km apart).
_MERGE_RADIUS_M = 800.0

# Placeholder / empty names that must never be grouped by name (they recur across
# unrelated stations); these fall back to the station id key instead.
_PLACEHOLDER_NAMES: frozenset[str] = frozenset({'', 'nc'})

# Raw `mode` value → canonical category name.
_MODE_TO_CATEGORY: dict[str, str] = {
    'METRO': 'metro',
    'RER': 'rer',
    'TRAIN': 'train',
    'TRAMWAY': 'tram',
    'TRAM': 'tram',
    'VAL': 'val',
    'CABLE': 'cable',
}

# The six Paris mainline terminals get their own category (Bercy excluded - a
# small secondary terminus). Matched case-insensitively against `nom_zdc`.
_MAJOR_CATEGORY = 'major_station'
_MAJOR_STATIONS: frozenset[str] = frozenset({
    'gare du nord',
    "gare de l'est",
    'gare de lyon',
    "gare d'austerlitz",
    'gare montparnasse',
    'gare saint-lazare',
})


def _is_major(name: str | None) -> bool:
    """True for the six Paris mainline terminals (matched against `nom_zdc`)."""
    return bool(name and name.strip().lower() in _MAJOR_STATIONS)


def _categories(name: str | None, modes: set[str]) -> list[str]:
    """Category list for a station: its modes, with `major_station` prepended for
    the Paris mainline terminals."""
    cats = sorted({_MODE_TO_CATEGORY.get(m, (m or '').lower()) for m in modes if m})
    if _is_major(name):
        return [_MAJOR_CATEGORY, *cats]
    return cats


# Display order of a station's lines: by mode band, then by designation (metro
# numerically - 1, 2, … 14, then `3bis`/`7bis` - others alphabetically).
_LINE_MODE_ORDER: dict[str, int] = {
    'metro': 0, 'rer': 1, 'tram': 2, 'train': 3, 'val': 4, 'cable': 5,
}


def _picto_filename(picto: Any) -> str:
    """Pull the SVG filename from the dataset's `picto` field (an object, or a
    bare URL/filename depending on the export); '' when absent."""
    if isinstance(picto, dict):
        return picto.get('filename') or ''
    if isinstance(picto, str):
        return picto.rsplit('/', 1)[-1]
    return ''


def _line_sort_key(line: dict[str, str]) -> tuple[int, int, str]:
    m = re.match(r'(\d+)(.*)', line['line'])
    num, suffix = (int(m.group(1)), m.group(2)) if m else (9999, line['line'])
    return (_LINE_MODE_ORDER.get(line['mode'], 9), num, suffix)


def _ordered_lines(lines: dict[tuple[str, str], str], is_major: bool) -> list[dict[str, str]]:
    """Build the ordered `lines` list. At a major station, keep only metro + RER
    lines (drop mainline Transilien/`train`); elsewhere keep every line."""
    out = [
        {'mode': mode, 'line': indice, 'picto': picto}
        for (mode, indice), picto in lines.items()
        if not (is_major and mode not in ('metro', 'rer'))
    ]
    out.sort(key=_line_sort_key)
    return out


def fetch_transit(city: CityDef) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of IDF rail stations (one point each).

    Empty for any city other than Paris (no transit source wired up elsewhere).
    Each feature: Point geometry at the station's mean coordinate, with
    `properties = {id: 'transit/<zdc>', name, categories: [...]}`. No address.
    """
    empty = {'type': 'FeatureCollection', 'features': []}

    if city.id != 'paris':
        print(f'  transit: no dataset for {city.id} (Paris-only)', file=sys.stderr)
        return empty

    url = f'{_EXPORT_URL}?{urllib.parse.urlencode({"select": _SELECT})}'
    print(f'Querying IDF Mobilités (emplacement-des-gares-idf) - {city.id} ...')
    rows = get_json(url, timeout=_TIMEOUT_S)

    print(f'  Retrieved {len(rows)} station-line records for {city.id}', file=sys.stderr)

    # Collapse station-line rows into one entry per physical station. Rows are
    # grouped by a token, then split by proximity within that token:
    #   - a meaningful name → token ('n', name): same-name rows merge only when
    #     within _MERGE_RADIUS_M, so a split hub unifies but distant namesakes don't.
    #   - an empty/placeholder name → token ('z', zdc id) / ('g', gares id): never
    #     merged by name, only by their own station id.
    stations: list[dict[str, Any]] = []
    buckets: dict[Any, list[int]] = {}  # token -> indices into `stations`
    for row in rows:
        point = row.get('geo_point_2d') or {}
        lon, lat = point.get('lon'), point.get('lat')
        if lon is None or lat is None:
            continue
        name = row.get('nom_zdc')
        norm = (name or '').strip().lower()
        if norm and norm not in _PLACEHOLDER_NAMES:
            token: Any = ('n', norm)
        elif row.get('id_ref_zdc') is not None:
            token = ('z', row.get('id_ref_zdc'))
        else:
            token = ('g', row.get('id_gares'))

        placed = None
        for i in buckets.setdefault(token, []):
            st = stations[i]
            cx, cy = st['sx'] / st['n'], st['sy'] / st['n']
            if haversine_m(lon, lat, cx, cy) <= _MERGE_RADIUS_M:
                placed = i
                break
        if placed is None:
            placed = len(stations)
            stations.append({'name': name, 'modes': set(), 'lines': {},
                             'sx': 0.0, 'sy': 0.0, 'n': 0, 'id': None})
            buckets[token].append(placed)

        st = stations[placed]
        st['modes'].add(row.get('mode'))
        # Record this row's line: keyed by (mode, designation) so a line counted
        # once per station even though it appears on many platform rows.
        mode_cat = _MODE_TO_CATEGORY.get(row.get('mode'), (row.get('mode') or '').lower())
        indice = (row.get('indice_lig') or '').strip()
        if mode_cat and indice:
            st['lines'][(mode_cat, indice)] = _picto_filename(row.get('picto'))
        st['sx'] += lon
        st['sy'] += lat
        st['n'] += 1
        if name and not st['name']:
            st['name'] = name
        # Stable id: the smallest zone/station id seen in the cluster.
        idv = row.get('id_ref_zdc') if row.get('id_ref_zdc') is not None else row.get('id_gares')
        if idv is not None and (st['id'] is None or idv < st['id']):
            st['id'] = idv

    features: list[dict[str, Any]] = []
    for st in stations:
        lon = round(st['sx'] / st['n'], COORD_DP)
        lat = round(st['sy'] / st['n'], COORD_DP)
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {
                'id': f'transit/{st["id"]}',
                'name': st['name'],
                'categories': _categories(st['name'], st['modes']),
                'lines': _ordered_lines(st['lines'], _is_major(st['name'])),
            },
        })

    return {'type': 'FeatureCollection', 'features': features}
