import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/api";

/**
 * Optimistic check only: no cookie means sign in first. The API still validates the token
 * on every request, so a forged or expired cookie gets a 401 there.
 */
export function proxy(request: NextRequest): NextResponse {
  if (!request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/", "/data", "/dashboard", "/normalize", "/er"] };
