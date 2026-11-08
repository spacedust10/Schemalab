import type { ChartSpec } from "./chart";
import { fmt } from "./format";

/**
 * The one-sentence finding a view leads with (design_principles.md §6), computed from the
 * result in the browser, so no value goes to a model. Null when there is no honest summary.
 */
export function takeaway(spec: ChartSpec): string | null {
  switch (spec.kind) {
    case "number":
      return `${spec.label}: ${fmt(spec.value)}.`;
    case "bar": {
      const [first, second] = spec.bars;
      if (!second) return `${first.label}: ${fmt(first.value)}.`;
      const lead = `${first.label} has the most ${spec.measure}: ${fmt(first.value)}`;
      if (second.value <= 0) return `${lead}.`;
      const ratio = first.value / second.value;
      return ratio < 1.1
        ? `${lead}, just ahead of ${second.label} (${fmt(second.value)}).`
        : `${lead}, ${ratio.toFixed(1)}× ${second.label}.`;
    }
    case "line": {
      const a = spec.points[0];
      const b = spec.points[spec.points.length - 1];
      if (!a || a === b) return null;
      const verb = b.y > a.y ? "rose" : b.y < a.y ? "fell" : "held";
      return verb === "held"
        ? `${spec.measure} held at ${fmt(a.y)} from ${a.x} to ${b.x}.`
        : `${spec.measure} ${verb} from ${fmt(a.y)} in ${a.x} to ${fmt(b.y)} in ${b.x}.`;
    }
    case "table":
      return null;
  }
}
