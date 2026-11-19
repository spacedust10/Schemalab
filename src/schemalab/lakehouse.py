"""The lakehouse, seen from the API: a client for the ingest service and the model check.

The API never touches the lake itself. It hands work to the ingest service (which runs dlt
on DuckDB 1.5) and reads only the DuckDB file ingest publishes for Ask.
"""

import re
from graphlib import CycleError, TopologicalSorter
from typing import Any

import httpx
import sqlglot
from sqlglot import exp

from schemalab.guards.sql import UnsafeSQLError, check_sql, visible_ctes

MAX_MODELS = 20
MAX_SQL = 5000
MAX_CHECKS = 20
TABLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")
LAYERS = ("bronze", "silver", "gold")


class LakehouseError(ValueError):
    """A refusal or a failed run; the message is safe to show the user."""


class Unavailable(RuntimeError):
    """The ingest service did not answer."""


def lake_of(user: dict[str, Any]) -> str:
    # The id is an integer from the session lookup, never user input.
    return f"u{int(user['id'])}"


def _reads(sql: str) -> set[str]:
    """The tables a model's SELECT names, minus CTEs in scope where each is named.

    Nothing if it won't parse: the SQL guard then refuses it with a proper message.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except sqlglot.errors.ParseError:
        return set()
    return {
        t.name.lower() for t in tree.find_all(exp.Table) if t.name.lower() not in visible_ctes(t)
    }


def order_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each model after the silver models it reads; models that are ready together run in
    the order listed.

    A model that reads its own table reads the previous build, so that is not a dependency.
    """
    by_table = {f"silver_{m['name']}": i for i, m in enumerate(models) if m["layer"] == "silver"}
    graph = TopologicalSorter[int]()
    for i, m in enumerate(models):
        graph.add(i, *(by_table[t] for t in _reads(m["sql"]) if by_table.get(t, i) != i))
    try:
        graph.prepare()
    except CycleError as e:
        loop = " → ".join(f"{models[i]['layer']}.{models[i]['name']}" for i in reversed(e.args[1]))
        raise LakehouseError(f"These models read each other in a loop: {loop}") from None
    out = []
    while graph.is_active():
        ready = sorted(graph.get_ready())
        out += [models[i] for i in ready]
        graph.done(*ready)
    return out


def check_models(
    models: list[dict[str, Any]], tables: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The models in the order their tables need, each SELECT passed through the SQL guard.

    A model reads bronze and silver tables by layer_table names (bronze_orders), including
    any silver model's; gold tables are never read. Ingest checks again with DuckDB's parser.
    """
    allowed = {f"{t['layer']}_{t['name']}" for t in tables if t["layer"] != "gold"}
    allowed |= {f"silver_{m['name']}" for m in models if m["layer"] == "silver"}
    out = []
    for m in order_models(models):
        target = f"{m['layer']}.{m['name']}"
        try:
            sql = check_sql(m["sql"], allowed)
        except UnsafeSQLError as e:
            raise LakehouseError(f"{target}: {e}") from None
        checks = {k: m.get(k, []) for k in ("not_null", "unique")}  # older saves have none
        out.append({"layer": m["layer"], "name": m["name"], "sql": sql, **checks})
    return out


class Ingest:
    """The ingest service's HTTP API, called with the token only the API holds.

    A load or transform answers {"tables": rows per table, "snapshot": the lake's version after}.
    """

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

    def load_file(self, lake: str, file: str, table: str) -> dict[str, Any]:
        body = {"file": file, "table": table}
        return dict(self._call("POST", f"/lakes/{lake}/load/file", json=body))

    def load_sheet(self, lake: str, url: str, table: str) -> dict[str, Any]:
        body = {"url": url, "table": table}
        return dict(self._call("POST", f"/lakes/{lake}/load/sheet", json=body))

    def load_quakes(self, lake: str) -> dict[str, Any]:
        return dict(self._call("POST", f"/lakes/{lake}/load/quakes"))

    def transform(self, lake: str, models: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"models": models}
        return dict(self._call("POST", f"/lakes/{lake}/transform", json=body))

    def tables(self, lake: str) -> list[dict[str, Any]]:
        return list(self._call("GET", f"/lakes/{lake}/tables"))

    def snapshots(self, lake: str) -> list[dict[str, Any]]:
        return list(self._call("GET", f"/lakes/{lake}/snapshots"))

    def preview(self, lake: str, layer: str, table: str, version: int | None) -> dict[str, Any]:
        params = {"version": version} if version is not None else {}
        return dict(self._call("GET", f"/lakes/{lake}/tables/{layer}/{table}", params=params))
