"""Name-normalisation and 50 m same-name/same-type deduplication.

Mirrors the conflation pass from the retired scripts/fetch-stores.mjs.
"""

from __future__ import annotations

import math
import unicodedata
import re
from typing import Any

CONFLATION_RADIUS_M = 50.0


def _norm_name(name: str | None) -> str:
    """Lowercase, strip diacritics (NFD + remove combining marks), collapse
    punctuation and whitespace.  Mirrors JS normName() exactly.
    """
    if not name:
        return ''
    # NFD decompose, then drop combining marks (Unicode category 'Mn')
    nfd = unicodedata.normalize('NFD', name)
    stripped = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
    lowered = stripped.lower()
    # Replace non-word / non-space chars with space (mirrors /[^\w\s]/g → ' ')
    no_punct = re.sub(r'[^\w\s]', ' ', lowered)
    # Collapse runs of whitespace
    return re.sub(r'\s+', ' ', no_punct).strip()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two (lat, lon) pairs."""
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def conflate(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove node+way duplicate pairs: same normalised name, same canonical
    type, within 50 m.  Prefer keeping a node over a way/relation; otherwise
    keep first seen.  Features with empty normalised names are never conflated.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    unnamed: list[dict[str, Any]] = []

    for f in features:
        nn = _norm_name(f['properties'].get('name'))
        if not nn:
            unnamed.append(f)
            continue
        key = f"{nn}|{f['properties']['shop']}"
        groups.setdefault(key, []).append(f)

    kept: list[dict[str, Any]] = list(unnamed)
    removed = 0

    for group in groups.values():
        if len(group) == 1:
            kept.append(group[0])
            continue

        # Pairwise distance check within name+type group.
        # O(k²) but k is almost always 2–3.
        suppress: set[int] = set()
        for i in range(len(group)):
            if i in suppress:
                continue
            for j in range(i + 1, len(group)):
                if j in suppress:
                    continue
                lon_i, lat_i = group[i]['geometry']['coordinates']
                lon_j, lat_j = group[j]['geometry']['coordinates']
                if _haversine_m(lat_i, lon_i, lat_j, lon_j) <= CONFLATION_RADIUS_M:
                    # Prefer keeping nodes over ways/relations
                    type_i = group[i]['properties']['id'].split('/')[0]
                    type_j = group[j]['properties']['id'].split('/')[0]
                    if type_i == 'node' and type_j != 'node':
                        suppress.add(j)
                    elif type_j == 'node' and type_i != 'node':
                        suppress.add(i)
                    else:
                        suppress.add(j)  # both same type — keep first seen

        for i, f in enumerate(group):
            if i in suppress:
                removed += 1
            else:
                kept.append(f)

    print(f'Conflated {removed} duplicate(s)')
    return kept
