"""One pass of the analyst: question -> schema -> model SQL -> guard -> rows, as events."""

from pathlib import Path
from typing import Any

import litellm
import pytest

from schemalab.agent.ask import ask, extract_sql
from schemalab.llm import Gateway

MODEL = "ollama_chat/llama3.1"


def gateway(reply: str | Exception) -> Gateway:
    return Gateway([MODEL], options={MODEL: {"mock_response": reply}})


def steps(events: list[dict[str, Any]]) -> list[str]:
    return [e["step"] for e in events]


@pytest.mark.parametrize(
    "text",
    [
        "SELECT count(*) FROM orders",
        "```sql\nSELECT count(*) FROM orders\n```",
        "Here is the query:\n```\nSELECT count(*) FROM orders;\n```\nIt counts orders.",
        "```SQL\nSELECT count(*) FROM orders\n```",
    ],
)
def test_extracts_sql_from_model_replies(text: str) -> None:
    assert extract_sql(text).rstrip(";") == "SELECT count(*) FROM orders"


def test_streams_schema_sql_and_result(sample_db: Path) -> None:
    events = list(
        ask("How many orders?", sample_db, gateway("```sql\nSELECT count(*) AS n FROM orders\n```"))
    )
    assert steps(events) == ["schema", "sql", "result"]
    sql = events[1]
    assert sql["sql"] == "SELECT COUNT(*) AS n FROM orders"
    assert sql["model"] == MODEL
    result = events[2]
    assert result["columns"] == ["n"]
    assert result["rows"] == [[100]]
    assert result["truncated"] is False


def test_unsafe_model_sql_stops_at_the_guard(sample_db: Path) -> None:
    events = list(ask("Delete everything", sample_db, gateway("DROP TABLE orders")))
    assert steps(events) == ["schema", "error"]
    assert events[-1]["stage"] == "guard"
    assert "DROP" in events[-1]["message"]
    assert events[-1]["sql"] == "DROP TABLE orders"


def test_model_failure_is_reported(sample_db: Path) -> None:
    down = litellm.APIConnectionError("ollama is not running", llm_provider="ollama", model=MODEL)
    events = list(ask("How many orders?", sample_db, gateway(down)))
    assert steps(events) == ["schema", "error"]
    assert events[-1]["stage"] == "model"


def test_prompt_holds_the_schema_and_question_but_no_rows(
    sample_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[dict[str, str]]] = []
    real = Gateway.complete

    def spy(self: Gateway, messages: list[dict[str, str]], **kw: Any) -> Any:
        seen.append(messages)
        return real(self, messages, **kw)

    monkeypatch.setattr(Gateway, "complete", spy)
    list(ask("Total by customer?", sample_db, gateway("SELECT 1 FROM orders")))
    prompt = "\n".join(m["content"] for m in seen[0])
    assert "orders (100 rows)" in prompt and "customer_id INTEGER" in prompt
    assert "Total by customer?" in prompt
    assert "c7" not in prompt  # a customers.name value


def test_database_error_is_reported_with_the_sql(sample_db: Path) -> None:
    events = list(ask("?", sample_db, gateway("SELECT no_such_column FROM orders")))
    assert steps(events) == ["schema", "error"]
    assert events[-1]["stage"] == "run"
    assert "no_such_column" in events[-1]["message"]
