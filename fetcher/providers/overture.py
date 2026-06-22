"""Overture Maps Places fetch for fitness dataset.

Queries s3://overturemaps-us-west-2/release/<RELEASE>/theme=places/type=place/*
via DuckDB (anonymous S3, hive-partitioned Parquet).

duckdb is NOT a stdlib dependency - it is imported lazily (see duckdb_io) so
OSM-only commands keep working without it. Install with `uv sync`; see
fetcher/README.md for full dependency notes.
"""

from __future__ import annotations

import sys
from typing import Any

from ..cities import CityDef
from ..duckdb_io import connect, require_duckdb, sql_str_list
from ..transform.geojson_io import make_feature

# ---------------------------------------------------------------------------
# Release pin - bump deliberately when a newer Overture release is available.
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
# Category mapping - validated by prototype query against Austin, Paris, NYC.
#
# Excluded (validated junk - city-agnostic rules):
#   fitness_trainer           - personal trainers, not venues
#   adventure_sports_center   - CAD firms / outdoor-equipment retailers (Austin/NYC)
#   health_coach              - hospital/medical systems (Austin/NYC)
#   sports_and_fitness_instruction - swim schools, golf instruction, tennis (all cities)
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

_CAT_LIST_SQL = sql_str_list(_CATEGORY_TO_TYPE)

_QUERY_TMPL = """\
SELECT
    id,
    names.primary                          AS name,
    categories.primary                     AS overture_category,
    confidence,
    ST_X(geometry)                         AS lon,
    ST_Y(geometry)                         AS lat,
    addresses[1].freeform                  AS addr_street,
    addresses[1].locality                  AS addr_city,
    addresses[1].postcode                  AS addr_postcode
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


def fetch_overture(city: CityDef, dataset_id: str = 'fitness') -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of Overture places for city.

    Overture coverage here is fitness-only. Each feature carries a structured
    address {street, city, postcode} when Overture has one (no housenumber field).
    id is 'overture/<gers-id>', coordinates are 6-decimal.

    Raises ImportError (with install instructions) if duckdb is missing.
    """
    if dataset_id != 'fitness':
        return {'type': 'FeatureCollection', 'features': []}

    duckdb = require_duckdb('Overture')

    min_lon, min_lat, max_lon, max_lat = city.bbox
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
        f'Querying Overture {OVERTURE_RELEASE} - {city.id} fitness '
        f'(conf≥{OVERTURE_CONFIDENCE_MIN}) ...'
    )

    con = connect(duckdb, spatial=True, s3=True)
    rows = con.execute(query).fetchall()
    print(f'  Retrieved {len(rows)} Overture candidates for {city.id}', file=sys.stderr)

    features: list[dict[str, Any]] = []
    for gers_id, name, cat, _confidence, lon, lat, addr_street, addr_city, addr_postcode in rows:
        canonical = _CATEGORY_TO_TYPE.get(cat)
        if canonical is None:
            continue  # shouldn't happen given cat_list filter, but be safe
        features.append(make_feature(
            f'overture/{gers_id}', name, canonical, lon, lat,
            {'street': addr_street, 'city': addr_city, 'postcode': addr_postcode},
        ))

    return {'type': 'FeatureCollection', 'features': features}
