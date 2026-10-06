import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Memory, { RejectDialog } from "./Memory";
import { resetCsrfForTests } from "@/lib/api";
import type { Proposal } from "@/api/types";

const proposal: Proposal = {
  id: "p-20261004-ab12cd", accept_command: "uv run coach memory accept p-20261004-ab12cd", status: "pending", created: "2026-10-04T10:00:00", file: "budgets.yaml",
  reason: "Add a restaurant budget", source: "coach-llm", sealed: true, applicable: true, error: null, diff: "+budgets:\n+  - id: b", changes: [], suspicious_paths: [],
};

let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  vi.stubGlobal("clipboard", undefined);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/proposals?")) body = { proposals: [proposal] };
    else if (url.startsWith("/api/v1/memory/overview")) body = { files: [], history_enabled: true, counts: { open_questions: 0 }, check: { errors: 0, warnings: 0, info: 0, by_code: {} } };
    else if (url.startsWith("/api/v1/memory/history?")) body = { enabled: true, changes: [{ id: "9f8e7d6", date: "2026-10-04T10:00:00", subject: "coach: set-budget", files: ["budgets.yaml"], reason: null, source: "ui" }] };
    else if (url.includes("/diff")) body = { id: "9f8e7d6", diff: "+x", revert_command: "uv run coach memory revert 9f8e7d6" };
    else if (url.includes("/reject")) body = { id: proposal.id, status: "rejected" };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("proposals are accepted in a terminal, never in the page", () => {
  it("shows the diff and the exact command, with no accept button and no code field", async () => {
    renderApp(<Memory />, "/memory?tab=proposals");
    expect(await screen.findByText("Add a restaurant budget")).toBeInTheDocument();
    expect(screen.getByText(/- id: b/)).toBeInTheDocument();
    expect(screen.getByText("uv run coach memory accept p-20261004-ab12cd")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copy command/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^accept/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText(/this page cannot accept for you/i)).toBeInTheDocument();
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });

  it("rejecting asks for confirmation first, then posts the rejection with the CSRF token", async () => {
    renderApp(<Memory />, "/memory?tab=proposals");
    await userEvent.click(await screen.findByRole("button", { name: /reject…/i }));
    const dialog = await screen.findByRole("dialog", { name: /reject this proposal/i });
    expect(calls.filter((c) => c.url.includes("/reject"))).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(calls.some((c) => c.url === `/api/v1/proposals/${proposal.id}/reject`)).toBe(true));
    const post = calls.find((c) => c.url.includes("/reject"))!;
    expect((post.init!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });

  it("the reject dialog can be cancelled without any request", async () => {
    const onClose = vi.fn();
    renderApp(<RejectDialog p={proposal} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(onClose).toHaveBeenCalled();
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });
});

describe("history is read-only in the page", () => {
  it("offers the revert command, not a revert button", async () => {
    renderApp(<Memory />, "/memory?tab=history");
    expect(await screen.findByText("set-budget")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^revert/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "View" }));
    expect(await screen.findByText("uv run coach memory revert 9f8e7d6")).toBeInTheDocument();
    expect(calls.some((c) => c.init?.method === "POST")).toBe(false);
  });
});
