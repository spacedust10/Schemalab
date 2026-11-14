# Changelog

## Unreleased

- Data pipelines: four experiments in your lab compare the naive way with the right one. Change data capture (polling updated_at misses deletes and late commits; a trigger-written change log replays exactly), partitioning (one table reads all 120,000 rows to total a month and DELETE frees no space; monthly partitions read 10,000 and dropping one frees it), SCD Type 2 (overwriting a customer's state moves past sales; versioned rows keep them where they happened), and OLTP to star schema (the same answer from four tables instead of six).
- Transaction patterns: five experiments run real concurrent PostgreSQL connections in your lab, the naive way beside the safe one, with checks an outsider would notice. Ledger transfers (read-then-write creates or destroys money; FOR UPDATE in id order and UPDATE in place do not), idempotency keys (a retried payment charges once), optimistic locking with a version column (no lost edits), the transactional outbox with FOR UPDATE SKIP LOCKED relays (no lost or doubled events), and TPC-C New-Order (unique order numbers, exact stock).
- Tested Days 1-14 against real files and hostile inputs, and fixed what broke:
  - Ask: a model reply with an unclosed quote no longer ends the answer with nothing; parse errors read as plain text; a question over empty tables is answered once, saying they are empty.
  - SQL guard: table functions behind LATERAL are refused.
  - Uploads: Windows-1252 and UTF-16 CSV files load; ragged rows keep their columns; only reserved words get a leading underscore; errors no longer show server paths; MySQL integer types, SQL Server scripts and tables dropped and created again load correctly.
  - Design: quoted names with spaces, reserved table names (user, order) and tables created twice are read.
  - Normalize: decomposition no longer hangs on 14-16 attributes, each request has one work limit, and 2NF violations are reported once each.
  - Algebra: deeply nested expressions are refused with a message instead of a server error.
  - Lab: a slow statement is shown as running, not as waiting for a lock.
- Design: weak entities, identifying relationships and specialization (ISA) are read from SQL, written in the design text (weak entity, identifying relationship, isa Super: A, B disjoint total on kind) and mapped back to tables. Chen diagrams use Elmasri and Navathe's notation; crow's-foot diagrams use IDEF1X conventions. A foreign key covering a table's key is now 1:1 or a subtype, not 1:N, and relationship names no longer come from the first key column.
- Diagrams are laid out in layers (owners and supertypes above), with relationship names kept clear of boxes and of each other, and long rows wrapped.
- .sql files: tables nested in CREATE SCHEMA, CREATE DOMAIN types, schema-qualified names, columns added by ALTER TABLE and psql meta-commands are handled, so the AdventureWorks for Postgres script loads. When a file's rows live in CSV files it loads with \copy, the upload says the tables are empty and why.
- Up to 100 uploaded tables and design entities (was 50 and 40).

## 0.2.0 (2026-11-12)

The database lab: design, normalize, query, transact and measure.

- Benchmarks: generate TPC-H or TPC-DS data, load the same data into DuckDB and PostgreSQL, and run the standard queries on both. Each query is timed (median of three) and both engines' answers are checked against each other; at scale 0.01 all 121 queries agree. The page shows the speed ratio per query and the times. Scale 0.01 is open to everyone; larger scales need an operator account (SCHEMALAB_BENCH_OPERATORS).
- Plans: run EXPLAIN ANALYZE on your lab tables and see where the time goes (own time per node, actual vs estimated rows, misestimates flagged). An index advisor suggests CREATE INDEX statements with the evidence, and one click creates the index and compares the timings. Step-through B+ tree and extendible hashing animations.
- Design: one input takes design text, CREATE TABLE statements or a .sql file, including whole pg_dump and mysqldump files.
- Schedules and recovery: check a schedule's precedence graph, conflict and view serializability, recoverability classes, and whether basic, strict or rigorous 2PL could produce it (with locks written in); replay a write-ahead log through ARIES analysis, redo and undo with CLRs.
- Transaction lab: two real PostgreSQL sessions per user in a sandbox database, with live transaction states, who waits for whom, the lock table and row versions (xmin, xmax). Nine guided scenarios: non-repeatable read, REPEATABLE READ's snapshot, phantom read, lost update, SELECT ... FOR UPDATE, serialization failure, write skew, SERIALIZABLE and deadlock.
- Algebra: write relational algebra (σ π ρ γ τ ⋈ × ∪ − ∩, or words) or SQL, get the other form and the query tree, and run it on the sample data or your tables through the SQL guard.
- Constraint coach: on Your data, each table can list the constraints its data already satisfies (keys, foreign keys, NOT NULL, UNIQUE, CHECKs), with the evidence and ALTER TABLE statements.
- Normalize: give a relation and its functional dependencies, or start from an uploaded table, and get its keys, highest normal form with each violation explained, a BCNF decomposition with its steps, a 3NF synthesis, and chase proofs that both joins are lossless.
- Design: write an ER design as text, draft one from an English description, or read it from CREATE TABLE statements or your uploaded tables. See it in Chen or crow's-foot notation and get the tables in PostgreSQL, MySQL, SQLite, SQL Server or Oracle.
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
