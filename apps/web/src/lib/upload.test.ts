import { describe, expect, it } from "vitest";
import { MAX_MB, type Dataset, checkFile, relationships, skippedText } from "./upload";

describe("checkFile", () => {
  it.each(["orders.csv", "Sales.XLSX", "q1 report.xlsx", "shop_dump.sql"])("accepts %s", (name) => {
    expect(checkFile(name, 1024)).toBeNull();
  });

  it.each(["old.xls", "data.json", "notes", "archive.csv.zip"])("refuses %s by type", (name) => {
    expect(checkFile(name, 1024)).toBe("Choose a .csv, .xlsx or .sql file.");
  });

  it("refuses files over the API's limit before sending them", () => {
    expect(checkFile("big.csv", MAX_MB * 1024 * 1024 + 1)).toBe(`Files can be at most ${MAX_MB} MB.`);
    expect(checkFile("ok.csv", MAX_MB * 1024 * 1024)).toBeNull();
  });

  it("refuses an empty file", () => {
    expect(checkFile("empty.csv", 0)).toBe("That file is empty.");
  });
});

describe("skippedText", () => {
  it("says nothing when nothing was skipped", () => {
    expect(skippedText({})).toBeNull();
  });

  it("names each kind, most common first", () => {
    expect(skippedText({ GRANT: 1, SET: 12, "CREATE INDEX": 3 })).toBe(
      "Skipped 16 statements that hold no tables or rows: 12 SET, 3 CREATE INDEX, 1 GRANT.",
    );
  });

  it("uses the singular for one statement", () => {
    expect(skippedText({ GRANT: 1 })).toBe("Skipped 1 statement that holds no tables or rows: 1 GRANT.");
  });
});

describe("relationships", () => {
  const col = (name: string, note: string | null = null) => ({
    name, type: "INTEGER", min: null, max: null, distinct: 0, null_percent: 0, note,
  });
  const tables: Dataset[] = [
    { name: "orders", rows: 2, columns: [col("id", "primary key"), col("customer_id", "references customers(id)")] },
    { name: "items", rows: 1, columns: [col("order_id", "references orders")] },
    { name: "notes", rows: 0, columns: [col("body")] },
  ];

  it("lists each foreign key as from and to", () => {
    expect(relationships(tables)).toEqual([
      { from: "orders.customer_id", to: "customers.id" },
      { from: "items.order_id", to: "orders" },
    ]);
  });
});
