/**
 * Export an answer as a notebook that re-runs its SQL: Jupyter (.ipynb) or marimo (.py).
 * Text from the model or the user goes into the code only as JSON string literals, which are
 * also valid Python string literals, so it can never become code.
 */

export type Export = {
  question: string;
  headline: string;
  confidence: string;
  sql: string;
  dataset: "sample" | "mine";
};

const SAMPLE_DB = "data/samples/schemalab.duckdb";
const py = (text: string) => JSON.stringify(text);

function intro(e: Export): string {
  const where =
    e.dataset === "sample"
      ? `Run this from the Schemalab folder; it reads \`${SAMPLE_DB}\`.`
      : "This answer used your own tables, which live in your Schemalab account. Point `DB` at a DuckDB or database file with the same tables.";
  return [
    `# ${e.question}`,
    "",
    `**Answer:** ${e.headline}`,
    "",
    `Confidence: ${e.confidence}. Exported from Schemalab. ${where}`,
  ].join("\n");
}

function code(e: Export): string[] {
  const db = e.dataset === "sample" ? SAMPLE_DB : "your.duckdb";
  return [
    `import duckdb\n\nDB = ${py(db)}\ncon = duckdb.connect(DB, read_only=True)`,
    `SQL = ${py(e.sql)}\ndf = con.execute(SQL).df()\ndf`,
  ];
}

const lines = (text: string) => text.split(/(?<=\n)/);

export function toIpynb(e: Export): string {
  const cells = [
    { cell_type: "markdown", metadata: {}, source: lines(intro(e)) },
    ...code(e).map((src) => ({
      cell_type: "code",
      metadata: {},
      execution_count: null,
      outputs: [],
      source: lines(src),
    })),
  ];
  const notebook = {
    nbformat: 4,
    nbformat_minor: 5,
    metadata: {
      kernelspec: { name: "python3", display_name: "Python 3", language: "python" },
      language_info: { name: "python" },
    },
    cells: cells.map((c, i) => ({ id: `cell-${i}`, ...c })),
  };
  return JSON.stringify(notebook, null, 1) + "\n";
}

export function toMarimo(e: Export): string {
  const [connect, query] = code(e);
  const indent = (src: string) => src.replace(/^/gm, "    ");
  return `import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo

    mo.md(${py(intro(e))})
    return (mo,)


@app.cell
def _():
${indent(connect)}
    return (con,)


@app.cell
def _(con):
${indent(query)}
    return (df,)


if __name__ == "__main__":
    app.run()
`;
}

export function notebookName(question: string, ext: "ipynb" | "py"): string {
  const slug = question
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60)
    .replace(/-$/, "");
  return `${slug || "schemalab-answer"}.${ext}`;
}
