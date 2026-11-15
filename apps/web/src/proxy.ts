import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/api";
import { contentSecurityPolicy } from "@/lib/csp";

const SIGNED_IN = /^\/(data|dashboard|normalize|er|algebra|lab|theory|plans|bench|patterns|pipelines|lakehouse|assignments(\/[^/]+(\/dashboard)?)?)?$/;

/**
 * Gives every page a fresh script nonce in its Content-Security-Policy; Next.js reads it
 * from the request header and puts it on the page's own scripts.
 * Signed-in pages get an optimistic check only: no cookie means sign in first. The API
 * still validates the token on every request, so a forged or expired cookie gets a 401.
 */
export function proxy(request: NextRequest): NextResponse {
  if (SIGNED_IN.test(request.nextUrl.pathname) && !request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  const csp = contentSecurityPolicy(process.env.NODE_ENV === "development", btoa(crypto.randomUUID()));
  const headers = new Headers(request.headers);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

// Pages and public files (the DuckDB worker's policy fences its requests); not the hashed
// bundles or the JSON API, which no browser runs as a document or worker.
export const config = { matcher: ["/((?!_next/static|_next/image|api/|favicon.ico).*)"] };
