"""Overture Maps Places fetch for fitness dataset.

Queries s3://overturemaps-us-west-2/release/<RELEASE>/theme=places/type=place/*
via DuckDB (anonymous S3, hive-partitioned Parquet).

duckdb is NOT a stdlib dependency — it is imported lazily so OSM-only commands
keep working without it.  Install once with:

    pip3 install duckdb --user --break-system-packages   # python3 3.11+
    # or for python3.10: pip3.10 install duckdb --user

See data/README.md for full dependency notes.
"""

from __future__ import annotations

import sys
from typing import Any

from .cities import CityDef

# ---------------------------------------------------------------------------
# Release pin — bump deliberately when a newer Overture release is available.
# Current: 2026-05-20.0
# ---------------------------------------------------------------------------
OVERTURE_RELEASE = "2026-05-20.0"

_S3_PATH = (
    f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}"
    "/theme=places/type=place/*"
)

# Confidence threshold: places below this score are excluded.
OVERTURE_CONFIDENCE_MIN = 0.7

# ---------------------------------------------------------------------------
# Category mapping — validated by prototype query against Austin, Paris, NYC.
#
# Excluded (validated junk — city-agnostic rules):
#   fitness_trainer           — personal trainers, not venues
#   adventure_sports_center   — CAD firms / outdoor-equipment retailers (Austin/NYC)
#   health_coach              — hospital/medical systems (Austin/NYC)
#   sports_and_fitness_instruction — swim schools, golf instruction, tennis (all cities)
# ---------------------------------------------------------------------------
_CATEGORY_TO_TYPE: dict[str, str] = {
    "gym":                     "gym",
    "gymnastics_center":       "gym",
    "health_and_wellness_club": "gym",
    "yoga_studio":             "yoga",
    "pilates_studio":          "pilates",
    "martial_arts_club":       "martial_arts",
    "boxing_class":            "martial_arts",
    "boxing_club":             "martial_arts",
    "boxing_gym":              "martial_arts",
    "kickboxing_club":         "martial_arts",
    "taekwondo_club":          "martial_arts",
    "karate_club":             "martial_arts",
    "dance_school":            "dance",
    "rock_climbing_gym":       "climbing",
    "rock_climbing_spot":      "climbing",
}

_CAT_LIST_SQL = ", ".join(f"'{c}'" for c in _CATEGORY_TO_TYPE)

_QUERY_TMPL = """\
SELECT
    id,
    names.primary                          AS name,
    categories.primary                     AS overture_category,
    confidence,
    ST_X(geometry)                         AS lon,
    ST_Y(geometry)                         AS lat
FROM read_parquet('{s3_path}', hive_partitioning=1)
WHERE bbox.xmin >= {min_lon}
  AND bbox.xmax <= {max_lon}
  AND bbox.ymin >= {min_lat}
  AND bbox.ymax <= {max_lat}
  AND categories.primary IN ({cat_list})
  AND confidence >= {confidence}
  AND names.primary IS NOT NULL
  AND names.primary != ''
ORDER BY confidence DESC, names.primary
"""


def _get_city_bbox(city: CityDef) -> tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat) from the city's bbox.

    The bbox is read from data/cities.py where it mirrors src/cities.ts.
    Cities.py doesn't store bbox directly — we derive it from the cities.ts
    values hard-coded here to avoid a TS parse dependency.
    """
    # Mirrors src/cities.ts CITIES[id].bbox exactly.
    _BBOX: dict[str, tuple[float, float, float, float]] = {
        # (minLng, minLat, maxLng, maxLat)
        'paris':  (2.224, 48.815, 2.470, 48.902),
        'nyc':    (-74.259, 40.477, -73.700, 40.917),
        'austin': (-97.937, 30.099, -97.561, 30.517),
    }
    if city.id not in _BBOX:
        raise ValueError(
            f'No bbox configured for city "{city.id}" in overture.py._BBOX. '
            'Add it to match src/cities.ts.'
        )
    return _BBOX[city.id]


def fetch_overture_fitness(city: CityDef) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of Overture fitness places for city.

    Each feature has properties: {id, name, shop, source}.
    id is 'overture/<gers-id>', coordinates are 6-decimal.

    Raises ImportError (with install instructions) if duckdb is missing.
    """
    try:
        import duckdb  # noqa: F401 — lazy import
    except ImportError:
        raise ImportError(
            "duckdb is required for Overture-backed datasets but is not installed.\n"
            "Install with:\n"
            "  pip3 install duckdb --user --break-system-packages\n"
            "Or run with --no-overture to use OSM data only."
        ) from None

    import duckdb

    min_lon, min_lat, max_lon, max_lat = _get_city_bbox(city)
    query = _QUERY_TMPL.format(
        s3_path=_S3_PATH,
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        cat_list=_CAT_LIST_SQL,
        confidence=OVERTURE_CONFIDENCE_MIN,
    )

    print(
        f'Querying Overture {OVERTURE_RELEASE} — {city.id} fitness '
        f'(conf≥{OVERTURE_CONFIDENCE_MIN}) ...'
    )

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET s3_access_key_id=''; SET s3_secret_access_key='';")

    rows = con.execute(query).fetchall()
    print(f'  Retrieved {len(rows)} Overture candidates for {city.id}', file=sys.stderr)

    features: list[dict[str, Any]] = []
    for gers_id, name, cat, _confidence, lon, lat in rows:
        canonical = _CATEGORY_TO_TYPE.get(cat)
        if canonical is None:
            continue  # shouldn't happen given cat_list filter, but be safe
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [round(float(lon), 6), round(float(lat), 6)],
            },
            'properties': {
                'id': f'overture/{gers_id}',
                'name': name,
                'shop': canonical,
                'source': 'overture',
            },
        })

    return {'type': 'FeatureCollection', 'features': features}
