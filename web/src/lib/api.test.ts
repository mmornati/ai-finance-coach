import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, resetCsrfForTests, streamSSE } from "./api";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  resetCsrfForTests();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("GET sends no CSRF header and builds the query", async () => {
    fetchMock.mockResolvedValueOnce(json({ ok: 1 }));
    await api.get("/transactions", { tag: ["a", "b"], q: "x" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/transactions?tag=a&tag=b&q=x");
    expect(init.headers["X-CSRF-Token"]).toBeUndefined();
    expect(init.credentials).toBe("same-origin");
  });

  it("mutations fetch the token once and send it as a header with a JSON body", async () => {
    fetchMock.mockResolvedValueOnce(json({ csrf_token: "tok1" })).mockImplementation(() => Promise.resolve(json({ done: true })));
    await api.post("/budgets", { a: 1 }, { dry_run: true });
    await api.post("/budgets", { a: 2 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/session");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/v1/budgets?dry_run=true");
    expect(init.headers).toMatchObject({ "X-CSRF-Token": "tok1", "Content-Type": "application/json" });
    expect(init.body).toBe('{"a":1}');
    expect(fetchMock).toHaveBeenCalledTimes(3); // the token is fetched once
  });

  it("refreshes a stale token once and retries", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ csrf_token: "old" }))
      .mockResolvedValueOnce(json({ error: { code: "csrf", message: "bad token" } }, 403))
      .mockResolvedValueOnce(json({ csrf_token: "new" }))
      .mockResolvedValueOnce(json({ ok: true }));
    await expect(api.post("/x", {})).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls[3][1].headers["X-CSRF-Token"]).toBe("new");
  });

  it("turns the error model into an ApiError", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: { code: "unknown_category", message: "unknown category 'x'", details: [1] } }, 422));
    const e = await api.get("/nope").catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ status: 422, code: "unknown_category", message: "unknown category 'x'", details: [1] });
  });

  it("survives a non-JSON error page", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<h1>boom</h1>", { status: 502, statusText: "Bad Gateway" }));
    await expect(api.get("/x")).rejects.toMatchObject({ status: 502, message: "Bad Gateway" });
  });

  it("download URLs are same-origin API paths", () => {
    expect(api.url("/transactions/export.csv", { q: "a" })).toBe("/api/v1/transactions/export.csv?q=a");
  });
});

describe("server-sent events", () => {
  it("parses events split across chunks", async () => {
    fetchMock.mockResolvedValueOnce(json({ csrf_token: "t" }));
    const enc = new TextEncoder();
    const chunks = ['event: meta\ndata: {"conversation_id":"c1"}\n\nevent: del', 'ta\ndata: {"text":"Hel"}\n\nevent: delta\ndata: {"text":"lo"}\n\n', 'event: done\ndata: {"finish_reason":"stop"}\n\n'];
    const body = new ReadableStream({
      start(c) {
        chunks.forEach((x) => c.enqueue(enc.encode(x)));
        c.close();
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(body, { status: 200 }));
    const got: [string, any][] = [];
    await streamSSE("/coach/stream", { question: "q" }, (e, d) => got.push([e, d]));
    expect(got.map((g) => g[0])).toEqual(["meta", "delta", "delta", "done"]);
    expect(got.filter((g) => g[0] === "delta").map((g) => g[1].text).join("")).toBe("Hello");
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("t");
  });
});
