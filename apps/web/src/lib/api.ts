/**
 * Server-side access to the FastAPI backend. The browser talks only to these Next.js routes;
 * the session token lives in an httpOnly cookie that page scripts cannot read.
 */
import type { NextResponse } from "next/server";

export const API_URL = process.env.API_URL ?? "http://localhost:8000";
export const SESSION_COOKIE = "sl_session";
const WEEK = 7 * 24 * 60 * 60; // matches the API's session lifetime

export function upstream(path: string, init: RequestInit = {}, token?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json");
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
