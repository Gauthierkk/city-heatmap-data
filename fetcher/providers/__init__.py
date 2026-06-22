"""Data-source providers and the registry used to select them.

Each Provider is a uniform `fetch(city, dataset_id) -> FeatureCollection`. The
`name` is only used for CLI selection and log lines - it is NOT written to the
output (the downstream merge is source-agnostic).

To add a provider: implement a `fetch(city, dataset_id)` and append a Provider
entry below. The CLI and aggregator pick it up automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..cities import CityDef
from .overpass import fetch_osm
from .overture import fetch_overture
from .geoapify import fetch_geoapify
from .sirene import fetch_sirene


@dataclass(frozen=True)
class Provider:
    name: str                                   # 'osm' | 'overture' | 'geoapify' | 'sirene'
    datasets: frozenset[str]                    # which datasets it can serve
    fetch: Callable[[CityDef, str], dict[str, Any]]


ALL_PROVIDERS: list[Provider] = [
    Provider('osm', frozenset({'food', 'fitness'}), fetch_osm),
    Provider('overture', frozenset({'fitness'}), fetch_overture),
    Provider('geoapify', frozenset({'food', 'fitness'}), fetch_geoapify),
    # France-only: serves Paris, empty elsewhere (gated inside fetch_sirene).
    Provider('sirene', frozenset({'food', 'fitness'}), fetch_sirene),
]

PROVIDER_NAMES: list[str] = [p.name for p in ALL_PROVIDERS]


def providers_for(
    dataset_id: str,
    allow: set[str] | None = None,
    deny: set[str] | None = None,
) -> list[Provider]:
    """Providers that serve `dataset_id`, filtered by optional allow/deny sets."""
    deny = deny or set()
    out = []
    for p in ALL_PROVIDERS:
        if dataset_id not in p.datasets:
            continue
        if allow is not None and p.name not in allow:
            continue
        if p.name in deny:
            continue
        out.append(p)
    return out
