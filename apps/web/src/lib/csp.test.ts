// @vitest-environment node
import { describe, expect, it } from "vitest";
import { contentSecurityPolicy } from "./csp";
import nextConfig from "../../next.config";

const directives = (csp: string) =>
  Object.fromEntries(csp.split(";").map((d) => d.trim().split(/\s+/)).filter((d) => d[0]).map(([k, ...v]) => [k, v]));

describe("the Content-Security-Policy", () => {
  it("keeps every request on this site and lets DuckDB-WASM compile", () => {
    const d = directives(contentSecurityPolicy(false));
    expect(d["default-src"]).toEqual(["'self'"]);
    expect(d["connect-src"]).toEqual(["'self'"]); // the scratchpad cannot reach another site
    expect(d["script-src"]).toContain("'wasm-unsafe-eval'");
    expect(d["script-src"]).not.toContain("'unsafe-eval'");
    expect(d["worker-src"]).toEqual(["'self'"]);
    expect(d["frame-ancestors"]).toEqual(["'none'"]);
    expect(d["object-src"]).toEqual(["'none'"]);
  });

  it("allows eval only in development, for hot reload", () => {
    expect(directives(contentSecurityPolicy(true))["script-src"]).toContain("'unsafe-eval'");
  });

  it("is sent on every page", async () => {
    const rules = await nextConfig.headers!();
    const all = rules.find((r) => r.source === "/:path*");
    expect(all?.headers.map((h) => h.key)).toContain("Content-Security-Policy");
  });
});

describe("caching the DuckDB-WASM files", () => {
  it("caches them for a year: the version is in the path, so they never change", async () => {
    const rules = await nextConfig.headers!();
    const duck = rules.find((r) => r.source === "/duckdb/:path*");
    expect(duck?.headers).toContainEqual({ key: "Cache-Control", value: "public, max-age=31536000, immutable" });
  });
});
