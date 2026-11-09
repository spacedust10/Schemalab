import { describe, expect, it } from "vitest";
import { type Export, notebookName, toIpynb, toMarimo } from "./notebook";

const base: Export = {
  question: "Which pickup borough had the most taxi trips?",
  headline: "Manhattan has the most trips: 2,646,948.",
  confidence: "high",
  sql: "SELECT z.Borough, count(*) AS trips FROM taxi_trips t JOIN taxi_zones z ON t.PULocationID = z.LocationID GROUP BY 1",
  dataset: "sample",
};

// Model-written SQL must stay a string in the exported code, whatever it contains.
const hostile = `SELECT 1 AS x """ + __import__('os').system('rm -rf ~') + """ \\ '`;

describe("toIpynb", () => {
  const nb = JSON.parse(toIpynb(base));

  it("is an nbformat 4 notebook with a markdown summary and code cells", () => {
    expect(nb.nbformat).toBe(4);
    expect(nb.metadata.kernelspec.language).toBe("python");
    const [intro, ...code] = nb.cells;
    expect(intro.cell_type).toBe("markdown");
    expect(intro.source.join("")).toContain(base.question);
    expect(intro.source.join("")).toContain("Confidence: high");
    expect(code.every((c: { cell_type: string }) => c.cell_type === "code")).toBe(true);
  });

  it("runs the answer's SQL read-only against the sample database", () => {
    const source = nb.cells.map((c: { source: string[] }) => c.source.join("")).join("\n");
    expect(source).toContain(`DB = "data/samples/schemalab.duckdb"`);
    expect(source).toContain("duckdb.connect(DB, read_only=True)");
    expect(source).toContain(`SQL = ${JSON.stringify(base.sql)}`);
  });

  it("keeps hostile SQL inside one string literal", () => {
    const source = JSON.parse(toIpynb({ ...base, sql: hostile }))
      .cells.map((c: { source: string[] }) => c.source.join(""))
      .join("\n");
    expect(source).toContain(`SQL = ${JSON.stringify(hostile)}`);
  });

  it("says where to point the notebook for uploaded tables", () => {
    const md = JSON.parse(toIpynb({ ...base, dataset: "mine" })).cells[0].source.join("");
    expect(md).toContain("your own tables");
  });
});

describe("toMarimo", () => {
  const app = toMarimo(base);

  it("is a marimo app with cells and a main guard", () => {
    expect(app.startsWith("import marimo\n")).toBe(true);
    expect(app).toContain("app = marimo.App()");
    expect(app).toContain("@app.cell");
    expect(app.trimEnd().endsWith('if __name__ == "__main__":\n    app.run()')).toBe(true);
  });

  it("embeds the question and SQL as string literals only", () => {
    const hostileApp = toMarimo({ ...base, question: 'x""" {os.system("id")}', sql: hostile });
    expect(hostileApp).toContain(`SQL = ${JSON.stringify(hostile)}`);
    expect(hostileApp).toContain(`mo.md(${JSON.stringify('# x""" {os.system("id")}').slice(0, -1)}\\n`);
  });
});

describe("notebookName", () => {
  it("makes a short file name from the question", () => {
    expect(notebookName(base.question, "ipynb")).toBe("which-pickup-borough-had-the-most-taxi-trips.ipynb");
    expect(notebookName("???", "py")).toBe("schemalab-answer.py");
  });
});
