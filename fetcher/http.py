"""Shared HTTP helpers (stdlib only).

One `User-Agent`, one Overpass endpoint list, and the two request patterns every
provider used to re-implement:

  - `get_json`      — GET a JSON endpoint, retrying transient (5xx / network)
                      errors and failing fast on 4xx. Used by Geoapify, the Paris
                      trees/transit bulk exports, the data.gouv resource lookup,
                      and the polygons.openstreetmap.fr boundary source.
  - `post_overpass` — POST an Overpass QL query to each mirror in turn until one
                      succeeds. Used by the OSM stores fetch and the boundary
                      fallback.

Centralising these means retry/failover semantics are uniform (the bulk exports
now get retries they previously lacked) and the User-Agent / endpoint list are
defined exactly once.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:  # Track the version declared in pyproject.toml when the package is installed.
    from importlib.metadata import version

    _VERSION = version('city-heatmap-data')
except Exception:  # not installed (e.g. run straight from a checkout)
    _VERSION = '0.1.0'

# Single User-Agent for every outbound request (was redefined in six modules with
# drifting text). Identifies the worker to the upstream data sources.
USER_AGENT = f'city-heatmap-data/{_VERSION} (weekly data refresh worker)'

# Public Overpass mirrors, tried in order (was duplicated in overpass + boundary).
OVERPASS_ENDPOINTS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
]


def get_json(url: str, *, timeout: int = 60, retries: int = 3) -> Any:
    """GET `url` and return parsed JSON, retrying transient (5xx / network) errors.

    A 4xx response (bad key, bad query, …) won't fix itself, so it fails fast.
    Raises RuntimeError if every attempt fails.
    """
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    last_error: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise RuntimeError(f'HTTP {exc.code} for {url}: {exc.read().decode()[:200]}') from None
            last_error = exc
        except Exception as exc:  # transient network / 5xx — retry
            last_error = exc
    raise RuntimeError(f'Request to {url} failed after {retries} attempt(s): {last_error}')


def post_overpass(query: str, *, timeout: int | None = None, note: str = '') -> dict[str, Any]:
    """POST an Overpass QL query to each mirror in turn; return parsed JSON.

    `note` is appended to the per-endpoint progress line (e.g. 'for relation 7444').
    `timeout` is the client socket timeout; the default (None) defers to the query's
    own server-side `[out:json][timeout:N]`. Raises RuntimeError if all mirrors fail.
    """
    body = urllib.parse.urlencode({'data': query}).encode()
    suffix = f' {note}' if note else ''
    last_error: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            print(f'Querying {endpoint}{suffix} ...')
            req = urllib.request.Request(
                endpoint, data=body, method='POST',
                headers={'User-Agent': USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            print(f'  failed: {exc}', file=sys.stderr)
            last_error = exc
    raise RuntimeError(f'All Overpass endpoints failed. Last error: {last_error}')
