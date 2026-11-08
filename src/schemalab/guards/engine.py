"""Read-only DuckDB connections: the second line of defense behind the SQL guard.

Even if a query gets past the parser guard, this connection cannot write, read files,
reach the network, load extensions or change its own settings.
"""

from pathlib import Path

import duckdb

_LOCKED_CONFIG = {
    "access_mode": "READ_ONLY",
    "enable_external_access": "false",
    "autoinstall_known_extensions": "false",
    "autoload_known_extensions": "false",
    # Must come last: once set, no setting above can be changed by a query.
    "lock_configuration": "true",
}


def open_readonly(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), config=dict(_LOCKED_CONFIG))
