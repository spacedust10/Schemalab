import type { Provenance as P } from "@/lib/trace";

export function Provenance({ provenance: p }: { provenance: P }) {
  return (
    <section>
      <h2>Provenance</h2>
      <dl className="provenance">
        <dt>Tables</dt>
        <dd>{p.tables.join(", ") || "none"}</dd>
        <dt>Attempts</dt>
        <dd>{p.attempts}</dd>
        <dt>Models</dt>
        <dd>{p.models.join(", ")}</dd>
        <dt>Rows sent to model</dt>
        <dd>{p.results_shared_with_model ? "Yes, up to 20 result rows" : "No, schema and SQL only"}</dd>
      </dl>
      <pre className="sql">
        <code>{p.sql}</code>
      </pre>
    </section>
  );
}
