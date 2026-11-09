/**
 * Server-side access to the FastAPI backend. The browser talks only to these Next.js routes;
 * the session token lives in an httpOnly cookie that page scripts cannot read.
 */
import { type NextRequest, NextResponse } from "next/server";

export const API_URL = process.env.API_URL ?? "http://localhost:8000";
export const SESSION_COOKIE = "sl_session";
const WEEK = 7 * 24 * 60 * 60; // matches the API's session lifetime

export function upstream(path: string, init: RequestInit = {}, token?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!headers.has("content-type")) headers.set("content-type", "application/json");
  if (token) headers.set("authorization", `Bearer ${token}`);
  return fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
}

export function setSession(res: NextResponse, token: string): void {
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: WEEK,
  });
}

export function clearSession(res: NextResponse): void {
  res.cookies.set(SESSION_COOKIE, "", { path: "/", maxAge: 0 });
}

/**
 * Refuse requests that did not come from our own pages: a same-origin request (Sec-Fetch-Site,
 * else Origin) with the expected body type. A cross-site HTML form can only send form or text
 * bodies, so requiring JSON or raw bytes rules forms out. `null` skips the body check, for
 * bodiless methods like DELETE. Without this, another site could sign a visitor in to an
 * attacker's account.
 */
export function rejectCrossSite(
  request: NextRequest,
  expected: string | null = "application/json",
): NextResponse | null {
  const site = request.headers.get("sec-fetch-site");
  const origin = request.headers.get("origin");
  const sameOrigin = site ? site === "same-origin" : origin === request.nextUrl.origin;
  if (!sameOrigin) return NextResponse.json({ detail: "Cross-site request refused." }, { status: 403 });

  const type = request.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
  if (expected && type !== expected) {
    return NextResponse.json({ detail: `Send ${expected}.` }, { status: 415 });
  }
  return null;
}
