import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { Login } from "./Login";
import { resetExchangeForTests } from "@/lib/api";

let calls: { url: string; init?: RequestInit }[];
let exchangeStatus = 200;
beforeEach(() => {
  resetExchangeForTests();
  calls = [];
  exchangeStatus = 200;
  window.history.replaceState(null, "", "/login");
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const ok = exchangeStatus === 200;
    return new Response(JSON.stringify(ok ? { ok: true } : { error: { code: "invalid_login_token", message: "this login link is invalid, already used or expired" } }), { status: exchangeStatus, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("login screen", () => {
  it("without a link it only explains how to get one and sends nothing", () => {
    render(<Login onDone={() => undefined} />);
    expect(screen.getByRole("heading", { name: /open the login link from your terminal/i })).toBeInTheDocument();
    expect(screen.getByText("uv run coach ui --login-link")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("trades the token from the URL fragment exactly once (even under StrictMode) and wipes it from the address bar", async () => {
    window.history.replaceState(null, "", "/login#t=tok-123");
    const done = vi.fn();
    render(<StrictMode><Login onDone={done} /></StrictMode>);
    await waitFor(() => expect(done).toHaveBeenCalled());
    const posts = calls.filter((c) => c.url === "/api/v1/session/exchange");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0].init!.body as string)).toEqual({ token: "tok-123" });
    expect(posts[0].url).not.toContain("tok-123"); // never in a URL that is sent anywhere
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/");
  });

  it("says so when the link is used up or expired, and removes the token anyway", async () => {
    exchangeStatus = 401;
    window.history.replaceState(null, "", "/login#t=old");
    const done = vi.fn();
    render(<Login onDone={done} />);
    expect(await screen.findByText(/did not work/i)).toBeInTheDocument();
    expect(screen.getByText(/already used or expired/i)).toBeInTheDocument();
    expect(done).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
  });
});
