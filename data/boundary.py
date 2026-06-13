"""Boundary fetcher — ports scripts/fetch-boundary.mjs faithfully.

Fetches a city's administrative boundary from OSM, simplifies it with
Douglas-Peucker, validates the area, and writes a compact GeoJSON Feature
to public/data/boundary-<city>.geojson.

Primary source: polygons.openstreetmap.fr (fast pre-built polygons).
Fallback: Overpass API (assembles the relation from member ways).
NYC fallback: if the main city relation is unavailable, assembles from the
five borough relations.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .cities import CityDef

USER_AGENT = 'grocery-heatmap/0.1 (boundary fetch script)'

OVERPASS_ENDPOINTS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
]


# ---------------------------------------------------------------------------
# Primary source: polygons.openstreetmap.fr
# ---------------------------------------------------------------------------

def _from_polygons_service(relation_id: int) -> dict[str, Any]:
    url = f'https://polygons.openstreetmap.fr/get_geojson.py?id={relation_id}&params=0'
    print(f'Trying {url} ...')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        if resp.status != 200:
            raise urllib.error.HTTPError(url, resp.status, resp.reason, {}, None)
        geom: dict[str, Any] = json.loads(resp.read())

    # The service sometimes wraps the result in a GeometryCollection
    if geom.get('type') == 'GeometryCollection':
        geometries = geom.get('geometries') or []
        geom = geometries[0] if geometries else {}

    if geom.get('type') not in ('Polygon', 'MultiPolygon'):
        raise ValueError(f"unexpected geometry type: {geom.get('type')}")
    return geom


# ---------------------------------------------------------------------------
# Fallback: Overpass — download member ways and stitch into rings
# ---------------------------------------------------------------------------

def _key(point: list[float]) -> str:
    """Stable string key for a [lon, lat] point (7 decimal places)."""
    return f'{point[0]:.7f},{point[1]:.7f}'


def _stitch_rings(ways: list[list[list[float]]]) -> list[list[list[float]]]:
    """Stitch a flat list of way coordinate arrays into closed rings."""
    unused = [list(w) for w in ways]
    rings: list[list[list[float]]] = []
    while unused:
        ring = unused.pop(0)
        while _key(ring[0]) != _key(ring[-1]):
            end = _key(ring[-1])
            idx = next(
                (i for i, w in enumerate(unused) if _key(w[0]) == end or _key(w[-1]) == end),
                -1,
            )
            if idx == -1:
                raise ValueError('open ring: member ways do not close')
            nxt = unused.pop(idx)
            if _key(nxt[0]) != end:
                nxt.reverse()
            ring.extend(nxt[1:])
        rings.append(ring)
    return rings


def _point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = point[0], point[1]
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _assemble_polygons(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble a Polygon or MultiPolygon from a relation's member list."""
    def ways_for_role(role: str) -> list[list[list[float]]]:
        result = []
        for m in members:
            if m.get('type') == 'way' and m.get('role') == role and m.get('geometry'):
                result.append([[p['lon'], p['lat']] for p in m['geometry']])
        return result

    outers = _stitch_rings(ways_for_role('outer'))
    inners = _stitch_rings(ways_for_role('inner'))

    polygons: list[list[list[list[float]]]] = [[outer] for outer in outers]
    for inner in inners:
        # Assign inner ring to the first outer that contains it
        host = next((p for p in polygons if _point_in_ring(inner[0], p[0])), None)
        if host is not None:
            host.append(inner)

    if len(polygons) == 1:
        return {'type': 'Polygon', 'coordinates': polygons[0]}
    return {'type': 'MultiPolygon', 'coordinates': polygons}


def _from_overpass(relation_id: int) -> dict[str, Any]:
    query = f'[out:json][timeout:300];rel({relation_id});out geom;'
    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            print(f'Querying {endpoint} for relation {relation_id} ...')
            body = urllib.parse.urlencode({'data': query}).encode()
            req = urllib.request.Request(
                endpoint,
                data=body,
                method='POST',
                headers={'User-Agent': USER_AGENT},
            )
            with urllib.request.urlopen(req) as resp:
                if resp.status != 200:
                    raise urllib.error.HTTPError(
                        endpoint, resp.status, resp.reason, {}, None
                    )
                result = json.loads(resp.read())

            rel = next(
                (el for el in (result.get('elements') or []) if el.get('type') == 'relation'),
                None,
            )
            if rel is None:
                raise ValueError('relation not found in response')
            return _assemble_polygons(rel.get('members') or [])
        except Exception as exc:
            print(f'  failed: {exc}', file=sys.stderr)
            last_error = exc

    raise RuntimeError(f'All Overpass endpoints failed. Last error: {last_error}')


def _fetch_relation(relation_id: int) -> dict[str, Any]:
    """Try polygons.openstreetmap.fr first, fall back to Overpass."""
    try:
        return _from_polygons_service(relation_id)
    except Exception as err:
        print(
            f'polygons.openstreetmap.fr failed ({err}); falling back to Overpass',
            file=sys.stderr,
        )
        return _from_overpass(relation_id)


# ---------------------------------------------------------------------------
# Douglas-Peucker simplification
# ---------------------------------------------------------------------------

def _perp_dist(
    point: list[float], p1: list[float], p2: list[float]
) -> float:
    """Perpendicular distance from point to the line segment p1→p2."""
    x, y = point[0], point[1]
    x1, y1 = p1[0], p1[1]
    x2, y2 = p2[0], p2[1]
    dx = x2 - x1
    dy = y2 - y1
    len2 = dx * dx + dy * dy
    if len2 == 0.0:
        return math.hypot(x - x1, y - y1)
    t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / len2))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def _douglas_peucker(
    points: list[list[float]], tol: float
) -> list[list[float]]:
    if len(points) <= 2:
        return points
    max_d = 0.0
    max_i = 0
    for i in range(1, len(points) - 1):
        d = _perp_dist(points[i], points[0], points[-1])
        if d > max_d:
            max_d = d
            max_i = i
    if max_d <= tol:
        return [points[0], points[-1]]
    left = _douglas_peucker(points[: max_i + 1], tol)
    right = _douglas_peucker(points[max_i:], tol)
    return left[:-1] + right


def _simplify_ring(ring: list[list[float]], tol: float) -> list[list[float]]:
    simplified = _douglas_peucker(ring, tol)
    return simplified if len(simplified) >= 4 else ring


def _map_rings(geom: dict[str, Any], fn: Any) -> dict[str, Any]:
    if geom['type'] == 'Polygon':
        return {'type': 'Polygon', 'coordinates': [fn(r) for r in geom['coordinates']]}
    return {
        'type': 'MultiPolygon',
        'coordinates': [[fn(r) for r in poly] for poly in geom['coordinates']],
    }


def _count_points(geom: dict[str, Any]) -> int:
    count = 0
    if geom['type'] == 'Polygon':
        for ring in geom['coordinates']:
            count += len(ring)
    else:
        for poly in geom['coordinates']:
            for ring in poly:
                count += len(ring)
    return count


# ---------------------------------------------------------------------------
# Area check: equirectangular shoelace, signed sum over rings
# ---------------------------------------------------------------------------

def _ring_area_km2(ring: list[list[float]]) -> float:
    lat_ref = math.radians(ring[0][1])
    m_lat = 111_320.0
    m_lng = 111_320.0 * math.cos(lat_ref)
    total = 0.0
    j = len(ring) - 1
    for i in range(len(ring)):
        total += (
            ring[j][0] * m_lng * (ring[i][1] * m_lat)
            - ring[i][0] * m_lng * (ring[j][1] * m_lat)
        )
        j = i
    return abs(total / 2) / 1e6


def _area_km2(geom: dict[str, Any]) -> float:
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    total = 0.0
    for poly in polys:
        outer = poly[0]
        holes = poly[1:]
        total += _ring_area_km2(outer)
        for hole in holes:
            total -= _ring_area_km2(hole)
    return total


def _polygons_of(geom: dict[str, Any]) -> list[Any]:
    """Flatten geometry into a list of polygon coordinate arrays."""
    if geom['type'] == 'Polygon':
        return [geom['coordinates']]
    return geom['coordinates']


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_boundary(city: CityDef) -> dict[str, Any]:
    """Fetch, simplify, and validate the boundary for the given city.

    Returns a compact GeoJSON Feature ready for writing.
    Raises SystemExit(1) on area-sanity failure.
    """
    raw: dict[str, Any] | None = None
    try:
        raw = _fetch_relation(city.relation)
    except Exception as err:
        if not city.fallback_relations:
            raise
        print(
            f'City relation {city.relation} failed ({err}); assembling from parts',
            file=sys.stderr,
        )
        parts: list[Any] = []
        for rel_id in city.fallback_relations:
            parts.extend(_polygons_of(_fetch_relation(rel_id)))
        raw = {'type': 'MultiPolygon', 'coordinates': parts}

    def simplify_and_round(ring: list[list[float]]) -> list[list[float]]:
        simplified = _simplify_ring(ring, city.tolerance_deg)
        return [[round(p[0], 6), round(p[1], 6)] for p in simplified]

    before_points = _count_points(raw)
    simplified = _map_rings(raw, simplify_and_round)
    after_points = _count_points(simplified)
    area = _area_km2(simplified)

    print(
        f'Boundary: {simplified["type"]}, '
        f'{before_points} → {after_points} points, '
        f'{area:.1f} km²'
    )

    min_area, max_area = city.area_range
    if area < min_area or area > max_area:
        print(
            f'area {area:.1f} km² is outside the plausible range '
            f'{min_area}-{max_area} km² for {city.name}; aborting',
            file=sys.stderr,
        )
        sys.exit(1)

    # No `generated` timestamp: boundaries change rarely, and omitting it keeps
    # re-runs from churning the committed file.
    return {
        'type': 'Feature',
        'properties': {'name': city.name, 'osmRelation': city.relation},
        'geometry': simplified,
    }
