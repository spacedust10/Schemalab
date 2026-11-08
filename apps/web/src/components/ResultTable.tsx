import { humanize } from "@/lib/chart";
import { fmt } from "@/lib/format";
import type { Result } from "@/lib/trace";

const SHOWN = 50;

export function ResultTable({ result }: { result: Result }) {
  const rows = result.rows.slice(0, SHOWN);
  const numeric = result.columns.map((_, i) => rows.some((r) => typeof r[i] === "number"));
  const more = result.rows.length > SHOWN || result.truncated;
  return (
    <div className="table-wrap">
      <table>
        <caption>
          {result.rows.length.toLocaleString("en-US")} {result.rows.length === 1 ? "row" : "rows"}
          {more ? `, first ${rows.length} shown` : ""}
          {result.truncated ? "; the query returned more than were fetched" : ""}
        </caption>
        <thead>
          <tr>
            {result.columns.map((c, i) => (
              <th key={c + i} scope="col" className={numeric[i] ? "num" : undefined}>
                {humanize(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, i) => (
                <td key={i} className={numeric[i] ? "num" : undefined}>
                  {fmt(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
