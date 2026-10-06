import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Usage from "./Usage";
import { resetCsrfForTests } from "@/lib/api";

const summary = (calls: number, month: Record<string, unknown> = {}) => ({
  days: 30, journal_available: true, note: "claude-code costs are NOTIONAL.",
  totals: { calls, tokens_in: 1200, tokens_out: 800, cost_usd: 0.53, notional_cost_usd: 0.53, estimated_cost_usd: 0, unknown_cost_calls: 0, duration_s: 68 },
  lines: calls ? [{ job: "classify run", purpose: "label", backend: "claude-code", model: "sonnet", calls: 3, tokens_in: 600, tokens_out: 500, cache_read_tokens: 4000, cache_write_tokens: 300, cost_usd: 0.239, cost_unknown_calls: 0, notional: true, duration_s: 38, avg_duration_s: 12.7 }] : [],
  by_job: [], by_day: [{ date: "2026-10-04", calls: 3, tokens_in: 600, tokens_out: 500, cost_usd: 0.239 }],
  destinations: calls ? [{ kind: "llm.claude-code", host: "api.anthropic.com (via the claude CLI)", purpose: "classify.label", calls: 3, bytes: 5000, denied: 0, web: false, last: "2026-10-04T10:00:00+00:00" }] : [],
  month: { month: "2026-10", cost_usd: 0.53, threshold_usd: null, ratio: null, level: null, unknown_cost_calls: 0, include_notional: true, ...month },
});
let calls: string[];
function serve(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    return new Response(JSON.stringify(url.endsWith("/session") ? { csrf_token: "tok" } : body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
beforeEach(() => { resetCsrfForTests(); calls = []; });
afterEach(() => vi.unstubAllGlobals());

describe("usage page", () => {
  it("lists the jobs with their notional cost and where the calls went, never a payload", async () => {
    serve(summary(3));
    renderApp(<Usage />);
    expect(await screen.findByText("classify run")).toBeInTheDocument();
    expect(screen.getByText("notional")).toBeInTheDocument();
    expect(screen.getByText(/Cache read \/ written/)).toBeInTheDocument();
    expect(screen.getByText(/api.anthropic.com \(via the claude CLI\)/)).toBeInTheDocument();
    expect(screen.getByText(/5[ ,.  ]?000 bytes/)).toBeInTheDocument();
    expect(calls.find((c) => c.startsWith("/api/v1/usage"))).toContain("days=30");
  });

  it("changes the period", async () => {
    serve(summary(3));
    renderApp(<Usage />);
    await screen.findByText("classify run");
    await userEvent.click(screen.getByRole("button", { name: "90 days" }));
    await waitFor(() => expect(calls.some((c) => c.includes("days=90"))).toBe(true));
  });

  it("says when nothing was called", async () => {
    serve(summary(0));
    renderApp(<Usage />);
    expect(await screen.findByText("No AI call in this period")).toBeInTheDocument();
  });

  it("warns when the month passed the threshold set in the configuration", async () => {
    serve(summary(3, { threshold_usd: 0.25, ratio: 2.12, level: "high", cost_usd: 0.53 }));
    renderApp(<Usage />);
    expect(await screen.findByText(/over the threshold you set/i)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /against your threshold/i })).toBeInTheDocument();
  });
});
