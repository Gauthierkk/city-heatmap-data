"""SIRENE provider - Paris-only food/fitness enrichment from data.gouv.fr.

France-only registry data (INSEE geolocated SIRENE joined to StockEtablissement),
served for Paris and empty elsewhere. See provider.py for details.
"""

from __future__ import annotations

from .provider import fetch_sirene

__all__ = ['fetch_sirene']
