import { describe, expect, it } from "vitest";
import { MAX_MB, checkFile } from "./upload";

describe("checkFile", () => {
  it.each(["orders.csv", "Sales.XLSX", "q1 report.xlsx"])("accepts %s", (name) => {
    expect(checkFile(name, 1024)).toBeNull();
  });

  it.each(["old.xls", "data.json", "notes", "archive.csv.zip"])("refuses %s by type", (name) => {
    expect(checkFile(name, 1024)).toBe("Choose a .csv or .xlsx file.");
  });

  it("refuses files over the API's limit before sending them", () => {
    expect(checkFile("big.csv", MAX_MB * 1024 * 1024 + 1)).toBe(`Files can be at most ${MAX_MB} MB.`);
    expect(checkFile("ok.csv", MAX_MB * 1024 * 1024)).toBeNull();
  });

  it("refuses an empty file", () => {
    expect(checkFile("empty.csv", 0)).toBe("That file is empty.");
  });
});
