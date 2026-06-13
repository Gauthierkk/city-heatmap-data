"""City registry — mirrors src/cities.ts where relevant.

Each entry contains only the fields needed by the data-fetch pipeline.
Keep in sync with src/cities.ts whenever city ids, wikidata ids, or
OSM relation ids change.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CityDef:
    id: str
    name: str
    wikidata: str          # used as Overpass area selector
    relation: int          # primary OSM admin relation id
    timeout: int           # Overpass timeout in seconds (stores queries)
    # Bounding box (minLon, minLat, maxLon, maxLat); mirrors src/cities.ts bbox.
    # Used by bbox-based providers (Overture, Geoapify).
    bbox: tuple[float, float, float, float]
    # Per-city tolerance for Douglas-Peucker boundary simplification (degrees)
    tolerance_deg: float
    # Plausible area range for the simplified boundary polygon (km²)
    area_range: tuple[float, float]
    # Optional list of per-borough relation ids to assemble when the primary
    # city relation is unavailable (currently only NYC)
    fallback_relations: tuple[int, ...] = field(default_factory=tuple)


CITIES: dict[str, CityDef] = {
    'paris': CityDef(
        id='paris',
        name='Paris',
        wikidata='Q90',
        relation=71525,
        timeout=180,
        bbox=(2.224, 48.815, 2.470, 48.902),
        # ~15 m tolerance: invisible at the overlay's 50 m cell resolution
        tolerance_deg=0.00015,
        # ~105 km² (intra-muros incl. both bois)
        area_range=(90.0, 120.0),
    ),
    'nyc': CityDef(
        id='nyc',
        name='New York City',
        wikidata='Q60',
        relation=175905,
        timeout=300,
        bbox=(-74.259, 40.477, -73.700, 40.917),
        # coarser tolerance: NYC overlay cells are ≥100 m
        tolerance_deg=0.0004,
        # ~784 km² of land; the OSM admin polygon extends into harbour/bay water,
        # so accept up to ~1,300 km²
        area_range=(700.0, 1300.0),
        # If the big city relation is unavailable, assemble the five boroughs
        fallback_relations=(2552485, 369518, 369519, 2552450, 962876),
    ),
    'austin': CityDef(
        id='austin',
        name='Austin',
        wikidata='Q16559',
        relation=113314,
        timeout=240,
        bbox=(-97.937, 30.099, -97.561, 30.517),
        # ~33 m tolerance: invisible at the overlay's 100 m cell resolution
        tolerance_deg=0.0003,
        # ~704 km² land (2020 census); the OSM polygon has ~56 inner holes
        # (unincorporated enclaves) which area_km2() subtracts
        area_range=(600.0, 1000.0),
    ),
}


def city_by_id(city_id: str) -> CityDef:
    if city_id not in CITIES:
        available = ', '.join(CITIES.keys())
        raise ValueError(f'Unknown city "{city_id}". Available: {available}')
    return CITIES[city_id]
