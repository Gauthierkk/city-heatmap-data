"""Geoapify Places API provider (food + fitness).

GET https://api.geoapify.com/v2/places?categories=...&filter=rect:...&limit=500&apiKey=...

Coverage notes (from Geoapify's live category taxonomy):
  - Food: rich — commercial.supermarket / convenience and the
    commercial.food_and_drink.* leaves map cleanly onto our canonical types.
  - Fitness: sparse — Geoapify only exposes sport.fitness (gym / fitness_centre)
    and sport.dojo (martial arts). It has NO yoga / pilates / dance / climbing
    categories, so Geoapify mainly adds gyms + dojos. sport.fitness.fitness_station
    is OUTDOOR equipment and is excluded.

Completeness without guessing page counts: recursive quad-tiling. We query a
rectangle; if it returns the 500-result cap, we split it into four and recurse;
otherwise we keep the page. Stays well inside the free plan (3000 credits/day,
1 credit / 20 places).

The API key comes from config.get_geoapify_key(); if unset the provider yields an
empty collection with a warning so the rest of the pipeline still runs.

Unnamed places are skipped: we can't dedup them against named OSM/Overture
records, so including them would risk the very duplicates we're trying to avoid.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import config
from ..cities import CityDef
from ..transform.geojson_io import make_feature

_ENDPOINT = 'https://api.geoapify.com/v2/places'
_LIMIT = 500            # Geoapify per-request maximum
_MAX_DEPTH = 6          # quad-tiling recursion guard (4^6 = 4096 leaf tiles max)
_TIMEOUT = 60
_USER_AGENT = 'city-heatmap-data/0.1 (weekly data refresh worker)'

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

# Categories to request per dataset (only what we can map — keeps credits low).
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


def _request(categories: str, rect: tuple[float, float, float, float], key: str) -> list[dict[str, Any]]:
    """One Places API call for a rectangle; returns the raw feature list."""
    params = {
        'categories': categories,
        'filter': f'rect:{rect[0]},{rect[1]},{rect[2]},{rect[3]}',
        'limit': str(_LIMIT),
        'apiKey': key,
    }
    url = f'{_ENDPOINT}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read()).get('features', [])
        except urllib.error.HTTPError as exc:
            # 4xx (bad category etc.) won't fix itself — fail fast.
            if 400 <= exc.code < 500:
                raise RuntimeError(f'Geoapify {exc.code}: {exc.read().decode()[:200]}') from None
            last_error = exc
        except Exception as exc:  # transient network/5xx — retry
            last_error = exc
    raise RuntimeError(f'Geoapify request failed after retries: {last_error}')


def _fetch_rect(
    categories: str,
    rect: tuple[float, float, float, float],
    key: str,
    depth: int,
    out: dict[str, dict[str, Any]],
) -> None:
    """Recursively fetch a rectangle, quad-splitting when it hits the cap."""
    feats = _request(categories, rect, key)
    if len(feats) >= _LIMIT and depth < _MAX_DEPTH:
        min_lon, min_lat, max_lon, max_lat = rect
        mid_lon = (min_lon + max_lon) / 2
        mid_lat = (min_lat + max_lat) / 2
        for sub in (
            (min_lon, min_lat, mid_lon, mid_lat),
            (mid_lon, min_lat, max_lon, mid_lat),
            (min_lon, mid_lat, mid_lon, max_lat),
            (mid_lon, mid_lat, max_lon, max_lat),
        ):
            _fetch_rect(categories, sub, key, depth + 1, out)
        return
    for f in feats:
        pid = f['properties'].get('place_id')
        if pid and pid not in out:
            out[pid] = f


def fetch_geoapify(city: CityDef, dataset_id: str) -> dict[str, Any]:
    """Provider entry point: return a normalised FeatureCollection for city+dataset."""
    key = config.get_geoapify_key()
    if not key:
        print('  geoapify: GEOAPIFY_KEY not set — skipping provider.', file=sys.stderr)
        return {'type': 'FeatureCollection', 'features': []}

    categories = _REQUEST_CATEGORIES.get(dataset_id)
    if not categories:
        return {'type': 'FeatureCollection', 'features': []}

    print(f'Querying Geoapify — {city.id}/{dataset_id} ...')
    raw: dict[str, dict[str, Any]] = {}
    _fetch_rect(','.join(categories), city.bbox, key, 0, raw)

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
