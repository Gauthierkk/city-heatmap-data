"""Resolve the latest SIRENE Parquet download URLs from the data.gouv.fr API.

The two source files are re-published monthly under stable *dataset* ids but with
a fresh, date-stamped resource URL each time. Hardcoding a URL would silently go
stale, so we resolve the current Parquet resource by querying the dataset's API
and picking the right resource by format + title.

  - Geolocation (INSEE):  dataset 61d5e2d372a52d9f9411ff88
      one Parquet resource - `siret` + `x_longitude`/`y_latitude` (already WGS84).
  - StockEtablissement (SIRENE base): dataset slug below
      several Parquet resources (StockEtablissement, …Historique, …LiensSuccession,
      StockUniteLegale) - we want the plain establishment stock.
"""

from __future__ import annotations

from ...http import get_json

GEO_DATASET_ID = '61d5e2d372a52d9f9411ff88'
STOCK_DATASET_ID = 'base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret'

# The plain establishment stock resource title looks like:
#   "Sirene : Fichier StockEtablissement - 01 juin 2026 (format parquet)"
# The trailing " - " after the file name discriminates it from the related
# StockEtablissementHistorique / StockEtablissementLiensSuccession resources.
_STOCK_TITLE_MARKER = 'stocketablissement -'


def _resources(dataset_id: str) -> list[dict]:
    url = f'https://www.data.gouv.fr/api/1/datasets/{dataset_id}/'
    return get_json(url, timeout=60).get('resources') or []


def _pick(resources: list[dict], title_marker: str | None) -> str:
    for r in resources:
        if (r.get('format') or '').lower() != 'parquet':
            continue
        if title_marker and title_marker not in (r.get('title') or '').lower():
            continue
        url = r.get('url')
        if url:
            return url
    raise RuntimeError(
        f'No matching Parquet resource found (title marker: {title_marker!r}).'
    )


def geoloc_parquet_url() -> str:
    """Latest URL of the INSEE geolocation Parquet (one per dataset)."""
    return _pick(_resources(GEO_DATASET_ID), title_marker=None)


def stock_parquet_url() -> str:
    """Latest URL of the StockEtablissement Parquet (the plain establishment stock)."""
    return _pick(_resources(STOCK_DATASET_ID), title_marker=_STOCK_TITLE_MARKER)
