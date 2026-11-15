// @vitest-environment node
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { config, proxy } from "./proxy";

const get = (path: string, cookie?: string) =>
  proxy(new NextRequest(`http://localhost${path}`, { headers: cookie ? { cookie } : {} }));
const nonceOf = (csp: string | null) => csp?.match(/'nonce-([^']+)'/)?.[1];

describe("the proxy", () => {
  it("sends a fresh script nonce with every page, and hands it to the renderer", () => {
    const first = get("/login");
    const second = get("/login");
    const nonce = nonceOf(first.headers.get("Content-Security-Policy"));
    expect(nonce).toMatch(/^[A-Za-z0-9+/=]{16,}$/);
    expect(nonceOf(second.headers.get("Content-Security-Policy"))).not.toBe(nonce);
    // Next.js reads the nonce from the request's CSP header while rendering.
    expect(nonceOf(first.headers.get("x-middleware-request-content-security-policy"))).toBe(nonce);
  });

  it("still sends a signed-out visitor to sign in", () => {
    const res = get("/assignments/7");
    expect(res.headers.get("location")).toBe("http://localhost/login");
  });

  it("lets a signed-in visitor through", () => {
    expect(get("/assignments/7", "sl_session=t").headers.get("location")).toBeNull();
  });

  it("leaves the sign-in page open to everyone", () => {
    expect(get("/login").headers.get("location")).toBeNull();
  });

  it("covers every page and the DuckDB worker, whose own policy fences its requests", () => {
    for (const url of ["/", "/login", "/assignments/7/dashboard", "/duckdb/1.32.0/duckdb-browser-eh.worker.js"]) {
      expect(unstable_doesMiddlewareMatch({ config, url }), url).toBe(true);
    }
    for (const url of ["/_next/static/chunks/main.js", "/api/ask", "/favicon.ico"]) {
      expect(unstable_doesMiddlewareMatch({ config, url }), url).toBe(false);
    }
  });
});
