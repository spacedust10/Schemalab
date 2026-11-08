const number = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** Numbers with thousands separators and at most two decimals; anything else as text. */
export function fmt(value: unknown): string {
  if (typeof value === "number") return number.format(value);
  if (value === null || value === undefined) return "—";
  return String(value);
}
