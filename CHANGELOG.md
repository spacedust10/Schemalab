# Changelog

## Unreleased

- The model now sees the values of short category columns ('Under 65 years', 'Airports') for the sample data, and for uploaded tables when the user ticks a box. Accuracy on the eval rose from 64.8% to 75.9%; see evals/RESULTS.md.
- The category scan is bounded in memory and time, so a large upload cannot stall questions.

## 0.1.0 (2026-11-09)

The first release: a safe core and an AI data analyst on free, local models.

### Ask questions in plain English
- An analyst plans a query, writes DuckDB SQL and runs it through a two-layer safety guard, retrying up to three times when a query is rejected, fails or returns nothing. A critic then reviews the answer against the question and sets its confidence. Every step streams to the page as it happens.
- Answers lead with a computed one-line finding and a chart (bars from zero, largest first), followed by the full table, a confidence label with its reasons, and provenance: tables read, attempts, models and the final SQL.
- The model sees table and column names, types and keys, never rows, unless results sharing is switched on.
- Models run on Ollama (llama3.1) by default, with Groq and Gemini free tiers as fallbacks when their keys are set. Sampling is at temperature 0.
- Users can teach the analyst facts ("fiscal year starts in July"), which go into their own prompts.

### Keep SQL safe
- A sqlglot guard allows one read-only SELECT over known tables, with no table functions, files, extensions or settings, and correct CTE scoping.
- The DuckDB connection is read-only with external access off, no extension loading and a locked configuration, plus a row cap and a timeout.

### Use your own data
- Upload .csv and .xlsx files (one table per sheet) or a .sql dump from pg_dump, mysqldump or SQLite. Dumps keep their tables, rows and primary and foreign keys, and nothing in them is run.
- Every table gets a column profile; dumps get an overview of their relationships. Tables are private to their owner.

### Take answers further
- Export any answer as a Jupyter or marimo notebook that re-runs its SQL.
- Pin answers to a dashboard that re-runs each query through the guard when it opens.

### Accounts and security
- Accounts with Argon2id passwords, httpOnly session cookies and login throttling per email and address.
- Cross-site requests are refused on every state-changing route.
- Compose publishes ports on 127.0.0.1 only, and Postgres not at all.

### Measured
- An eval harness scores the analyst on 54 questions with reference queries (execution accuracy) and reports accuracy per confidence label. Its first use settled how the critic works: its rewrites fixed no answer and broke two, while its verdict separated right from wrong answers well, so it now judges and does not rewrite. Results are in evals/RESULTS.md.
