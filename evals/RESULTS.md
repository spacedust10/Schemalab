# Eval results

Execution accuracy on the 54 questions in [questions.yaml](questions.yaml): an answer counts as
correct when its final result matches the reference query's result (row order and column names
ignored, numbers to 2 decimal places, a one-row reference compared with the top row of a
ranking). Run with `uv run python -m schemalab.evals`.

## 2026-11-09: does the critic help? (#24)

Model: `ollama_chat/llama3.1` (8B, local), temperature 0. Same questions, three agent versions.

| Agent version | Accuracy | Taxi (36) | Insurance (18) | Seconds per question |
|---|---|---|---|---|
| Critic rewrites on objection (before) | 61.1% (33) | 29 | 4 | 14.1 |
| No critic | 64.8% (35) | 31 | 4 | 7.3 |
| **Critic judges only (now)** | **64.8% (35)** | **31** | **4** | **9.3** |

What the critic's rewrites did, compared with no critic: they fixed no answer and broke two right
ones (t10, t36). In t10 the first query counted trips ending in Manhattan correctly; the critic
objected and the rewrite grouped by borough instead.

What its verdict is worth, as a confidence signal:

| Label | Rewrites on | Judge only |
|---|---|---|
| high (critic agreed on the first attempt) | 93% of 30 | 93% of 30 |
| medium | 33% of 3 | 100% of 1 |
| low (critic objected, or no rows) | 22% of 18 | 30% of 20 |

Judge only gets every question the same as no critic, right or wrong, and keeps the labels. Without a critic every answer is capped at medium (79.5% of 44 correct), so the labels say
nothing. Decision: keep the critic as a judge that sets the confidence, and stop it from
rewriting answers. An empty result is still retried, since that check needs no model.

## Where the analyst fails

Most misses are on the Michigan insurance table (4 of 18 correct), and nearly all have one
cause: the model never sees cell values, so it guesses category spellings. It wrote
`age = 'Under 65'` for `'Under 65 years'`, `sex = 'Male'` for `'Male Only'`, `'Both'` for
`'Both Sexes'`, `name = 'Kent County'` for `'Kent County, MI'` and `service_zone = 'Airport'`
for `'Airports'`. Each runs without error and returns nothing or the wrong rows.

The likely fix is showing the model the distinct values of low-cardinality text columns. That
sends data values to the model, which the analyst does not do today, so it should be a
per-dataset choice (on for the public sample data, off by default for uploads) and be measured
here before it ships.

Other misses: joining the insurance table to taxi zones on unrelated ids (m03, m04, m06, m07,
m08), counting distinct zones instead of trips (t09), and treating Sunday to Thursday as weekdays
(t35; DuckDB numbers days from Sunday as 0).

## 2026-11-09: category values (#30)

Same model and questions, critic as judge. "Values on" lists the distinct values of short text
columns (at most 100 values of at most 60 characters) in the schema the model sees, which the
app now does for the sample data.

| Agent version | Accuracy | Taxi (36) | Insurance (18) | Seconds per question |
|---|---|---|---|---|
| Values off | 64.8% (35) | 31 | 4 | 9.3 |
| **Values on (now, sample data)** | **75.9% (41)** | 28 | **13** | 12.3 |

Values fixed 12 answers (t09, t17, t20, m04 to m08, m10, m12, m13, m15): the model now writes
`'Under 65 years'`, `'Both Sexes'` and `'Airports'` as they are stored. They broke 6, all taxi
questions (t07, t21, t22, t27, t28, t32), where the longer prompt led llama3.1 into filters
nobody asked for, such as `payment_type IN (1, 2, 3)`; five of the six came back labelled low.

Labels stay informative: high was right 91% of the time (35 answers), low 50% (18).

Next to try: list values only for the tables a question is likely to need, which should keep
the insurance gains without lengthening every taxi prompt.
