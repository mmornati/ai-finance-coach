import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Gold from "./Gold";
import { resetCsrfForTests } from "@/lib/api";

const sample = [
  { tx_key: "t1", date: "2026-09-02", amount: -120.5, description: "CARTE 01/09 ZEKALU MARKET", account: "Main", merchant_key: "ZEKALU MARKET", merchant: "Zekalu Market", category: "food.groceries", source: "llm", confidence: 0.6, type: "card" },
  { tx_key: "t2", date: "2026-09-03", amount: -40, description: "PRLV VOMIRA ENERGIE", account: "Main", merchant_key: "VOMIRA ENERGIE", merchant: null, category: "other.uncategorized", source: "none", confidence: null, type: "direct_debit" },
];
const latest = { id: 3, ts: "2026-10-04T10:00:00+00:00", kind: "classify", label: "web", backend: null, model: null, n: 40, cost_usd: null, duration_s: 0.2, summary: { method: "v2", n: 30, accuracy_tx: 0.75, accuracy_money: 0.5, llm_accuracy_tx: 0.6, llm_n: 10, pipeline_accuracy_tx: 0.99 } };
let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/gold?")) body = { counts: { total: 40, by_origin: { annotation: 30, merchant_label: 10 }, by_labeled_by: {}, categories: 7 }, latest, runs: [latest], sample, strategy: "money", seed: 7 };
    else if (url.startsWith("/api/v1/meta/taxonomy")) body = { groups: [{ id: "food", categories: [{ id: "food.groceries", description: "" }, { id: "food.restaurants", description: "" }] }, { id: "housing", categories: [{ id: "housing.energy", description: "" }] }] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("gold set page", () => {
  it("shows the counts by origin, the latest scores and says labelling never changes a category", async () => {
    renderApp(<Gold />);
    expect(await screen.findByText("Transactions to label")).toBeInTheDocument();
    expect(screen.getByText(/never changes a category in the app/i)).toBeInTheDocument();
    expect(screen.getByText(/Your memory annotations: 30/)).toBeInTheDocument();
    expect(screen.getByText(/Your merchant labels: 10/)).toBeInTheDocument();
    expect(screen.getByText("Classifier accuracy on merchant-level truth").parentElement).toHaveTextContent(/75/);
    expect(screen.getByText("Classifier accuracy on merchant-level truth").parentElement).toHaveTextContent(/50/);
    expect(screen.getByText("Pipeline with memory (what you see)").parentElement).toHaveTextContent(/99/);
  });

  it("flags a score made with the deprecated first method instead of showing it as a headline", async () => {
    const old = { ...latest, summary: { accuracy_tx: 0.9 } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/session") ? { csrf_token: "tok" } : url.startsWith("/api/v1/gold?") ? { counts: { total: 5, by_origin: {}, by_labeled_by: {}, categories: 1 }, latest: old, runs: [old], sample: [], strategy: "money", seed: 7 } : {}), { status: 200, headers: { "Content-Type": "application/json" } })));
    renderApp(<Gold />);
    expect(await screen.findByText(/method_v1/)).toBeInTheDocument();
    expect(screen.getByText("Classifier accuracy on merchant-level truth").parentElement).toHaveTextContent(/n\/a/);
  });

  it("confirming the current label writes the gold set only (with the CSRF token)", async () => {
    renderApp(<Gold />);
    const row = (await screen.findByText(/ZEKALU MARKET/)).closest("section")!;
    await userEvent.click(within(row).getByRole("button", { name: /it is groceries/i }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/gold/label")).toBe(true));
    const post = calls.find((c) => c.url === "/api/v1/gold/label")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({ tx_key: "t1", category: "food.groceries" });
    expect((post.init!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
    expect(calls.some((c) => c.url.includes("/review/") || c.url.includes("/memory"))).toBe(false);
  });

  it("an unlabelled transaction needs a category before it can be set, and Skip hides it without writing", async () => {
    renderApp(<Gold />);
    const row = (await screen.findByText(/VOMIRA ENERGIE/)).closest("section")!;
    const set = within(row).getByRole("button", { name: "Set" });
    expect(set).toBeDisabled();
    await within(row).findByRole("option", { name: "Energy" });
    await userEvent.selectOptions(within(row).getByRole("combobox"), "housing.energy");
    expect(set).toBeEnabled();
    await userEvent.click(within(row).getByRole("button", { name: "Skip" }));
    expect(screen.queryByText(/VOMIRA ENERGIE/)).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/v1/gold/label")).toBe(false);
  });

  it("Score it now asks the server to re-score offline", async () => {
    renderApp(<Gold />);
    await userEvent.click(await screen.findByRole("button", { name: /score it now/i }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/eval/classify")).toBe(true));
  });
});
