import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Transactions, { TxTable } from "./Transactions";
import { resetCsrfForTests } from "@/lib/api";
import type { TxItem } from "@/api/types";

const tx = (o: Partial<TxItem>): TxItem => ({
  tx_key: "a:ref:1", date: "2026-10-03", amount: "-29.54", category: "shopping.marketplace", source: "llm", tags: [], event: null, entity: "Whatnot",
  merchant: "Whatnot", description: "CARTE WHATNOT", account: "a", account_label: "Main", owner: "joint", purpose: "main", type: "card", split: false,
  overridden: false, transfer_linked: false, ...o,
});

describe("TxTable", () => {
  it("groups by day, signs and colours amounts, flags tags and transfers", async () => {
    const open = vi.fn();
    renderApp(<TxTable onOpen={open} items={[tx({}), tx({ tx_key: "b", amount: "1500.00", entity: "Employer", category: "income.salary", date: "2026-10-03" }), tx({ tx_key: "c", date: "2026-10-02", tags: ["one_off"], transfer_linked: true, category: "transfer.internal" })]} />);
    expect(screen.getAllByText(/oct/i).length).toBeGreaterThanOrEqual(2); // one header per day, not per row
    const rows = screen.getAllByRole("button");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText(/29,54/).className).toMatch(/text-neg/);
    expect(within(rows[1]).getByText(/\+1\s?500,00/).className).toMatch(/text-pos/);
    expect(within(rows[2]).getByText("one_off")).toBeInTheDocument();
    expect(within(rows[2]).getByText("transfer")).toBeInTheDocument();
    await userEvent.click(rows[0]);
    expect(open).toHaveBeenCalledWith("a:ref:1");
  });
});

describe("Transactions page", () => {
  let urls: string[];
  beforeEach(() => {
    resetCsrfForTests();
    urls = [];
    vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} unobserve() {} });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      const body = url.includes("/transactions")
        ? { items: [tx({})], totals: { count: 1, sum: "-29.54", income: "0.00", outflow: "-29.54" }, limit: 60, offset: 0, total: 1, next_offset: null }
        : url.includes("/meta/filters") ? { accounts: [], owners: [], members: [], purposes: [], tags: [], sources: [], events: [], groups: [], today: "2026-10-04" }
        : url.includes("/meta/taxonomy") ? { groups: [] } : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the totals of the filtered set and passes the filters from the URL to the API", async () => {
    renderApp(<Transactions />, "/transactions?category=food.groceries&date_from=2026-01-01&tag=one_off");
    await screen.findByText("Whatnot");
    expect(screen.getByText("Matching").parentElement).toHaveTextContent("1");
    const call = urls.find((u) => u.startsWith("/api/v1/transactions?"))!;
    const p = new URL(call, "http://x").searchParams;
    expect(p.get("category")).toBe("food.groceries");
    expect(p.get("date_from")).toBe("2026-01-01");
    expect(p.get("tag")).toBe("one_off");
    expect(p.get("limit")).toBe("60");
    expect(screen.getByRole("button", { name: /export csv/i })).toBeEnabled();
  });

  it("searches after a pause and sends the text", async () => {
    renderApp(<Transactions />, "/transactions");
    await screen.findByText("Whatnot");
    await userEvent.type(screen.getByRole("searchbox", { name: /search transactions/i }), "what");
    await waitFor(() => expect(urls.some((u) => u.includes("q=what"))).toBe(true), { timeout: 2000 });
  });
});
