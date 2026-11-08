import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as ask } from "../app/api/ask/route";
import { POST as auth } from "../app/api/auth/[action]/route";
import { proxy } from "../proxy";
import { SESSION_COOKIE } from "./api";

type Call = { url: string; init: RequestInit };

function fakeApi(respond: (call: Call) => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const call = { url: String(url), init };
    calls.push(call);
    return respond(call);
  });
  return calls;
}

function req(path: string, body?: unknown, cookie?: string): NextRequest {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (cookie) headers.cookie = `${SESSION_COOKIE}=${cookie}`;
  return new NextRequest(`http://localhost:3000${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const params = (action: string) => ({ params: Promise.resolve({ action }) });

afterEach(() => vi.unstubAllGlobals());

describe("sign-in route", () => {
  it.each(["login", "register"])("%s keeps the token in an httpOnly cookie, not the body", async (action) => {
    const calls = fakeApi(() => Response.json({ token: "tok-123" }, { status: action === "register" ? 201 : 200 }));
    const res = await auth(req(`/api/auth/${action}`, { email: "a@b.co", password: "x".repeat(12) }), params(action));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(calls[0].url).toBe(`http://localhost:8000/auth/${action}`);
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`${SESSION_COOKIE}=tok-123`);
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie.toLowerCase()).toContain("samesite=lax");
    expect(cookie).toContain("Path=/");
  });

  it("passes the API's error through without setting a cookie", async () => {
    fakeApi(() => Response.json({ detail: "Invalid email or password." }, { status: 401 }));
    const res = await auth(req("/api/auth/login", { email: "a@b.co", password: "nope" }), params("login"));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Invalid email or password." });
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("logout revokes upstream and clears the cookie", async () => {
    const calls = fakeApi(() => new Response(null, { status: 204 }));
    const res = await auth(req("/api/auth/logout", {}, "tok-123"), params("logout"));
    expect(res.status).toBe(204);
    expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer tok-123");
    expect(res.headers.get("set-cookie")).toMatch(new RegExp(`${SESSION_COOKIE}=;.*(Max-Age=0|Expires=Thu, 01 Jan 1970)`));
  });

  it("rejects unknown actions", async () => {
    const res = await auth(req("/api/auth/delete-everything", {}), params("delete-everything"));
    expect(res.status).toBe(404);
  });
});

describe("ask route", () => {
  it("needs a session cookie", async () => {
    const calls = fakeApi(() => new Response("unused"));
    const res = await ask(req("/api/ask", { question: "hi" }));
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("adds the token and streams the API's events through", async () => {
    const calls = fakeApi(
      () => new Response('data: {"step":"schema"}\n\n', { headers: { "content-type": "text/event-stream" } }),
    );
    const res = await ask(req("/api/ask", { question: "hi" }, "tok-123"));
    expect(res.headers.get("content-type")).toContain("text/event-stream");
    expect(await res.text()).toBe('data: {"step":"schema"}\n\n');
    expect(calls[0].url).toBe("http://localhost:8000/ask");
    expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer tok-123");
    expect(calls[0].init.body).toBe(JSON.stringify({ question: "hi" }));
  });

  it("clears a session the API no longer accepts", async () => {
    fakeApi(() => Response.json({ detail: "Not signed in." }, { status: 401 }));
    const res = await ask(req("/api/ask", { question: "hi" }, "stale"));
    expect(res.status).toBe(401);
    expect(res.headers.get("set-cookie")).toContain(`${SESSION_COOKIE}=;`);
  });
});

describe("proxy", () => {
  it("sends visitors without a session to the sign-in page", () => {
    const res = proxy(req("/"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("http://localhost:3000/login");
  });

  it("lets signed-in visitors through", () => {
    expect(proxy(req("/", undefined, "tok")).headers.get("location")).toBeNull();
  });
});
