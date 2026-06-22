"""Clip a FeatureCollection to a city's boundary polygon.

Only the OSM/Overpass provider restricts its query to the city's admin polygon
server-side (via `area.city`). Geoapify clips to its own geocoded "city"
boundary (which for Paris/NYC is larger than the OSM admin polygon) and Overture
clips only to a rectangular bbox. Both therefore leak places that sit outside the
clip zone the front end draws.

This module applies one final, source-agnostic point-in-polygon filter against
the committed boundary GeoJSON - the same simplified polygon the app renders -
so no provider can place a feature outside the zone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..geo import point_in_ring


def point_in_geometry(lon: float, lat: float, geom: dict[str, Any]) -> bool:
    """True if [lon, lat] lies inside a Polygon/MultiPolygon (outer ring, not in a hole)."""
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    for poly in polys:
        outer = poly[0]
        holes = poly[1:]
        if point_in_ring(lon, lat, outer) and not any(point_in_ring(lon, lat, h) for h in holes):
            return True
    return False


def load_boundary_geometry(boundary_dir: Path, city_id: str) -> dict[str, Any] | None:
    """Load the committed boundary polygon geometry for a city, or None if absent."""
    path = boundary_dir / city_id / 'boundary.geojson'
    if not path.exists():
        return None
    feature = json.loads(path.read_text())
    geom = feature.get('geometry') if feature.get('type') == 'Feature' else feature
    if not geom or geom.get('type') not in ('Polygon', 'MultiPolygon'):
        return None
    return geom


def clip_to_geometry(geojson: dict[str, Any], geom: dict[str, Any]) -> dict[str, Any]:
    """Return a FeatureCollection containing only features whose point is inside geom."""
    kept = []
    for f in geojson['features']:
        lon, lat = f['geometry']['coordinates']
        if point_in_geometry(lon, lat, geom):
            kept.append(f)
    return {'type': 'FeatureCollection', 'features': kept}
