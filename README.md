# Schemalab

An open-source lab for learning relational databases, with an AI data analyst that runs on free, local models.

Students, professors and TAs use it to see what a database course usually only describes: a lost update between two real PostgreSQL sessions, a schema decomposed into BCNF step by step, a query plan with real timings. The analyst answers plain-English questions about your data, and every query it writes passes a safety guard before it touches the data.

[![CI](https://github.com/spacedust10/Schemalab/actions/workflows/ci.yml/badge.svg)](https://github.com/spacedust10/Schemalab/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)

> **Status: early development (v0.1 in progress).** The analyst API works end to end: accounts, the plan, SQL, critic and retry loop, the SQL safety layer, history and correction notes. The UI and the lab modules are being built; see [Roadmap](#roadmap).

## What it will do

**Learning lab**
- ER designer: describe a schema in English or draw it, then get Chen and crow's-foot diagrams and DDL for PostgreSQL, MySQL, SQL Server, Oracle and SQLite.
- Normalization: FD closure, candidate keys, 1NF to BCNF checks, and decompositions with lossless-join and dependency-preservation proofs. Computed exactly; the model only explains.
- Transaction lab: two live PostgreSQL sessions side by side. Switch the isolation level and watch lost updates, phantoms and write skew appear or disappear, with locks and row versions shown live.
- Schedule checker, recovery simulator, query-plan viewer, and benchmarks of DuckDB against PostgreSQL on TPC-H data.
- Teaching tools: assignments with hidden test databases, an auto-grader with partial credit, a hints-only tutor mode and an exam mode with AI off.

**AI data analyst**
- Ask a question in plain English and get an answer, a chart, the exact SQL, the tables used and a confidence label.
- Only the schema is sent to the model, never your rows, unless you opt in.
- Runs on Ollama locally, with free cloud tiers as a fallback. No paid API keys.

## How model-written SQL is kept safe

Model output is treated as untrusted input. A query reaches the data only through `run_query`, which applies two independent layers:

1. **Parser guard** ([`guards/sql.py`](src/schemalab/guards/sql.py)). The SQL is parsed with sqlglot and must be exactly one `SELECT` (or `UNION` and similar), with no write, DDL or session command anywhere in the tree, no table functions or file paths in `FROM`, no host-reading functions, and only allow-listed tables. CTE names count only inside the query that defines them. The guard returns normalized SQL, and that is what runs, so the text that was checked is the text that executes.
2. **Locked engine** ([`guards/engine.py`](src/schemalab/guards/engine.py)). The DuckDB connection is read-only, has external access off (no files, no network), cannot load extensions, and locks its configuration so a query cannot undo any of this.

Each query also gets a row cap and a time budget, after which it is interrupted. The tests run about 60 attack queries against the guard and run raw SQL against the engine with no guard in front, so each layer is proven on its own. Each rule was disabled in turn to confirm that a test fails without it.

## Quick start

Requirements: Docker, and [Ollama](https://ollama.com) for local models.

```bash
git clone https://github.com/spacedust10/Schemalab
cd Schemalab
cp .env.example .env
docker compose up
```

- Web UI: http://localhost:3000
- API docs: http://localhost:8000/docs

If ports 3000, 8000 or 5432 are already in use, set `WEB_PORT`, `API_PORT` or `POSTGRES_PORT` in `.env`. All ports are published on 127.0.0.1 only.

### Sample data

```bash
uv run python -m schemalab.datasets
```

This downloads the sample datasets, checks each file against a pinned SHA-256, and builds `data/samples/schemalab.duckdb`. Data files are never committed to the repo.

### Try the analyst

With the stack running, Ollama up (`ollama pull llama3.1`) and the sample data built:

```bash
uv run python scripts/demo.py "What is the average fare by pickup borough?"
```

This signs in a local demo account and prints each step as it streams: the plan, the SQL for each attempt, the rows, the critic's verdict, and the answer with its confidence and provenance. The interactive API docs at http://localhost:8000/docs cover every endpoint; use **Authorize** with the token from `/auth/register` or `/auth/login`.

## Development

Python 3.12 with [uv](https://docs.astral.sh/uv/); Node 22 for the web app.

```bash
uv sync
uv run pre-commit install
uv run pytest
```

```bash
cd apps/web && npm ci && npm run dev
```

Every change is made test-first. CI runs ruff, strict mypy, pytest, `npm audit`, ESLint with zero warnings allowed, a type check, a production build, and a smoke test of the full Compose stack. Dependabot watches every lockfile, and CI actions are pinned to commit SHAs.

## Project layout

```
apps/web/              Next.js UI
src/schemalab/
  api/                 FastAPI app
  guards/              SQL guard, locked engine, guarded execution
  datasets.py          sample dataset loader
tests/
docs/
  design_principles.md rules every chart and view follows
  decisions.md         technical decisions and their reasons
```

## Roadmap

| Release | Scope |
|---|---|
| v0.1 | SQL safety layer, model gateway, analyst agent with retries, provenance, eval harness, first UI |
| v0.2 | ER designer, normalization engine, transaction lab, schedule checker, recovery simulator, query plans, TPC-H benchmarks |
| v0.3 | Enterprise transaction patterns, teaching tools, ingestion, dbt models, data quality gates, lineage |
| v1.0 | Statistics and ML toolkit, Python sandbox, MCP server, PII masking, tracing, hosted demo |

## License

[Apache-2.0](LICENSE)
