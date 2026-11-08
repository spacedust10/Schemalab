import type { Confidence } from "@/lib/trace";

const DOTS = { low: 1, medium: 2, high: 3 } as const;

export function ConfidenceNote({ confidence }: { confidence: Confidence }) {
  const filled = DOTS[confidence.label];
  return (
    <div className={`confidence tone-${confidence.label}`}>
      <p className="label">
        <span className="meter" aria-hidden="true">
          {[1, 2, 3].map((i) => (
            <i key={i} className={i <= filled ? "on" : undefined} />
          ))}
        </span>
        {confidence.label[0].toUpperCase() + confidence.label.slice(1)} confidence
      </p>
      <ul>
        {confidence.reasons.map((r) => (
          <li key={r}>{r[0].toUpperCase() + r.slice(1)}.</li>
        ))}
      </ul>
    </div>
  );
}
