"""Eval harness: how often the analyst's answer matches a reference query's result.

    uv run python -m schemalab.evals                     # all cases, as the app runs them
    uv run python -m schemalab.evals --critic both --categories off --limit 10
    SCHEMALAB_MODELS='["groq/openai/gpt-oss-120b"]' uv run python -m schemalab.evals

Scoring is execution accuracy: run the reference SQL and the agent's final query and compare
results, ignoring row order and column names, with numbers rounded to 2 decimal places. Records
go to evals/results/ as JSON lines; the summary also gives accuracy per confidence label, which
shows whether the labels mean anything.
"""

import argparse
import json
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from schemalab.agent.graph import run_agent
from schemalab.config import Settings
from schemalab.guards.run import run_query
from schemalab.llm import Gateway, gateway_from_settings

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "evals" / "questions.yaml"


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    sql: str
    tags: tuple[str, ...] = ()


def load_cases(path: Path = QUESTIONS) -> list[Case]:
    raw = yaml.safe_load(path.read_text())
    return [Case(c["id"], c["question"], c["sql"], tuple(c.get("tags", []))) for c in raw]


def _norm(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        return round(float(value), 2)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value).strip()


def _key(value: Any) -> str:
    return repr(value)  # mixed types sort together safely


def same_result(expected: Sequence[Sequence[Any]], got: Sequence[Sequence[Any]]) -> bool:
    """Same rows as multisets, matching each reference column to one of the agent's columns."""
    want = [tuple(_norm(v) for v in row) for row in expected]
    have = [tuple(_norm(v) for v in row) for row in got]
    if len(want) == 1 and len(have) > 1:
        have = have[:1]  # "which X has the most": a full ranking counts if its top row matches
    if len(want) != len(have):
        return False
    if not want:
        return True
    have_columns = list(zip(*have, strict=True))
    used: list[int] = []
    for column in zip(*want, strict=True):
        target = sorted(column, key=_key)
        match = next(
            (
                i
                for i, c in enumerate(have_columns)
                if i not in used and sorted(c, key=_key) == target
            ),
            None,
        )
        if match is None:
            return False
        used.append(match)
    return Counter(want) == Counter(tuple(row[i] for i in used) for row in have)


def run_case(
    case: Case, db: Path, gateway: Gateway, critic: bool = True, categories: bool = False
) -> dict[str, Any]:
    start = time.perf_counter()
    events = list(run_agent(case.question, db, gateway, critic=critic, categories=categories))
    seconds = round(time.perf_counter() - start, 1)
    final = events[-1] if events else {}
    rows = next((e["rows"] for e in reversed(events) if e["step"] == "result"), None)
    answered = final.get("step") == "answer"
    expected = run_query(db, case.sql, max_rows=100_000).rows
    return {
        "id": case.id,
        "correct": answered and rows is not None and same_result(expected, rows),
        "label": final["confidence"]["label"] if answered else None,
        "attempts": final["provenance"]["attempts"] if answered else final.get("attempts"),
        "error": None if answered else final.get("stage"),
        "sql": final["provenance"]["sql"] if answered else final.get("sql"),
        "seconds": seconds,
    }


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def accuracy(rs: Sequence[dict[str, Any]]) -> float:
        return round(sum(r["correct"] for r in rs) / len(rs), 3) if rs else 0.0

    labels: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        labels.setdefault(r["label"] or "none", []).append(r)
    return {
        "n": len(records),
        "accuracy": accuracy(records),
        "errors": sum(1 for r in records if r["error"]),
        "by_label": {k: {"n": len(v), "accuracy": accuracy(v)} for k, v in labels.items()},
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--critic", choices=["on", "off", "both"], default="on")
    parser.add_argument("--categories", choices=["on", "off"], default="on")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "results")
    args = parser.parse_args(argv)

    settings = Settings()
    gateway = gateway_from_settings(settings)
    db = Path(settings.db_path)
    cases = [c for c in load_cases() if not args.ids or c.id in args.ids][: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    models = "-".join(gateway.models).replace("/", "_")
    for variant in ["on", "off"] if args.critic == "both" else [args.critic]:
        path = args.out / f"{stamp}-{models}-critic-{variant}-categories-{args.categories}.jsonl"
        records = []
        with path.open("w") as f:
            for case in cases:
                record = run_case(
                    case, db, gateway, critic=variant == "on", categories=args.categories == "on"
                )
                records.append(record)
                f.write(json.dumps(record) + "\n")
                f.flush()
                mark = "ok  " if record["correct"] else "MISS"
                outcome = record["label"] or record["error"]
                print(f"[critic {variant}] {mark} {case.id:<6} {outcome}", flush=True)
        info = {"critic": variant, "categories": args.categories, "models": gateway.models}
        print(json.dumps({**info, **summarize(records)}), flush=True)


if __name__ == "__main__":
    main()
