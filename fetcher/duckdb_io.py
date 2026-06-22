"""Shared DuckDB helpers for the Parquet-backed providers (Overture S3 + SIRENE).

duckdb is an optional dependency, imported lazily so the stdlib-only providers
(OSM, boundary, Geoapify, trees, transit) keep working without it. These helpers
centralise the "is duckdb installed?" check (with one canonical install hint) and
the connection bootstrap (httpfs / spatial extensions, anonymous S3).
"""

from __future__ import annotations

from typing import Any, Iterable


def sql_str_list(values: Iterable[Any]) -> str:
    """Render an iterable of values as a SQL string list: ``'a', 'b', 'c'``."""
    return ', '.join(f"'{v}'" for v in values)


def require_duckdb(provider: str) -> Any:
    """Import and return the duckdb module, or raise ImportError with an install hint.

    `provider` names the caller in the error message (e.g. 'Overture', 'SIRENE').
    """
    try:
        import duckdb
    except ImportError:
        raise ImportError(
            f'duckdb is required for the {provider} provider but is not installed.\n'
            'Install with:\n'
            '  uv sync\n'
            'Or skip this provider with the matching --no-* flag.'
        ) from None
    return duckdb


def connect(duckdb: Any, *, spatial: bool = False, s3: bool = False) -> Any:
    """Open a DuckDB connection with httpfs (+ optional spatial / anonymous S3).

    httpfs is always loaded - both providers stream remote Parquet over HTTP.
    Anonymous S3 (`s3=True`) targets the public Overture us-west-2 bucket. The
    progress bar is disabled so logs stay clean in non-interactive runs.
    """
    con = duckdb.connect()
    con.execute('INSTALL httpfs; LOAD httpfs;')
    if spatial:
        con.execute('INSTALL spatial; LOAD spatial;')
    if s3:
        con.execute("SET s3_region='us-west-2';")
        con.execute("SET s3_access_key_id=''; SET s3_secret_access_key='';")
    con.execute('SET enable_progress_bar=false;')
    return con
