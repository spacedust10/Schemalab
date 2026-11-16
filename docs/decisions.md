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
| 2026-11-15 | Garage, not MinIO, for the lake's object storage | MinIO's community edition is archived and ships no images; Garage has a single-node mode, versioned images and per-bucket keys |
| 2026-11-15 | dlt runs in its own `ingest` service on DuckDB 1.5; the API stays on 2.0 | dlt pins `duckdb<2`. The DuckLake catalog stays at format 1.0, which both read |
| 2026-11-15 | The API never holds lake credentials | Ingest publishes silver and gold to a DuckDB file that Ask opens with the locked engine; only ingest has the S3 key and the catalog password |
| 2026-11-15 | Lake models are checked twice: the sqlglot guard in the API, then DuckDB's own parse in ingest | DuckLake's catalog is reachable through `postgres_query` even after lockdown; both checks refuse table functions and other catalogs |
| 2026-11-15 | Lake secrets have no defaults in Compose | `scripts/lake-env.sh` writes random ones to `.env`; a committed default would be a working key |
| 2026-11-15 | Ingest reaches the internet only through a Squid proxy that allows HTTPS CONNECT to the USGS and Google Sheets hosts | The code's fixed hosts are one wall; the network is a second, against a bug or a compromised dependency. Ingest sits on internal networks only, and dlt's telemetry is off |
| _open_ | MetricFlow or Cube for the semantic layer | Decide when the data platform is built |
