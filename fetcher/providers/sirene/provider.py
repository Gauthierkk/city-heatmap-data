"""SIRENE establishment fetch — Paris-only food/fitness enrichment.

Joins INSEE's geocoded SIRENE file (siret → WGS84 lon/lat) against the SIRENE
StockEtablissement base (siret → activity code, name, address) over DuckDB,
filtered to active Paris establishments in the food/fitness NAF codes. Both files
are Parquet on data.gouv.fr; DuckDB streams only the needed columns/rows via HTTP
range requests, so the multi-GB stock file is queried in seconds without download.

SIRENE is FRANCE-ONLY, so this provider serves Paris and returns an empty set for
every other city. Coverage is strongest for the long-tail specialist food shops
(butcher, bakery, beverages, greengrocer) that crowd-sourced sources miss; for
fitness it adds little beyond what OSM/Overture already give and is kept narrow.

duckdb is imported lazily so non-SIRENE commands keep working without it. Pass
--no-sirene to skip this provider entirely.
"""

from __future__ import annotations

import sys
from typing import Any

from ...cities import CityDef
from ...duckdb_io import connect, require_duckdb, sql_str_list
from ...transform.geojson_io import make_feature
from . import datagouv
from .naf import NAF_TO_SHOP, naf_codes_for

# Paris intra-muros INSEE commune codes (the 20 arrondissements, 75101–75120).
# This is the whole France-only scope of the provider.
PARIS_COMMUNES: tuple[str, ...] = tuple(f'{75100 + i}' for i in range(1, 21))


def _titlecase(value: str | None) -> str | None:
    """SIRENE text is ALL-CAPS; title-case it for display parity with other
    providers, but leave already-mixed-case strings untouched."""
    if value and value.isupper():
        return value.title()
    return value


_QUERY_TMPL = """\
WITH stock AS (
    SELECT
        siret,
        activitePrincipaleEtablissement AS naf,
        coalesce(
            nullif(trim(denominationUsuelleEtablissement), ''),
            nullif(trim(enseigne1Etablissement), '')
        )                                                 AS name,
        nullif(trim(numeroVoieEtablissement), '')         AS housenumber,
        nullif(trim(concat_ws(' ', typeVoieEtablissement,
                              libelleVoieEtablissement)), '') AS street,
        nullif(trim(codePostalEtablissement), '')         AS postcode,
        nullif(trim(libelleCommuneEtablissement), '')     AS city
    FROM read_parquet('{stock_url}')
    WHERE etatAdministratifEtablissement = 'A'
      AND codeCommuneEtablissement IN ({communes})
      AND activitePrincipaleEtablissement IN ({naf_codes})
),
geo AS (
    SELECT siret, x_longitude AS lon, y_latitude AS lat
    FROM read_parquet('{geo_url}')
    WHERE plg_code_commune IN ({communes})
      AND x_longitude IS NOT NULL
      AND y_latitude IS NOT NULL
)
SELECT s.siret, s.naf, s.name, s.housenumber, s.street, s.postcode, s.city,
       g.lon, g.lat
FROM stock s JOIN geo g USING (siret)
WHERE s.name IS NOT NULL
ORDER BY s.siret
"""


def fetch_sirene(city: CityDef, dataset_id: str = 'food') -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of Paris SIRENE establishments.

    Empty for any city other than Paris (SIRENE is France-only) and for datasets
    with no mapped NAF codes. id is 'sirene/<siret>'; each feature carries a
    {housenumber, street, postcode, city} address subset when present.

    Raises ImportError (with install hint) if duckdb is missing.
    """
    empty = {'type': 'FeatureCollection', 'features': []}

    if city.id != 'paris':
        return empty  # SIRENE covers France only

    naf_codes = naf_codes_for(dataset_id)
    if not naf_codes:
        return empty

    duckdb = require_duckdb('SIRENE')

    geo_url = datagouv.geoloc_parquet_url()
    stock_url = datagouv.stock_parquet_url()

    query = _QUERY_TMPL.format(
        stock_url=stock_url,
        geo_url=geo_url,
        communes=sql_str_list(PARIS_COMMUNES),
        naf_codes=sql_str_list(naf_codes),
    )

    print(f'Querying SIRENE (data.gouv) — paris {dataset_id} ...')

    con = connect(duckdb)
    rows = con.execute(query).fetchall()
    print(f'  Retrieved {len(rows)} SIRENE establishments for paris/{dataset_id}',
          file=sys.stderr)

    features: list[dict[str, Any]] = []
    for siret, naf, name, housenumber, street, postcode, city_name, lon, lat in rows:
        shop = NAF_TO_SHOP.get(naf)
        if shop is None:
            continue  # guarded by the NAF filter, but stay safe
        features.append(make_feature(
            f'sirene/{siret}',
            _titlecase(name),
            shop,
            lon,
            lat,
            {
                'housenumber': housenumber,
                'street': _titlecase(street),
                'postcode': postcode,
                'city': _titlecase(city_name),
            },
        ))

    return {'type': 'FeatureCollection', 'features': features}
