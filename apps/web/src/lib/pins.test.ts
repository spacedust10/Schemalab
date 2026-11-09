import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DELETE as unpin } from "../app/api/pins/[id]/route";
import { GET as list, POST as pin } from "../app/api/pins/route";
import { SESSION_COOKIE } from "./api";

type Call = { url: string; init: RequestInit };

function fakeApi(respond: () => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url: String(url), init });
    return respond();
  });
  return calls;
}

const headers = (extra: Record<string, string> = { origin: "http://localhost:3000" }) => ({
  cookie: `${SESSION_COOKIE}=tok`,
  "content-type": "application/json",
  ...extra,
});
const PIN = { question: "How many?", sql: "SELECT 1", dataset: "sample" };

afterEach(() => vi.unstubAllGlobals());

describe("pins routes", () => {
  it("lists pins with the caller's token", async () => {
    const calls = fakeApi(() => Response.json([{ id: 1 }]));
    const res = await list(new NextRequest("http://localhost:3000/api/pins", { headers: headers() }));
    expect(await res.json()).toEqual([{ id: 1 }]);
    expect(calls[0].url).toBe("http://localhost:8000/pins");
    expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer tok");
  });

  it("pins with a JSON body and passes refusals through", async () => {
    const calls = fakeApi(() => Response.json({ detail: "You can pin at most 12 answers." }, { status: 400 }));
    const res = await pin(new NextRequest("http://localhost:3000/api/pins", { method: "POST", headers: headers(), body: JSON.stringify(PIN) }));
    expect(res.status).toBe(400);
    expect(calls[0].init.body).toBe(JSON.stringify(PIN));
  });

  it("refuses a cross-site pin", async () => {
    const calls = fakeApi(() => Response.json({ id: 1 }, { status: 201 }));
    const r = new NextRequest("http://localhost:3000/api/pins", {
      method: "POST",
      headers: headers({ origin: "https://evil.example" }),
      body: JSON.stringify(PIN),
    });
    expect((await pin(r)).status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it("unpins one id, escaped into one path segment", async () => {
    const calls = fakeApi(() => new Response(null, { status: 204 }));
    const r = new NextRequest("http://localhost:3000/api/pins/x", { method: "DELETE", headers: headers() });
    const res = await unpin(r, { params: Promise.resolve({ id: "1/../../auth" }) });
    expect(res.status).toBe(204);
    expect(calls[0].url).toBe("http://localhost:8000/pins/1%2F..%2F..%2Fauth");
  });
});
