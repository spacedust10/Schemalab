import type { ColumnProfile } from "@/lib/upload";

/** One row per column: type, how much is missing, distinct values and range. */
export function ProfileTable({ columns }: { columns: ColumnProfile[] }) {
  return (
    <div className="table-wrap">
      <table>
        <caption>Column profile</caption>
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Type</th>
            <th scope="col" className="num">Empty</th>
            <th scope="col" className="num">Distinct (approx.)</th>
            <th scope="col">Smallest</th>
            <th scope="col">Largest</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.name}>
              <th scope="row" className="mono">{c.name}</th>
              <td className="mono">{c.type}</td>
              <td className="num">{c.null_percent ? `${c.null_percent}%` : "none"}</td>
              <td className="num">{c.distinct.toLocaleString("en-US")}</td>
              <td className="clip">{c.min ?? "none"}</td>
              <td className="clip">{c.max ?? "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
