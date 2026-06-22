"""Shared geometry primitives (stdlib only).

`haversine_m` and `point_in_ring` were each implemented twice with subtly
different argument orders; centralising them removes that footgun. All callers
use the GeoJSON-native **(lon, lat)** ordering.
"""

from __future__ import annotations

import math

# Coordinate output precision. 6 dp ≈ 0.11 m - finer than any layer needs, and
# the shared default every writer rounds to (the trees layer overrides to 4 dp).
COORD_DP = 6

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two (lon, lat) points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test for a single [lon, lat] ring."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside
