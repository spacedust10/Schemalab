"use client";

import * as Plot from "@observablehq/plot";
import { useEffect, useRef, useState } from "react";
import type { ChartSpec } from "@/lib/chart";
import { fmt } from "@/lib/format";

/**
 * Bars start at zero and carry their values as direct labels; the leading bar is the only
 * saturated mark, since it is the one the takeaway is about. Lines label their last point.
 * Colors come from the CSS tokens, re-read when the color scheme changes.
 */
export function ResultChart({ spec }: { spec: ChartSpec }) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const colors = useTokens();

  useEffect(() => {
    const el = ref.current;
    if (!el || !width || (spec.kind !== "bar" && spec.kind !== "line")) return;
    const chart = spec.kind === "bar" ? bars(spec, width, colors) : line(spec, width, colors);
    el.replaceChildren(chart);
    return () => chart.remove();
  }, [spec, width, colors]);

  if (spec.kind === "number") {
    return (
      <figure className="figure">
        <p className="bignum">{fmt(spec.value)}</p>
        <figcaption>{spec.label}</figcaption>
      </figure>
    );
  }
  if (spec.kind === "table") return null;
  return (
    <figure className="figure">
      <div ref={ref} role="img" aria-label={describe(spec)} />
      <figcaption>
        {spec.kind === "bar"
          ? `${spec.measure} by ${spec.dimension}, largest first. Bars start at zero.`
          : `${spec.measure} by ${spec.dimension}.`}
      </figcaption>
    </figure>
  );
}

type Tokens = { ink: string; muted: string; mark: string; rule: string };

function bars(spec: Extract<ChartSpec, { kind: "bar" }>, width: number, c: Tokens) {
  const top = spec.bars[0]?.label;
  const longest = Math.max(...spec.bars.map((b) => b.label.length));
  return Plot.plot({
    width,
    height: Math.max(120, spec.bars.length * 30 + 40),
    marginLeft: Math.min(180, 12 + longest * 7.5),
    marginRight: 96,
    style: { fontFamily: "var(--sans)", fontSize: "13px", color: c.ink, background: "transparent" },
    x: { axis: null, domain: [0, Math.max(...spec.bars.map((b) => b.value)) * 1.02] },
    y: { label: null, domain: spec.bars.map((b) => b.label), tickSize: 0 },
    marks: [
      Plot.barX(spec.bars, {
        x: "value",
        y: "label",
        fill: (d: { label: string }) => (d.label === top ? c.mark : c.muted),
        insetTop: 4,
        insetBottom: 4,
      }),
      Plot.text(spec.bars, {
        x: "value",
        y: "label",
        text: (d: { value: number }) => fmt(d.value),
        textAnchor: "start",
        dx: 6,
        fill: c.ink,
      }),
      Plot.ruleX([0], { stroke: c.ink }),
    ],
  });
}

function line(spec: Extract<ChartSpec, { kind: "line" }>, width: number, c: Tokens) {
  const last = spec.points[spec.points.length - 1];
  return Plot.plot({
    width,
    height: 300,
    marginRight: 72,
    style: { fontFamily: "var(--sans)", fontSize: "13px", color: c.ink, background: "transparent" },
    x: { label: spec.dimension, tickFormat: (v: unknown) => String(v) },
    y: { label: spec.measure, grid: true, zero: true },
    marks: [
      Plot.ruleY([0], { stroke: c.ink }),
      Plot.line(spec.points, { x: "x", y: "y", stroke: c.mark, strokeWidth: 2 }),
      Plot.dot(spec.points, { x: "x", y: "y", fill: c.mark, r: 2.5 }),
      Plot.text(last ? [last] : [], {
        x: "x",
        y: "y",
        text: (d: { y: number }) => fmt(d.y),
        textAnchor: "start",
        dx: 8,
        fill: c.ink,
        fontWeight: 700,
      }),
    ],
  });
}

function describe(spec: Extract<ChartSpec, { kind: "bar" | "line" }>): string {
  if (spec.kind === "bar") {
    return `Bar chart of ${spec.measure} by ${spec.dimension}: ${spec.bars
      .slice(0, 5)
      .map((b) => `${b.label} ${fmt(b.value)}`)
      .join(", ")}${spec.bars.length > 5 ? ", and more" : ""}.`;
  }
  const [a, b] = [spec.points[0], spec.points[spec.points.length - 1]];
  return `Line chart of ${spec.measure} by ${spec.dimension}, from ${fmt(a?.y)} at ${a?.x} to ${fmt(b?.y)} at ${b?.x}.`;
}

function useWidth(ref: React.RefObject<HTMLDivElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

function useTokens(): Tokens {
  const read = (): Tokens => {
    const s = getComputedStyle(document.documentElement);
    const v = (name: string) => s.getPropertyValue(name).trim();
    return { ink: v("--ink"), muted: v("--mark-muted"), mark: v("--mark"), rule: v("--rule") };
  };
  const [tokens, setTokens] = useState<Tokens>(() =>
    typeof document === "undefined" ? { ink: "#000", muted: "#999", mark: "#0072b2", rule: "#ddd" } : read(),
  );
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const update = () => setTokens(read());
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return tokens;
}
