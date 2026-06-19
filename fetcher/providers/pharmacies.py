"""Paris pharmacy fetch — "Carte des pharmacies de Paris" (Région Île-de-France
open data, dataset `carte-des-pharmacies-de-paris` on data.iledefrance.fr).

A SEPARATE pipeline (like trees / transit): a single authoritative source, one
point per pharmacy, no merge and no OSM backbone. The output is a normal
store-shaped `FeatureCollection` with `shop = 'pharmacy'`, so it flows through
the front end's places machinery (dots, the distance-to-nearest overlay, the
type filter and the closest-places list) with no app-side special-casing — the
same trick fitness/transit use by reusing the `shop` property key.

PARIS-ONLY semantics like the other Paris-specific providers: the source is the
Paris pharmacy register (≈987 establishments, all département 75, each with a
FINESS id, name and street address), and `fetch-pharmacies` clips to the Paris
boundary like transit/trees. Returns an empty FeatureCollection for every other
city (no equivalent register wired up elsewhere).

Each record carries a structured address (house number + street type/name +
postcode + commune) which we title-case for display parity with the other
providers (the register, like SIRENE, is ALL-CAPS).
"""

from __future__ import annotations

import sys
from typing import Any

from ..cities import CityDef
from ..http import get_json
from ..transform.geojson_io import make_feature, titlecase

# OpenDataSoft v1 download (whole dataset as a JSON array of records). The
# data.gouv.fr resource points here; the http→https 301 is followed by urllib.
_EXPORT_URL = (
    'https://data.iledefrance.fr/explore/dataset/'
    'carte-des-pharmacies-de-paris/download?format=json'
)
_TIMEOUT_S = 60


def _address(fields: dict[str, Any]) -> dict[str, Any]:
    """Build the structured address from the register's columns.

    Street is `typvoie` + `voie` joined (e.g. RUE + DE LA PAIX); all parts are
    title-cased. Numeric columns (house number, postcode) are stringified.
    """
    street = ' '.join(str(p) for p in (fields.get('typvoie'), fields.get('voie')) if p)
    numvoie = fields.get('numvoie')
    cp = fields.get('cp')
    return {
        'housenumber': str(numvoie) if numvoie is not None else '',
        'street': titlecase(street),
        'postcode': str(cp) if cp is not None else '',
        'city': titlecase(fields.get('commune')),
    }


def fetch_pharmacies(city: CityDef) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of Paris pharmacies (one point each).

    Empty for any city other than Paris (no pharmacy register wired up
    elsewhere). Each feature: Point geometry, `shop = 'pharmacy'`, a name, and a
    structured address. id is 'pharmacy/<FINESS établissement id>'.
    """
    if city.id != 'paris':
        print(f'  pharmacies: no dataset for {city.id} (Paris-only)', file=sys.stderr)
        return {'type': 'FeatureCollection', 'features': []}

    print(f'Querying Région Île-de-France (carte-des-pharmacies-de-paris) — {city.id} ...')
    records = get_json(_EXPORT_URL, timeout=_TIMEOUT_S)
    print(f'  Retrieved {len(records)} pharmacy records for {city.id}', file=sys.stderr)

    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in records:
        fields = rec.get('fields') or {}
        lon, lat = fields.get('lng'), fields.get('lat')
        if lon is None or lat is None:
            continue
        # FINESS établissement id is the stable per-pharmacy key; fall back to the
        # ODS record id on the rare row that lacks one.
        finess = fields.get('nofinesset')
        feat_id = f'pharmacy/{finess}' if finess is not None else f'pharmacy/{rec.get("recordid")}'
        if feat_id in seen:
            continue
        seen.add(feat_id)
        features.append(make_feature(
            feat_id, titlecase(fields.get('rs')), 'pharmacy', lon, lat, _address(fields),
        ))

    return {'type': 'FeatureCollection', 'features': features}
