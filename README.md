# Schemalab

> An open-source **database learning lab and AI data analyst** for students, professors and TAs. It runs on **free, local LLMs** and covers relational design from ER diagram to DDL, transactions and concurrency, and analytics on GB-scale data in seconds.

**Author:** Vinit Gunaki ([github.com/spacedust10](https://github.com/spacedust10))
**Status:** Week 1 of 4 (v0.1 safe core + agent). Day 1 done: tooling, API and web shells, Compose, CI.
**Last updated:** 2026-11-07

---

## 0. Read this first (context for anyone, human or AI, picking this up)

This file is the **single source of truth** for what we are building and why. Read it before you write code, and update it when a decision changes.

### Why this project exists
- **Goal:** a portfolio project that makes my profile strong for **Data Scientist**, **Data Engineer** and **AI/ML Engineer** roles.
- **Not the goal:** revenue, pricing or a paid SaaS. Optimize for **real, useful features** and **visible engineering quality**.
- **Product audience:** students, professors and TAs who learn or teach relational databases and SQL. The product is a reference and practice tool for them (decided 2026-11-07).
- **Problem we solve:**
  - Database courses teach concurrency, normalization and query plans with static slides, so students never see them happen.
  - TAs grade SQL by hand.
  - Students jump between separate tools for ER diagrams, SQL practice and query plans.
  - Classes rarely show the transaction patterns that large companies use.
- **Showcase audience:** recruiters and hiring managers. They spend about 2 minutes on a repo, so the README, the demo video, the architecture diagram and the evaluation score table must carry the story.

### Hard constraints
| Constraint | Rule |
|---|---|
| Models | **Free models only.** Local through Ollama (Qwen3, gpt-oss-20b, Llama), plus free cloud tiers (Groq free tier `openai/gpt-oss-120b`, Google AI Studio free tier) for the hosted demo. No paid API keys required to run. |
| Cost | Every component must be free and open source or self-hostable. |
| Reproducibility | `docker compose up` must start the full stack on a laptop. |
| Safety | AI-written SQL or Python **never** runs unchecked. |
| Originality | **Clean-room rebuild.** The original repo has **no license**, so no code is copied from it. It is a reference for ideas only. |

### Origin
- **Inspired by:** [HamnaCh456/Data_Analyst_Agent](https://github.com/HamnaCh456/Data_Analyst_Agent), a 200-line Streamlit text-to-SQL demo.
- **What it did:** the user uploads a CSV or Excel file, the app sends the schema to Groq `llama-3.3-70b-versatile`, the model returns SQL, and DuckDB runs it.
- **Why we rebuild:**
  - The model was shut down by Groq on 2026-08-16.
  - The SQL ran unchecked in DuckDB, so a query could read server files.
  - There was no agent loop, no charts, no tests and no license.
- **Idea we keep:** send **only the schema and metadata** to the LLM, never raw rows, unless the user opts in.

---

## 1. What the product does (user view)

1. The user connects data: upload CSV, Excel or Parquet, or connect Postgres, Google Sheets or a public API.
2. The pipeline ingests, cleans, tests and models the data, then registers it in a catalog with metric definitions.
3. The user asks a question in plain English, for example "Why did revenue drop in March?"
4. The agent plans the work, writes SQL or Python, runs it in a sandbox, checks the result and retries if needed.
5. The user gets:
   - an answer in plain words,
   - a chart,
   - the exact SQL and code used (provenance),
   - the tables and metrics used (lineage),
   - a confidence label,
   - an export to a notebook.
6. The user can pin answers to a dashboard, schedule them, or set alerts ("tell me if signups drop 20%").

---

## 2. Architecture

```
            ┌─────────────────────────── Frontend (Next.js or Streamlit) ───────────────────────────┐
            │  Chat · streamed agent steps · Plotly/Vega-Lite charts · dashboards · notebook export │
            └──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                       │ REST + SSE/WebSocket
            ┌──────────────────────────────────────────▼─────────────────────────────────────────────┐
            │                                FastAPI backend                                         │
            │  auth · sessions · history · job queue · alerts                                        │
            └───────┬──────────────────────────────┬──────────────────────────────┬──────────────────┘
                    │                              │                              │
   ┌────────────────▼───────────┐   ┌──────────────▼──────────────┐   ┌───────────▼──────────────┐
   │   AGENT (LangGraph)        │   │   DS TOOLKIT                │   │   OBSERVABILITY          │
   │ planner → SQL/Python step  │──▶│ profiling · stat tests      │   │ Langfuse (self-hosted)   │
   │ → critic → retry → answer  │   │ A/B · forecasting · anomaly │   │ OpenTelemetry · logs     │
   │ memory · catalog retrieval │   │ AutoML+SHAP · causal · MLflow│  └──────────────────────────┘
   └───────┬────────────────────┘   └─────────────────────────────┘
           │ LiteLLM (model gateway: Ollama / Groq free / AI Studio free)
           │ sqlglot guard (read-only SQL) · gVisor Docker sandbox (Python)
   ┌───────▼────────────────────────────────────────────────────────────────────────────────────┐
   │   DATA PLATFORM                                                                            │
   │ dlt ingestion → MinIO object storage → Iceberg / DuckLake tables (bronze → silver → gold)  │
   │ dbt-duckdb models + tests · MetricFlow/Cube semantic layer · Great Expectations/Soda/Pandera│
   │ Dagster orchestration · OpenLineage · schema-drift detection · (optional) Redpanda stream   │
   │ Query engines: DuckDB (read-only) · Polars                                                 │
   └────────────────────────────────────────────────────────────────────────────────────────────┘
   MCP servers expose the data tools so Claude Desktop and other agents can use the platform.
```

---

## 3. Tech stack

| Layer | Tools | Why |
|---|---|---|
| LLMs | Ollama (Qwen3, gpt-oss-20b, Llama), Groq free tier, Google AI Studio free tier | Free, swappable, private |
| Model gateway | LiteLLM | One interface, fallbacks, token logs |
| Agent | LangGraph | Stateful graph, retries, human-in-the-loop |
| SQL safety | sqlglot (parse, allow-list), DuckDB read-only, external access off | Block writes and file reads |
| Python sandbox | Docker + gVisor, no network, CPU/time/memory limits | Run untrusted code safely. **Not** Pyodide alone |
| Retrieval | pgvector, LanceDB or Qdrant | Fetch only the relevant schema and metric docs |
| Ingestion | dlt | Incremental loads, schema inference |
| Storage | MinIO + Apache Iceberg or DuckLake | Open lakehouse table formats |
| Transform | dbt-duckdb | Tested, documented models |
| Semantic layer | dbt MetricFlow or Cube | Consistent metric definitions, no guessed metrics |
| Orchestration | Dagster | Asset graph, schedules, lineage view |
| Data quality | Great Expectations, Soda or Pandera | Block bad data before the agent sees it |
| Lineage | OpenLineage | Each answer can show its source tables |
| Streaming (optional) | Redpanda + Polars or DuckDB | Real-time demo |
| DS toolkit | Polars, SciPy, statsmodels, StatsForecast / Chronos / TimesFM, PyOD, FLAML / AutoGluon, SHAP, DoWhy, scikit-learn, HDBSCAN | Real analysis, not just SQL |
| ML ops | MLflow, Evidently | Experiment tracking, drift |
| PII | Microsoft Presidio | Mask names and emails before they reach an LLM |
| Observability | Langfuse (self-hosted), OpenTelemetry | Trace steps, latency, tokens |
| Backend | FastAPI, Postgres | API, auth, history |
| Frontend | Next.js (or Streamlit for speed), Plotly / Vega-Lite | Charts, streamed steps |
| Notebooks | Jupyter (nbformat), marimo | Reproducible exports |
| DevOps | Docker Compose, GitHub Actions, pre-commit, ruff, mypy, pytest, uv | Clean, tested, typed |
| Hosting | Hugging Face Spaces (free demo) | Public link for recruiters |

> Tool picks are defaults, not laws. If you swap one, record the reason in **§9 Decision log**.

---

## 4. Feature map by role

### Data Engineer features
- [ ] dlt ingestion from CSV, Excel, Parquet, Postgres, Google Sheets, public APIs
- [ ] Bronze / silver / gold lakehouse on Iceberg or DuckLake + MinIO
- [ ] dbt models, tests and docs
- [ ] Semantic layer (MetricFlow or Cube) used by the agent
- [ ] Dagster asset graph with schedules
- [ ] Data quality gates that fail the pipeline on bad data
- [ ] Schema drift detection and alerts
- [ ] OpenLineage lineage shown next to each answer
- [ ] SQL guard: sqlglot allow-list, read-only DuckDB, no external file access
- [ ] (Optional) Redpanda streaming source

### Data Scientist features
- [ ] Automatic profiling on upload (distributions, nulls, outliers, correlations)
- [ ] Statistical test advisor (picks the test, checks assumptions, reports effect size)
- [ ] A/B test analyzer (lift, CI, power, sample ratio mismatch)
- [ ] Forecasting with prediction intervals (StatsForecast, Chronos or TimesFM)
- [ ] Anomaly detection with explanations (PyOD, Isolation Forest)
- [ ] "Predict X" → AutoML (FLAML or AutoGluon) + SHAP explanations
- [ ] Causal questions with DoWhy, with assumptions stated
- [ ] Customer segmentation (K-means, HDBSCAN) with segment descriptions
- [ ] MLflow tracking of every model run
- [ ] Evidently drift reports

### AI Engineer features
- [ ] LangGraph agent: planner → executor (SQL/Python) → critic → retry
- [ ] gVisor Docker sandbox for Python
- [ ] Retrieval over the data catalog and metric definitions
- [ ] Memory of user corrections ("fiscal year starts in July")
- [ ] Answer provenance: SQL, code, tables, confidence label
- [ ] MCP servers for the data tools
- [ ] Presidio PII masking
- [ ] Langfuse tracing
- [ ] **Evaluation harness** (see §6) running in CI

### Learning Lab features (students, professors, TAs)
**Design**
- [ ] ER designer: draw an ER diagram, or describe it in English, and get Chen and crow's-foot views
- [ ] ER → DDL for PostgreSQL, MySQL, SQL Server, Oracle and SQLite (dialect transpile with sqlglot); DDL → ER (reverse)
- [ ] Constraint coach: keys, FKs, CHECK, cascade rules, weak entities, M:N bridge tables
- [ ] Normalization engine (algorithmic, not LLM): FD closure, candidate keys, 1NF/2NF/3NF/BCNF check, decomposition with lossless-join and dependency-preservation proofs

**Query**
- [ ] Relational algebra ↔ SQL translator with query-tree view
- [ ] SQL error explainer (explains the error and gives a hint, not the answer)
- [ ] Query plan visualizer (`EXPLAIN ANALYZE`), index advisor, B-tree and hash index animations

**Transactions and concurrency**
- [ ] Transaction lab: run 2+ sessions side by side on real PostgreSQL
- [ ] Show dirty read, non-repeatable read, phantom, lost update and write skew under each isolation level
- [ ] Live view of locks, MVCC row versions and deadlock detection
- [ ] Schedule checker: precedence graph, conflict/view serializability, 2PL and strict 2PL
- [ ] Recovery simulator: WAL, checkpoints, ARIES-style redo/undo

**Enterprise patterns** (industry-standard OLTP designs)
- [ ] Bank transfer with a double-entry ledger and `SELECT ... FOR UPDATE`
- [ ] Order-to-cash and inventory reservation (TPC-C-style warehouse orders)
- [ ] Idempotency keys, optimistic locking, outbox pattern, CDC
- [ ] Partitioning, SCD Type 2, OLTP schema → star schema

**Scale**
- [ ] Generate TPC-H / TPC-DS data at scale factors 1–100 with DuckDB
- [ ] Run analytics on GB-scale data in seconds (DuckDB columnar, Polars) and compare with a PostgreSQL row store
- [ ] Benchmark page with timings, hardware notes and plan comparisons

**Teaching tools**
- [ ] Assignment builder with hidden test databases
- [ ] Auto-grader that compares result sets (reuses the §6 eval harness), with partial credit and a rubric
- [ ] Socratic tutor mode (hints only) and exam mode (AI off)
- [ ] Per-student sandbox (in-browser DuckDB-WASM, or ephemeral PostgreSQL schemas)
- [ ] Professor dashboard: which concepts students fail most (learning analytics, knowledge tracing)
- [ ] Export grades and assignments to an LMS (Canvas)

### Product features
- [ ] Streamed agent steps in the UI
- [ ] Interactive charts
- [ ] Pin-to-dashboard + scheduled refresh
- [ ] Natural-language alerts
- [ ] Notebook export (Jupyter, marimo)
- [ ] Auth + per-user history
- [ ] Hugging Face Spaces demo

---

## 5. Roadmap

### Phase 0: Safe core
**Exit test:** ask 10 questions on the sample dataset. Every query is guarded, logged and answered.
- [ ] Repo scaffold (see §7), uv, ruff, mypy, pytest, pre-commit, MIT or Apache-2.0 license
- [ ] LiteLLM + Ollama, Groq free tier as fallback
- [ ] DuckDB read-only + sqlglot guard + unit tests for blocked queries
- [ ] Minimal FastAPI endpoint `/ask` and a simple UI
- [ ] Sample datasets: NYC Taxi, Olist e-commerce, one Michigan open dataset

### Phase 0.5: Learning Lab core
**Exit test:** a student can go from an English description to an ER diagram, valid PostgreSQL DDL and a BCNF check, and can watch a lost update happen in the transaction lab.
- [ ] ER designer + ER ↔ DDL (sqlglot dialects)
- [ ] Normalization engine with unit tests from textbook examples
- [ ] Transaction lab v1 (two sessions, isolation levels, anomalies)
- [ ] TPC-H generator + DuckDB vs PostgreSQL benchmark page

### Phase 1: Agent + evaluation
**Exit test:** the eval suite scores ≥ 80% execution accuracy and runs in CI.
- [ ] LangGraph planner / executor / critic loop with retry on error or empty result
- [ ] Profiling on upload
- [ ] Charts + provenance panel
- [ ] Eval harness v1 (50+ questions)

### Phase 2: Data platform
**Exit test:** `dagster dev` shows the full asset graph from raw files to gold tables. A failing quality check blocks the load.
- [ ] dlt → MinIO → Iceberg/DuckLake
- [ ] dbt models + tests
- [ ] Semantic layer wired into the agent
- [ ] Data quality gates, schema drift, OpenLineage

### Phase 3: Data science toolkit
**Exit test:** stat tests, forecast and AutoML outputs match reference results from SciPy / statsmodels on test fixtures.
- [ ] Stat test advisor, A/B analyzer
- [ ] Forecasting, anomaly detection
- [ ] AutoML + SHAP, MLflow
- [ ] Causal (DoWhy), segmentation, Evidently

### Phase 4: Advanced agent + product
- [ ] gVisor Python sandbox
- [ ] Catalog retrieval, correction memory
- [ ] MCP servers
- [ ] Presidio, Langfuse
- [ ] Dashboards, schedules, alerts, notebook export, auth

### Phase 5: Showcase
- [ ] Architecture diagram (PNG/SVG) in the README
- [ ] 2-minute demo video
- [ ] Public demo on Hugging Face Spaces
- [ ] Blog post: "How I raised text-to-SQL accuracy from X% to Y% with free models"
- [ ] Resume bullets updated with real numbers (see §10)

---

## 6. Evaluation (the proof that it works)

- **Golden set:** `evals/questions.yaml` holds 50–100 questions, each with a reference SQL or a reference answer, a difficulty level and tags (join, aggregation, time, stats).
- **Metric:** execution accuracy, meaning the result set matches the reference (order-insensitive, with numeric tolerance).
- **Matrix:** run every model (local and free cloud) × every agent version. Store the results in `evals/results/`.
- **CI:** GitHub Actions runs a fast subset on each pull request and the full set nightly or on demand.
- **README table:** show the latest score per model. Do not publish public benchmark scores we did not run ourselves.

---

## 7. Repo layout

```
.
├── README.md                 ← this file
├── docker-compose.yml        core stack; later profiles: platform, ml, obs
├── pyproject.toml            uv project (Python 3.12)
├── apps/
│   └── web/                  Next.js UI
├── src/schemalab/
│   ├── api/                  FastAPI (auth, /ask, history, alerts)
│   ├── agent/                LangGraph graph, nodes, memory, retrieval
│   ├── guards/               sqlglot guard, sandbox client, PII masking
│   ├── lab/                  ER, normalization, transactions, schedules, recovery
│   ├── ds_toolkit/           profiling, stats, ab_test, forecast, anomaly, automl, causal
│   └── data_platform/        dlt, dbt, Dagster, quality checks
├── mcp_servers/
├── sandbox/                  Python execution image
├── evals/                    questions.yaml, runner, results/
├── data/samples/
├── docs/                     design_principles.md, architecture.md, images/
└── tests/
```

Folders appear when the code that needs them lands.

---

## 8. Working rules

1. **Security first.** Every new tool that runs model output needs a guard and a test that proves the guard blocks bad input.
2. **Measure before claiming.** A change to the prompts, the agent or the model must be backed by an eval run. Record the score in the pull request.
3. **Schema, not rows.** Send metadata to LLMs by default. Sending sample rows is opt-in and passes through Presidio first.
4. **Free stack only.** No dependency may require a paid key to run locally.
5. **Small pull requests**, conventional commits, tests for every module.
6. **Update this README** when scope, stack or phase status changes.
7. **Honest numbers.** Resume bullets and the README use only measured results.

---

## 9. Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-11-07 | Clean-room rebuild, not a fork | Original repo has no license; dead model; unsafe SQL execution |
| 2026-11-07 | Free and local models only | Portfolio goal; no paid keys; privacy story |
| 2026-11-07 | Target DS + DE + AI engineer roles | Job search covers Data Scientist and Data Engineer roles |
| 2026-11-07 | DuckDB + sqlglot guard as the core | Fast local analytics; parse-level safety |
| 2026-11-07 | LangGraph for the agent | Stateful retries and human-in-the-loop |
| 2026-11-07 | Product audience = students, professors, TAs | Clear user group I can reach on campus; strong teaching use case |
| 2026-11-07 | Add the Learning Lab (ER→DDL, normalization, transactions, enterprise patterns, scale) | Covers relational database concepts end to end; shows DE depth |
| 2026-11-07 | Normalization and serializability checks are algorithmic; the LLM only explains | Exact answers for teaching; no hallucinated grades |
| 2026-11-07 | Name: Schemalab | Says what it is: a lab for schemas and the data in them |
| 2026-11-07 | Apache-2.0 license | Explicit patent grant |
| 2026-11-07 | Next.js, not Streamlit | Transaction lab and bespoke views need a custom UI; Streamlit looks generic |
| 2026-11-07 | Build all features in 4 weeks, one release per week (v0.1 → v1.0) | Usage-limited build; weekly releases keep the repo working at every point |
| 2026-11-07 | DuckLake, not Iceberg | Same ACID and time-travel features on MinIO, far less setup |
| 2026-11-07 | Pandera for data quality | Fails the load on bad data with less config than Great Expectations |
| 2026-11-07 | OpenLineage events to a file, drawn in our UI | Same lineage data without running Marquez |
| 2026-11-07 | LanceDB, StatsForecast, FLAML | Embedded or light options already listed in §3 |
| 2026-11-07 | gVisor in Linux CI; locked-down Docker on macOS | gVisor does not run on macOS Docker Desktop |
| 2026-11-07 | One `src/schemalab` package, not top-level `agent/`, `platform/` | A top-level `platform` package shadows the stdlib module |
| _next_ | Choose MetricFlow vs Cube | Decide in week 3 |

---

## 10. Resume bullets (fill with real numbers)

- **Data Engineer:** Built a lakehouse pipeline (dlt, dbt, Iceberg/DuckLake, Dagster) with data quality gates that blocked __% of bad loads and lineage for every AI answer.
- **Data Scientist:** Added forecasting, A/B testing, causal analysis and AutoML with SHAP explanations to an AI analyst; validated statistical outputs against SciPy/statsmodels.
- **AI Engineer:** Designed a LangGraph agent on free local LLMs with a sandboxed executor and CI evaluation harness, raising execution accuracy from __% to __% on __ questions.

---

## 11. Quick start

```bash
git clone https://github.com/spacedust10/Schemalab
cd Schemalab
cp .env.example .env          # optional: free Groq / AI Studio keys for cloud fallback
ollama pull qwen3             # or gpt-oss:20b
docker compose up
# UI:       http://localhost:3000
# API docs: http://localhost:8000/docs
```

---

## 12. Open questions
- Which sample datasets tell the best story? (NYC Taxi for scale, Olist for business metrics?)
- Which Ollama model gives the best accuracy-to-speed tradeoff on a laptop? Answer this with the eval harness in Phase 1.
- How much of the streaming layer is worth it for the demo?

## License
Apache-2.0. See [LICENSE](LICENSE).
