"""The analyst as a LangGraph state machine.

    schema -> plan -> write_sql -> run --rows--> critic --> answer
                          ^         |               |
                          +--error--+---no rows-----+   (up to MAX_ATTEMPTS)

The critic's verdict sets the confidence; an objection does not trigger a rewrite (#24).

Every node reports what it did through the stream writer, so `/ask` can show each step as
it happens. Model prompts carry the schema and the agent's own SQL, never row values.
"""

import json
import re
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypedDict

import duckdb
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from schemalab.agent import prompts
from schemalab.agent.answer import confidence, summarize, tables_read
from schemalab.agent.schema import describe_schema
from schemalab.guards.engine import open_readonly
from schemalab.guards.run import QueryResult, QueryTimeoutError, run_query
from schemalab.guards.sql import UnsafeSQLError
from schemalab.llm import AllModelsFailedError, Completion, Gateway

MAX_ATTEMPTS = 3

_FENCE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)

Event = dict[str, Any]


class State(TypedDict, total=False):
    question: str
    schema: str
    plan: str
    sql: str  # the latest model SQL, before the guard
    attempt: int
    problem: str | None  # why the latest attempt must be redone
    result: QueryResult | None
    verdict: bool | None  # critic: True ok, False objected, None no usable verdict
    empty: bool  # the latest result had no rows, which is retried without the critic's model
    failed: bool


def run_agent(
    question: str,
    db_path: Path,
    gateway: Gateway,
    share_results: bool = False,
    notes: Sequence[str] = (),
    critic: bool = True,
) -> Iterator[Event]:
    """Stream the analyst's events. Result rows reach the model only if `share_results`.

    `notes` are the asking user's corrections, added to the planner, writer and critic.
    `critic=False` skips the reviewer, for measuring what it adds (#24).
    """
    if not _has_tables(db_path):
        yield {
            "step": "error",
            "stage": "data",
            "message": f"No sample data at {db_path}. Build it with: "
            "uv run python -m schemalab.datasets",
        }
        return
    graph = _build(db_path, gateway, share_results, prompts.notes_block(list(notes)), critic)
    yield from graph.stream({"question": question, "attempt": 0}, stream_mode="custom")


def _build(
    db_path: Path, gateway: Gateway, share_results: bool, notes: str, use_critic: bool
) -> Any:
    models_used: list[str] = []  # the graph is built per question, so this is per run

    def ask_model(messages: list[dict[str, str]]) -> Completion | None:
        try:
            reply = gateway.complete(messages)
            if reply.model not in models_used:
                models_used.append(reply.model)
            return reply
        except AllModelsFailedError as e:
            get_stream_writer()({"step": "error", "stage": "model", "message": str(e)})
            return None

    def schema(state: State) -> State:
        text = describe_schema(db_path)
        get_stream_writer()({"step": "schema", "tables": text.count(" rows)")})
        return {"schema": text}

    def plan(state: State) -> State:
        reply = ask_model(
            [
                {
                    "role": "system",
                    "content": prompts.PLAN.format(schema=state["schema"], notes=notes),
                },
                {"role": "user", "content": state["question"]},
            ]
        )
        if reply is None:
            return {"failed": True}
        get_stream_writer()({"step": "plan", "text": reply.text.strip(), **_usage(reply)})
        return {"plan": reply.text.strip()}

    def write_sql(state: State) -> State:
        system = prompts.WRITE_SQL.format(plan=state["plan"], schema=state["schema"], notes=notes)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": state["question"]},
        ]
        if state.get("problem"):
            messages.append({"role": "assistant", "content": state["sql"]})
            messages.append(
                {
                    "role": "user",
                    "content": prompts.RETRY.format(sql=state["sql"], problem=state["problem"]),
                }
            )
        reply = ask_model(messages)
        if reply is None:
            return {"failed": True}
        return {"sql": extract_sql(reply.text), "attempt": state["attempt"] + 1, "problem": None}

    def run(state: State) -> State:
        writer = get_stream_writer()
        attempt, sql = state["attempt"], state["sql"]
        stage: Literal["guard", "run"]
        try:
            result = run_query(db_path, sql)
        except UnsafeSQLError as e:
            stage, message = "guard", str(e)
        except (QueryTimeoutError, duckdb.Error) as e:
            stage, message = "run", str(e)
        else:
            writer({"step": "sql", "sql": result.sql, "attempt": attempt})
            writer(
                {
                    "step": "result",
                    "columns": result.columns,
                    "rows": [[json_value(v) for v in row] for row in result.rows],
                    "truncated": result.truncated,
                }
            )
            return {"result": result}

        failure = {"stage": stage, "message": message, "sql": sql}
        if attempt >= MAX_ATTEMPTS:
            writer({"step": "error", **failure, "attempts": attempt})
            return {"failed": True, "result": None}
        writer({"step": "retry", **failure, "attempt": attempt})
        # The user sees the full error; the model gets it with data values removed, since
        # database errors can quote cell contents ("Could not convert string 'x' ...").
        problem = redact_values(message) if stage == "run" else message
        if message.startswith("Binder Error"):
            # DuckDB's hint names one near match; models repeat a made-up column without the list.
            problem += "\n\nThe tables this query reads have only these columns:\n\n"
            problem += describe_schema(db_path, tables_read(sql))
        return {"problem": problem, "result": None}

    def critic(state: State) -> State:
        result = state.get("result")
        if result is None:
            raise RuntimeError("critic reached without a result")  # routing guarantees one
        prompt = prompts.CRITIC.format(
            question=state["question"],
            plan=state["plan"],
            sql=result.sql,
            row_count=len(result.rows),
            columns=", ".join(result.columns),
            notes=notes,
        )
        writer = get_stream_writer()
        attempt = state["attempt"]
        if not result.rows:
            # No model needed: an empty result is suspicious enough to try again.
            issue: str | None = "The query returned no rows; check the filters and joins."
            writer({"step": "critic", "ok": False, "issue": issue, "attempt": attempt})
            return {"verdict": False, "problem": issue, "empty": True}

        reply = ask_model([{"role": "user", "content": prompt}])
        if reply is None:
            return {"failed": True}
        ok, issue = _parse_verdict(reply.text)
        writer({"step": "critic", "ok": ok, "issue": issue, "attempt": attempt, **_usage(reply)})
        return {"verdict": ok, "problem": issue if ok is False else None, "empty": False}

    def answer(state: State) -> State:
        result = state.get("result")
        if result is None:
            raise RuntimeError("answer reached without a result")  # routing guarantees one
        rows = [[json_value(v) for v in row] for row in result.rows]
        text = summarize(result.columns, rows, result.truncated)
        if share_results:
            shown = rows[:20]
            reply = ask_model(
                [
                    {
                        "role": "user",
                        "content": prompts.ANSWER.format(
                            question=state["question"],
                            columns=", ".join(result.columns),
                            shown=len(shown),
                            total=len(rows),
                            rows="\n".join(map(str, shown)),
                        ),
                    }
                ]
            )
            if reply is not None:  # fall back to the plain summary if the model is down
                text = reply.text.strip()
        get_stream_writer()(
            {
                "step": "answer",
                "text": text,
                "confidence": confidence(
                    attempts=state["attempt"],
                    verdict=state.get("verdict"),
                    issue=state.get("problem"),
                    truncated=result.truncated,
                    row_count=len(rows),
                    reviewed=use_critic,
                ),
                "provenance": {
                    "sql": result.sql,
                    "tables": tables_read(result.sql),
                    "attempts": state["attempt"],
                    "models": models_used,
                    "results_shared_with_model": share_results,
                },
            }
        )
        return {}

    def after_critic(state: State) -> str:
        if state.get("failed"):
            return END
        # The critic judges and does not rewrite (#24): in the eval its rewrites fixed no
        # answer and broke two, while its verdict was a good confidence signal. An empty
        # result is still retried, since that check involves no model.
        retry = state.get("empty") and state["attempt"] < MAX_ATTEMPTS
        return "write_sql" if retry else "answer"

    def after_model(next_node: str) -> Any:
        return lambda state: END if state.get("failed") else next_node

    def after_run(state: State) -> str:
        if state.get("failed"):
            return END
        if state.get("result") is None:
            return "write_sql"
        return "critic" if use_critic else "answer"

    g = StateGraph(State)
    for name, fn in [
        ("schema", schema),
        ("plan", plan),
        ("write_sql", write_sql),
        ("run", run),
        ("critic", critic),
        ("answer", answer),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "schema")
    g.add_edge("schema", "plan")
    g.add_conditional_edges("plan", after_model("write_sql"))
    g.add_conditional_edges("write_sql", after_model("run"))
    g.add_conditional_edges("run", after_run)
    g.add_conditional_edges("critic", after_critic)
    g.add_edge("answer", END)
    return g.compile()


def _has_tables(db_path: Path) -> bool:
    if not db_path.is_file():
        return False
    with open_readonly(db_path) as con:
        return bool(con.execute("SHOW TABLES").fetchall())


def _parse_verdict(text: str) -> tuple[bool | None, str | None]:
    """Read the critic's JSON. Anything unreadable is "no verdict", not a pass."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        return None, None
    issue = data.get("issue")
    return data["ok"], str(issue) if issue and not data["ok"] else None


def _usage(reply: Completion) -> Event:
    return {
        "model": reply.model,
        "failed_models": reply.failed,
        "tokens": reply.prompt_tokens + reply.completion_tokens,
        "latency_ms": round(reply.latency_ms),
    }


_QUOTED = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def redact_values(message: str) -> str:
    """Strip quoted strings and bare numbers. Identifiers are double-quoted and survive."""
    return _NUMBER.sub("<number>", _QUOTED.sub("'<value>'", message))


def extract_sql(text: str) -> str:
    """Take the SQL out of a model reply: the first fenced block, else the whole reply."""
    match = _FENCE.search(text)
    return (match.group(1) if match else text).strip()


def json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
