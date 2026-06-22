"""Geoapify Places API provider (food + fitness).

Fetches every place inside a city's administrative boundary exactly once:

  1. Resolve the city to a Geoapify boundary via the Geocoding API
     (type=city) -> place_id.
  2. Page the Places API with filter=place:<place_id>, all mapped categories in
     a single query, walking offset by _LIMIT until a short page ends it.

This replaces the old recursive rect quad-tiling, which re-queried overlapping
tiles and paid for (then discarded) every 500-result parent page. Boundary +
offset paging fetches each place once: credits = places / 20 (the floor), and
the API clips to the city polygon server-side so no local filtering is needed.

Coverage notes (from Geoapify's live category taxonomy):
  - Food: rich - commercial.supermarket / convenience and the
    commercial.food_and_drink.* leaves map cleanly onto our canonical types.
  - Fitness: sparse - Geoapify only exposes sport.fitness (gym / fitness_centre)
    and sport.dojo (martial arts). It has NO yoga / pilates / dance / climbing
    categories, so Geoapify mainly adds gyms + dojos. sport.fitness.fitness_station
    is OUTDOOR equipment and is excluded.

The API key comes from config.get_geoapify_key(); if unset (or the city can't be
resolved to a boundary) the provider yields an empty collection with a warning so
the rest of the pipeline still runs.

Unnamed places are skipped: we can't dedup them against named OSM/Overture
records, so including them would risk the very duplicates we're trying to avoid.
"""

from __future__ import annotations

import sys
import urllib.parse
from typing import Any

from .. import config
from ..cities import CityDef
from ..http import get_json
from ..transform.geojson_io import make_feature

_PLACES_ENDPOINT = 'https://api.geoapify.com/v2/places'
_GEOCODE_ENDPOINT = 'https://api.geoapify.com/v1/geocode/search'
_LIMIT = 500            # Geoapify per-request (and per-page) maximum
_MAX_PAGES = 200        # pagination guard: 200 * 500 = 100k places; no city is close
_TIMEOUT = 60

# Geoapify dotted category -> canonical type. Verified against the live taxonomy.
_CATEGORY_TO_TYPE: dict[str, str] = {
    # --- food ---
    'commercial.supermarket':                      'supermarket',
    'commercial.convenience':                      'convenience',
    'commercial.food_and_drink.bakery':            'bakery',
    'commercial.food_and_drink.deli':              'deli',
    'commercial.food_and_drink.frozen_food':       'frozen_food',
    'commercial.food_and_drink.organic':           'organic',
    'commercial.food_and_drink.health_food':       'organic',
    'commercial.food_and_drink.seafood':           'fishmonger',
    'commercial.food_and_drink.fruit_and_vegetable': 'greengrocer',
    'commercial.food_and_drink.confectionery':     'confectionery',
    'commercial.food_and_drink.chocolate':         'chocolate',
    'commercial.food_and_drink.butcher':           'butcher',
    'commercial.food_and_drink.cheese_and_dairy':  'cheese',
    'commercial.food_and_drink.drinks':            'beverages',
    'commercial.food_and_drink.coffee_and_tea':    'coffee',
    # --- fitness ---
    'sport.fitness.gym':                           'gym',
    'sport.fitness.fitness_centre':                'gym',
    'sport.fitness':                               'gym',
    'sport.dojo':                                  'martial_arts',
}

# Categories that look mapped but must be dropped (more specific than their parent).
_EXCLUDE: frozenset[str] = frozenset({'sport.fitness.fitness_station'})

# Categories to request per dataset (only what we can map - keeps credits low).
_REQUEST_CATEGORIES: dict[str, list[str]] = {
    'food': [c for c in _CATEGORY_TO_TYPE if c.startswith('commercial.')],
    'fitness': ['sport.fitness', 'sport.dojo'],
}


def _classify(categories: list[str]) -> str | None:
    """Pick the canonical type from a feature's categories, most-specific first.

    Specificity = dot count. An excluded category seen before any mapped one
    (e.g. fitness_station before sport.fitness) means: skip this feature.
    """
    for cat in sorted(categories, key=lambda c: c.count('.'), reverse=True):
        if cat in _EXCLUDE:
            return None
        if cat in _CATEGORY_TO_TYPE:
            return _CATEGORY_TO_TYPE[cat]
    return None


_place_id_cache: dict[str, str | None] = {}


def _resolve_place_id(query: str, key: str) -> str | None:
    """Geocode a city name to its Geoapify boundary place_id (cached per run)."""
    if query in _place_id_cache:
        return _place_id_cache[query]
    params = {'text': query, 'type': 'city', 'format': 'json', 'limit': '1', 'apiKey': key}
    results = get_json(
        f'{_GEOCODE_ENDPOINT}?{urllib.parse.urlencode(params)}', timeout=_TIMEOUT
    ).get('results', [])
    place_id = results[0].get('place_id') if results else None
    _place_id_cache[query] = place_id
    return place_id


def _fetch_boundary(categories: str, place_id: str, key: str) -> dict[str, dict[str, Any]]:
    """Page the Places API over a city boundary; each place is returned once."""
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    for _ in range(_MAX_PAGES):
        params = {
            'categories': categories,
            'filter': f'place:{place_id}',
            'limit': str(_LIMIT),
            'offset': str(offset),
            'apiKey': key,
        }
        feats = get_json(
            f'{_PLACES_ENDPOINT}?{urllib.parse.urlencode(params)}', timeout=_TIMEOUT
        ).get('features', [])
        for f in feats:
            pid = f['properties'].get('place_id')
            if pid and pid not in out:
                out[pid] = f
        if len(feats) < _LIMIT:
            return out
        offset += _LIMIT
    print(f'  geoapify: WARNING hit pagination guard ({_MAX_PAGES} pages) - '
          'results may be truncated.', file=sys.stderr)
    return out


def fetch_geoapify(city: CityDef, dataset_id: str) -> dict[str, Any]:
    """Provider entry point: return a normalised FeatureCollection for city+dataset."""
    key = config.get_geoapify_key()
    if not key:
        print('  geoapify: GEOAPIFY_KEY not set - skipping provider.', file=sys.stderr)
        return {'type': 'FeatureCollection', 'features': []}

    categories = _REQUEST_CATEGORIES.get(dataset_id)
    if not categories:
        return {'type': 'FeatureCollection', 'features': []}

    query = city.geoapify_query or city.name
    place_id = _resolve_place_id(query, key)
    if not place_id:
        print(f'  geoapify: could not resolve boundary for {query!r} - '
              f'skipping {city.id}/{dataset_id}.', file=sys.stderr)
        return {'type': 'FeatureCollection', 'features': []}

    print(f'Querying Geoapify - {city.id}/{dataset_id} (place:{place_id[:12]}…) ...')
    raw = _fetch_boundary(','.join(categories), place_id, key)

    features: list[dict[str, Any]] = []
    for pid, f in raw.items():
        p = f['properties']
        if not p.get('name'):
            continue  # skip unnamed (see module docstring)
        canonical = _classify(p.get('categories', []))
        if canonical is None:
            continue
        lon, lat = p.get('lon'), p.get('lat')
        if lon is None or lat is None:
            continue
        features.append(make_feature(
            f'geoapify/{pid}', p.get('name'), canonical, lon, lat,
            {
                'housenumber': p.get('housenumber', ''),
                'street': p.get('street', ''),
                'postcode': p.get('postcode', ''),
                'city': p.get('city', ''),
            },
        ))

    print(f'  geoapify: {len(features)} features for {city.id}/{dataset_id}')
    return {'type': 'FeatureCollection', 'features': features}
