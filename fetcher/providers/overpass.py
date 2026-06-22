"""Overpass API helpers: endpoints, query builders, normalisation functions.

Mirrors the query logic from the retired scripts/fetch-stores.mjs exactly.
"""

from __future__ import annotations

from typing import Any

from ..cities import CityDef
from ..http import post_overpass
from ..transform.geojson_io import make_feature

# ---------------------------------------------------------------------------
# Food dataset
# ---------------------------------------------------------------------------

# PRD §4.3 lists `shop=fishmonger` and `shop=organic`, but in OSM fishmongers
# are tagged `shop=seafood` and organic stores are `<any shop>` + `organic=only`.
# We query the real tags and normalise back to the PRD's category names below.
SHOP_TYPES = [
    'supermarket',
    'convenience',
    'greengrocer',
    'butcher',
    'seafood',
    'bakery',
    'pastry',
    'deli',
    'cheese',
    'frozen_food',
    'wine',
    'alcohol',
    'beverages',
    'chocolate',
    'confectionery',
    'tea',
    'coffee',
]


def build_food_query(city: CityDef) -> str:
    shop_pattern = '|'.join(SHOP_TYPES)
    return f"""
[out:json][timeout:{city.timeout}];
area["wikidata"="{city.wikidata}"]["boundary"="administrative"]->.city;
(
  nwr["shop"~"^({shop_pattern})$"](area.city);
  nwr["shop"]["organic"="only"](area.city);
);
out center tags;
"""


def normalise_food(tags: dict[str, str]) -> str | None:
    """Returns None when element should be skipped (no shop tag).

    Normalises OSM tag quirks:
      seafood → fishmonger
      organic=only → organic (regardless of shop value)
    """
    if not tags.get('shop'):
        return None
    if tags.get('organic') == 'only':
        return 'organic'
    if tags['shop'] == 'seafood':
        return 'fishmonger'
    return tags['shop']


# ---------------------------------------------------------------------------
# Fitness dataset
# ---------------------------------------------------------------------------

# Fitness features reuse the `shop` property key (shop: 'yoga') - deliberate
# quirk so StoreProperties, MapView's ['get','shop'] expressions, and
# distanceField.ts stay untouched.

def build_fitness_query(city: CityDef) -> str:
    return f"""
[out:json][timeout:{city.timeout}];
area["wikidata"="{city.wikidata}"]["boundary"="administrative"]->.city;
(
  nwr["leisure"="fitness_centre"](area.city);
  nwr["leisure"="dance"](area.city);
  nwr["leisure"]["sport"~"fitness|yoga|pilates|martial_arts|karate|judo|taekwondo|boxing|mma|climbing|dance"](area.city);
  nwr[!"leisure"]["sport"~"fitness|yoga|pilates|martial_arts|karate|judo|taekwondo|boxing|mma|climbing|dance"](area.city);
  nwr["amenity"~"^(dojo|dancing_school|gym)$"](area.city);
  nwr["club"="sport"](area.city);
  nwr["leisure"="sports_hall"](area.city);
);
out center tags;
"""


MARTIAL: frozenset[str] = frozenset(['martial_arts', 'karate', 'judo', 'taekwondo', 'boxing', 'mma'])

# Outdoor / non-business leisure values that should never yield a result
EXCLUDED_LEISURE: frozenset[str] = frozenset([
    'fitness_station', 'pitch', 'track', 'swimming_pool', 'water_park',
])


def _classify_by_sport(tags: dict[str, str]) -> str | None:
    """Classify using the canonical priority chain derived from the sport tag.

    Returns one of the six canonical types, or None if no match.
    """
    raw = tags.get('sport', '')
    sports = {s.strip().lower() for s in raw.split(';') if s.strip()}
    if 'yoga' in sports:
        return 'yoga'
    if 'pilates' in sports:
        return 'pilates'
    if sports & MARTIAL:
        return 'martial_arts'
    if 'climbing' in sports:
        return 'climbing'
    if 'dance' in sports:
        return 'dance'
    if 'fitness' in sports:
        return 'gym'
    return None  # rowing, pétanque, tennis, swimming, etc. - filter out


def normalise_fitness(tags: dict[str, str]) -> str | None:
    """Returns the canonical fitness type or None to skip this element."""
    if tags.get('shop'):
        return None  # strictly no retail

    leisure = tags.get('leisure')
    amenity = tags.get('amenity')

    # Explicit EXCLUDED_LEISURE values are always outdoor/non-business facilities
    if leisure and leisure in EXCLUDED_LEISURE:
        return None

    # amenity-keyed variants: dojo, dancing_school, gym
    if amenity == 'dojo':
        return 'martial_arts'
    if amenity == 'dancing_school':
        return 'dance'
    if amenity == 'gym':
        return 'gym'

    # Sport tag takes priority over leisure for all cases where both are present:
    # a fitness_centre tagged sport=yoga is a yoga studio, not a generic gym.
    by_sport = _classify_by_sport(tags)
    if by_sport is not None:
        return by_sport

    # No recognised sport tag - fall back to leisure semantics.
    if leisure == 'fitness_centre':
        return 'gym'
    if leisure == 'dance':
        return 'dance'

    # sports_centre, sports_hall, club=sport, bare sport=* with no matching
    # sport tag: nothing we can classify.
    return None


# ---------------------------------------------------------------------------
# Dataset registry
# ---------------------------------------------------------------------------

# Output files are named <city>/<dataset-id>.geojson (e.g. paris/food.geojson),
# so the dataset id below doubles as the filename stem - no separate prefix.
DATASETS: dict[str, dict[str, Any]] = {
    'food': {
        'min_features': 100,
        'build_query': build_food_query,
        'normalise': normalise_food,
    },
    'fitness': {
        'min_features': 50,
        'build_query': build_fitness_query,
        'normalise': normalise_fitness,
    },
}


def dataset_by_id(dataset_id: str) -> dict[str, Any]:
    if dataset_id not in DATASETS:
        available = ', '.join(DATASETS.keys())
        raise ValueError(f'Unknown dataset "{dataset_id}". Available: {available}')
    return DATASETS[dataset_id]


# ---------------------------------------------------------------------------
# Provider entry point
# ---------------------------------------------------------------------------

def _osm_address(tags: dict[str, str]) -> dict[str, str]:
    """Extract a structured address from OSM addr:* tags (only present keys)."""
    return {
        'housenumber': tags.get('addr:housenumber', ''),
        'street': tags.get('addr:street', ''),
        'postcode': tags.get('addr:postcode', ''),
        'city': tags.get('addr:city', ''),
    }


def fetch_osm(city: CityDef, dataset_id: str) -> dict[str, Any]:
    """Provider entry point: query Overpass and return a normalised FeatureCollection."""
    dataset = dataset_by_id(dataset_id)
    raw = post_overpass(dataset['build_query'](city))
    normalise = dataset['normalise']

    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in raw.get('elements') or []:
        lat = el.get('lat') or (el.get('center') or {}).get('lat')
        lon = el.get('lon') or (el.get('center') or {}).get('lon')
        if lat is None or lon is None:
            continue
        tags: dict[str, str] = el.get('tags') or {}
        canonical = normalise(tags)
        if canonical is None:
            continue
        feat_id = f"{el['type']}/{el['id']}"
        if feat_id in seen:
            continue
        seen.add(feat_id)
        features.append(make_feature(
            feat_id, tags.get('name'), canonical, lon, lat, _osm_address(tags),
        ))

    print(f'  osm: {len(features)} features for {city.id}/{dataset_id}')
    return {'type': 'FeatureCollection', 'features': features}
