"""The lakehouse, seen from the API: a client for the ingest service and the model check.

The API never touches the lake itself. It hands work to the ingest service (which runs dlt
on DuckDB 1.5) and reads only the DuckDB file ingest publishes for Ask.
"""

import re
from typing import Any

import httpx

from schemalab.guards.sql import UnsafeSQLError, check_sql

MAX_MODELS = 20
MAX_SQL = 5000
TABLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
LAYERS = ("bronze", "silver", "gold")


class LakehouseError(ValueError):
    """A refusal or a failed run; the message is safe to show the user."""


class Unavailable(RuntimeError):
    """The ingest service did not answer."""


def lake_of(user: dict[str, Any]) -> str:
    # The id is an integer from the session lookup, never user input.
    return f"u{int(user['id'])}"


def check_models(
    models: list[dict[str, str]], tables: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Silver models first, then gold, each SELECT passed through the SQL guard.

    A model reads bronze and silver tables by layer_table names (bronze_orders); a gold model
    may also read a silver model listed before it. Ingest checks again with DuckDB's parser.
    """
    allowed = {f"{t['layer']}_{t['name']}" for t in tables if t["layer"] != "gold"}
    ordered = sorted(models, key=lambda m: m["layer"] != "silver")
    out = []
    for m in ordered:
        target = f"{m['layer']}.{m['name']}"
        try:
            sql = check_sql(m["sql"], allowed)
        except UnsafeSQLError as e:
            raise LakehouseError(f"{target}: {e}") from None
        out.append({"layer": m["layer"], "name": m["name"], "sql": sql})
        if m["layer"] == "silver":
            allowed.add(f"silver_{m['name']}")
    return out


class Ingest:
    """The ingest service's HTTP API, called with the token only the API holds."""

    def __init__(self, base_url: str, token: str) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(10, read=900),  # a load can take minutes
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise Unavailable(str(e)) from e
        if resp.status_code in (400, 404, 409):
            raise LakehouseError(str(resp.json().get("detail", "The lakehouse refused that.")))
        if resp.status_code != 200:
            raise Unavailable(f"ingest answered {resp.status_code}")
        return resp.json()

    def load_file(self, lake: str, file: str, table: str) -> dict[str, int]:
        body = {"file": file, "table": table}
        return dict(self._call("POST", f"/lakes/{lake}/load/file", json=body)["tables"])

    def load_sheet(self, lake: str, url: str, table: str) -> dict[str, int]:
        body = {"url": url, "table": table}
        return dict(self._call("POST", f"/lakes/{lake}/load/sheet", json=body)["tables"])

    def load_quakes(self, lake: str) -> dict[str, int]:
        return dict(self._call("POST", f"/lakes/{lake}/load/quakes")["tables"])

    def transform(self, lake: str, models: list[dict[str, str]]) -> dict[str, int]:
        body = {"models": models}
        return dict(self._call("POST", f"/lakes/{lake}/transform", json=body)["tables"])

    def tables(self, lake: str) -> list[dict[str, Any]]:
        return list(self._call("GET", f"/lakes/{lake}/tables"))

    def snapshots(self, lake: str) -> list[dict[str, Any]]:
        return list(self._call("GET", f"/lakes/{lake}/snapshots"))

    def preview(self, lake: str, layer: str, table: str, version: int | None) -> dict[str, Any]:
        params = {"version": version} if version is not None else {}
        return dict(self._call("GET", f"/lakes/{lake}/tables/{layer}/{table}", params=params))
