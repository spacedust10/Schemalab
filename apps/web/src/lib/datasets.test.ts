import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DELETE as remove } from "../app/api/datasets/[table]/route";
import { GET as list, POST as upload } from "../app/api/datasets/route";
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

const SAME = { origin: "http://localhost:3000" };

function fileReq(
  name: string | null,
  { cookie = "tok", type = "application/octet-stream", extra = SAME }: { cookie?: string; type?: string; extra?: Record<string, string> } = {},
): NextRequest {
  const qs = name === null ? "" : `?name=${encodeURIComponent(name)}`;
  return new NextRequest(`http://localhost:3000/api/datasets${qs}`, {
    method: "POST",
    headers: { "content-type": type, cookie: `${SESSION_COOKIE}=${cookie}`, ...extra },
    body: "id,amount\n1,2\n",
  });
}

const tableParams = (table: string) => ({ params: Promise.resolve({ table }) });

afterEach(() => vi.unstubAllGlobals());

describe("datasets routes", () => {
  it("lists the caller's tables with their token", async () => {
    const calls = fakeApi(() => Response.json([{ name: "orders" }]));
    const res = await list(new NextRequest("http://localhost:3000/api/datasets", { headers: { cookie: `${SESSION_COOKIE}=tok` } }));
    expect(await res.json()).toEqual([{ name: "orders" }]);
    expect(calls[0].url).toBe("http://localhost:8000/datasets");
    expect(new Headers(calls[0].init.headers).get("authorization")).toBe("Bearer tok");
  });

  it("needs a session to list", async () => {
    const calls = fakeApi(() => Response.json([]));
    expect((await list(new NextRequest("http://localhost:3000/api/datasets"))).status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("streams the file to the API as raw bytes with its name", async () => {
    const calls = fakeApi(() => Response.json({ created: ["orders"], tables: [] }, { status: 201 }));
    const res = await upload(fileReq("Orders 2024.csv"));
    expect(res.status).toBe(201);
    expect(calls[0].url).toBe("http://localhost:8000/datasets?name=Orders+2024.csv");
    const headers = new Headers(calls[0].init.headers);
    expect(headers.get("content-type")).toBe("application/octet-stream");
    expect(headers.get("authorization")).toBe("Bearer tok");
    expect(await new Response(calls[0].init.body).text()).toBe("id,amount\n1,2\n");
  });

  it("passes the API's refusal through", async () => {
    fakeApi(() => Response.json({ detail: "Upload a .csv or .xlsx file." }, { status: 400 }));
    const res = await upload(fileReq("x.json"));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ detail: "Upload a .csv or .xlsx file." });
  });

  it("needs a file name", async () => {
    const calls = fakeApi(() => Response.json({}));
    expect((await upload(fileReq(null))).status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it.each([
    ["another site", { cookie: "tok", extra: { origin: "https://evil.example" } }, 403],
    ["a form post", { cookie: "tok", type: "multipart/form-data; boundary=x" }, 415],
    ["a text body", { cookie: "tok", type: "text/plain" }, 415],
  ])("refuses an upload from %s", async (_, opts, status) => {
    const calls = fakeApi(() => Response.json({}));
    expect((await upload(fileReq("a.csv", opts))).status).toBe(status);
    expect(calls).toHaveLength(0);
  });

  it("deletes a table, escaping its name into one path segment", async () => {
    const calls = fakeApi(() => new Response(null, { status: 204 }));
    const r = new NextRequest("http://localhost:3000/api/datasets/x", {
      method: "DELETE",
      headers: { cookie: `${SESSION_COOKIE}=tok`, ...SAME },
    });
    const res = await remove(r, tableParams("../auth/logout"));
    expect(res.status).toBe(204);
    expect(calls[0].url).toBe("http://localhost:8000/datasets/..%2Fauth%2Flogout");
    expect(calls[0].init.method).toBe("DELETE");
  });

  it("refuses a cross-site delete", async () => {
    const calls = fakeApi(() => new Response(null, { status: 204 }));
    const r = new NextRequest("http://localhost:3000/api/datasets/x", {
      method: "DELETE",
      headers: { cookie: `${SESSION_COOKIE}=tok`, origin: "https://evil.example" },
    });
    expect((await remove(r, tableParams("orders"))).status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});
