"""NAF (rév. 2) activity code → canonical `shop` value.

SIRENE classifies every establishment by an APE/NAF activity code. We map the
food-retail and fitness codes onto the SAME `shop` vocabulary the other providers
emit (OSM `shop=*` values), so SIRENE points MERGE with existing OSM/Geoapify
points instead of duplicating them - the food aggregation only merges records that
share a `shop` type (see transform/aggregate.py, cross_type=False).

Deliberately excluded:
  47.11E  magasins multi-commerces  - mixed/non-food bazaars, too noisy
  47.26Z  commerce de tabac         - tobacconists, not a food shop
  93.12Z / 85.51Z / 96.04Z          - broad "sport club / sport teaching / body
                                       upkeep" codes; they pull in football clubs,
                                       swim schools, spas and tanning salons, so
                                       fitness stays the narrow gym code only.
"""

from __future__ import annotations

# Specialised food retail (NAF 47.2x) + the two bakery-manufacture codes that
# cover most French "boulangerie/pâtisserie" storefronts (10.71x), plus general
# food retail (47.11x). 47.29Z is the catch-all specialist grocer (cheese,
# chocolate, coffee…) - mapped to the generic 'food' bucket.
_FOOD: dict[str, str] = {
    '47.22Z': 'butcher',
    '47.23Z': 'seafood',
    '47.21Z': 'greengrocer',
    '47.25Z': 'beverages',
    '47.24Z': 'bakery',
    '10.71C': 'bakery',
    '10.71D': 'pastry',
    '47.11A': 'frozen_food',
    '47.11B': 'convenience',
    '47.11C': 'supermarket',
    '47.11D': 'supermarket',
    '47.11F': 'supermarket',
    '47.29Z': 'food',
}

# "Activités des centres de culture physique" - gyms/fitness centres.
_FITNESS: dict[str, str] = {
    '93.13Z': 'gym',
}

# Full mapping used to translate a row's NAF code into a `shop` value.
NAF_TO_SHOP: dict[str, str] = {**_FOOD, **_FITNESS}


def naf_codes_for(dataset_id: str) -> frozenset[str]:
    """NAF codes SIRENE serves for a dataset ('food' | 'fitness'); empty otherwise."""
    if dataset_id == 'food':
        return frozenset(_FOOD)
    if dataset_id == 'fitness':
        return frozenset(_FITNESS)
    return frozenset()
