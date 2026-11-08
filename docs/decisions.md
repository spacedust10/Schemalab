# Decision log

Technical choices and why they were made. When a decision changes, add a new row rather than editing the old one.

| Date | Decision | Reason |
|---|---|---|
| 2026-11-07 | Clean-room build; no code taken from earlier text-to-SQL demos | Those projects had no license and ran model SQL unchecked |
| 2026-11-07 | Free and local models only (Ollama, plus free tiers of Groq and Google AI Studio) | Runs with no paid keys; data can stay on the machine |
| 2026-11-07 | DuckDB plus a sqlglot guard at the core | Fast local analytics; safety checked on the parse tree, not with string matching |
| 2026-11-07 | LangGraph for the agent | Stateful retries and human-in-the-loop steps |
| 2026-11-07 | Normalization and serializability checks are algorithmic; the LLM only explains them | Exact answers for teaching; no invented grades |
| 2026-11-07 | Apache-2.0 license | Explicit patent grant |
| 2026-11-07 | Next.js, not Streamlit | The transaction lab and the custom views need full control of the UI |
| 2026-11-07 | DuckLake, not Iceberg | Same ACID and time-travel features on MinIO with much less setup |
| 2026-11-07 | Pandera for data quality | Fails a load on bad data with less config than Great Expectations |
| 2026-11-07 | OpenLineage events written to a file and drawn in the UI | Same lineage data without running Marquez |
| 2026-11-07 | LanceDB, StatsForecast, FLAML | Embedded or lightweight options with the needed features |
| 2026-11-07 | gVisor sandbox in Linux CI; locked-down Docker on macOS | gVisor does not run on macOS Docker Desktop |
| 2026-11-07 | One `src/schemalab` package | A top-level `platform` package would shadow the stdlib module |
| 2026-11-08 | Two-layer SQL safety: parser guard, then a locked read-only DuckDB connection | Either layer alone has gaps; each layer has tests that run without the other |
| 2026-11-08 | Run the guard's normalized SQL, never the raw model text | What was checked is exactly what runs |
| 2026-11-08 | Sample data is downloaded and checksummed, never committed | Keeps the repo small and respects each dataset's license |
| 2026-11-08 | Test-first development for every change, config included | The first review found problems no check had caught |
| _open_ | MetricFlow or Cube for the semantic layer | Decide when the data platform is built |
