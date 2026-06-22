"""Configuration / secret loading (stdlib only).

API keys are read from the process environment first, then from a `.env` file at
the repo root (gitignored). Keeping this here means providers don't each
re-implement env handling, and a missing key degrades gracefully (the provider
is skipped) rather than crashing the run.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Repo root: fetcher/config.py -> fetcher/ -> <repo root>/
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _REPO_ROOT / '.env'


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    """Parse the repo-root .env into a dict. Missing file → empty dict.

    Supports plain `KEY=VALUE` lines; ignores blanks and `#` comments and strips
    surrounding quotes. Not a full dotenv implementation - just enough for keys.
    """
    values: dict[str, str] = {}
    if not _ENV_FILE.exists():
        return values
    for raw in _ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def get(name: str) -> str | None:
    """Return a config value: environment variable wins, then .env, else None."""
    env = os.environ.get(name)
    if env:
        return env
    val = _dotenv().get(name)
    return val or None


def get_geoapify_key() -> str | None:
    """Geoapify Places API key, or None when unconfigured."""
    return get('GEOAPIFY_KEY')
