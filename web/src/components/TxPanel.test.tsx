import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import { TxPanel } from "./TxPanel";
import { resetCsrfForTests } from "@/lib/api";

const detail = {
  transaction: { tx_key: "a:ref:1", date: "2026-10-03", amount: "-29.54", description: "CARTE WHATNOT", bank: "Revolut", account: "Main", account_purpose: "main" },
  parsed: {}, steps: [{ step: "LLM label", applies: true, detail: "label shopping.marketplace", category: "shopping.marketplace", source: "llm", decides: true }],
  split: null, memory: { annotations: [], winner: null }, final: { category: "shopping.marketplace", source: "llm", tags: [], event: null, before_memory: { category: "shopping.marketplace", source: "llm" } },
  consistent: true, override: null, transfer_link: null, same_merchant_count: 4,
  item: { tx_key: "a:ref:1", date: "2026-10-03", amount: "-29.54", category: "shopping.marketplace", source: "llm", tags: [], event: null, entity: "Whatnot", merchant: "Whatnot", description: "CARTE WHATNOT", account: "a", account_label: "Main", owner: null, purpose: null, type: "card", split: false, overridden: false, transfer_linked: false },
};
const taxonomy = { groups: [{ id: "shopping", categories: [{ id: "shopping.marketplace", description: "" }, { id: "shopping.clothing", description: "" }] }, { id: "pets", categories: [{ id: "pets.pets", description: "" }] }] };

let calls: { url: string; init?: RequestInit }[];
let preview: Record<string, unknown>;
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  preview = { dry_run: true, scope: "merchant", category: "pets.pets", changed: true, diff: "", warnings: [], affected: { count: 3, already: 0, matched: 3, total: "-60.00", from_categories: [{ category: "shopping.marketplace", n: 3 }], blocked: [] } };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/transactions/detail")) body = detail;
    else if (url.startsWith("/api/v1/meta/taxonomy")) body = taxonomy;
    else if (url.startsWith("/api/v1/meta/filters")) body = { accounts: [], owners: [], members: [], purposes: [], tags: [], sources: [], events: [], groups: [], today: "" };
    else if (url.startsWith("/api/v1/transactions/category")) body = url.includes("dry_run=true") ? preview : { ...preview, dry_run: false };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

async function open() {
  const onClose = vi.fn();
  renderApp(<TxPanel txKey="a:ref:1" onClose={onClose} />);
  await screen.findByText("CARTE WHATNOT");
  await userEvent.selectOptions(await screen.findByLabelText("New category"), "pets.pets");
  return onClose;
}
const previews = () => calls.filter((c) => c.url.startsWith("/api/v1/transactions/category") && c.url.includes("dry_run=true"));
const writes = () => calls.filter((c) => c.url.startsWith("/api/v1/transactions/category") && !c.url.includes("dry_run"));

describe("category fix panel", () => {
  it("previews what will really change and writes only on Apply, with the same scope", async () => {
    const onClose = await open();
    expect(await screen.findByTestId("effect")).toHaveTextContent("3 transactions will change to Pets");
    expect(screen.getByText(/Currently: Marketplace ×3/)).toBeInTheDocument();
    expect(JSON.parse(previews().at(-1)!.init!.body as string)).toMatchObject({ tx_key: "a:ref:1", category: "pets.pets", scope: "merchant" });
    expect(writes()).toHaveLength(0);
    const apply = screen.getByRole("button", { name: "Apply" });
    await waitFor(() => expect(apply).toBeEnabled());
    await userEvent.click(apply);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(writes()).toHaveLength(1);
    expect(JSON.parse(writes()[0].init!.body as string)).toMatchObject({ scope: "merchant", category: "pets.pets" });
    expect((writes()[0].init!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });

  it("re-previews when the scope changes and shows the memory diff", async () => {
    await open();
    await screen.findByTestId("effect");
    preview = { ...preview, scope: "memory", diff: "+annotations:\n+  - id: whatnot-pets", warnings: [] };
    await userEvent.click(screen.getByRole("radio", { name: /remember with a memory annotation/i }));
    await waitFor(() => expect(JSON.parse(previews().at(-1)!.init!.body as string).scope).toBe("memory"));
    expect(await screen.findByText(/whatnot-pets/)).toBeInTheDocument();
  });

  it("disables Apply and says so when nothing would change, naming what wins", async () => {
    preview = { ...preview, changed: false, warnings: ["1 transaction(s) keep their category because of the memory annotation 'pin-it'"], affected: { count: 0, already: 0, matched: 1, total: "0.00", from_categories: [], blocked: [{ reason: "the memory annotation 'pin-it'", n: 1 }] } };
    await open();
    expect(await screen.findByTestId("effect")).toHaveTextContent("Nothing would change");
    expect(screen.getByText(/because of the memory annotation 'pin-it'/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });

  it("enables Apply when a merchant fix changes transactions even though an annotation decided the old category", async () => {
    preview = { ...preview, affected: { count: 3, already: 0, matched: 3, total: "-60.00", from_categories: [{ category: "food.restaurants", n: 3 }], blocked: [] } };
    await open();
    expect(await screen.findByTestId("effect")).toHaveTextContent("3 transactions");
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled());
  });
});

describe("whose transaction (E14-3)", () => {
  it("says who and why, reassigns by hand and can undo the reassignment", async () => {
    const why = { tx_key: "a:ref:1", person: "luca", source: "manual", rule: null, reason: "reassigned by hand",
      manual: { member: "luca", set_at: "2026-10-04T09:00:00+00:00", set_by: "ui:papa", note: null },
      rules: [{ id: "mia-card", member: "mia", matched: false, why_not: "the card ending 4242 is not printed on it" }],
      account: "Main", account_owner: "joint", account_owner_member: null, card_last4: null, history: [] };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      let body: unknown = {};
      if (url.endsWith("/session")) body = { csrf_token: "tok" };
      else if (url.startsWith("/api/v1/transactions/detail")) body = detail;
      else if (url.startsWith("/api/v1/transactions/attribution")) body = why;
      else if (url.startsWith("/api/v1/meta/filters")) body = { accounts: [], owners: [], members: [{ id: "luca", name: "Luca Rossi", role: "adult" }, { id: "mia", name: "Mia Rossi", role: "child" }], purposes: [], tags: [], sources: [], events: [], groups: [], today: "" };
      else if (url.startsWith("/api/v1/meta/taxonomy")) body = taxonomy;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    renderApp(<TxPanel txKey="a:ref:1" onClose={vi.fn()} />);
    const sec = await screen.findByRole("region", { name: "Whose transaction" });
    expect(sec).toHaveTextContent("Belongs to Luca Rossi");
    expect(sec).toHaveTextContent("you reassigned it");
    await userEvent.selectOptions(screen.getByLabelText("Reassign to"), "mia");
    await userEvent.click(screen.getByRole("button", { name: "Reassign" }));
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/v1/transactions/person") && !c.url.includes("clear") && String(c.init?.body).includes('"member":"mia"'))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Undo my reassignment" }));
    await waitFor(() => expect(calls.some((c) => c.url.startsWith("/api/v1/transactions/person/clear"))).toBe(true));
  });
});
