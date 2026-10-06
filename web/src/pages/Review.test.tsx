import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Review from "./Review";
import { resetCsrfForTests } from "@/lib/api";

const items = [
  { key: "FRESH MARKET", name: "Fresh Market", category: "food.groceries", confidence: 0.5, source: "llm", reason: "low_confidence", n: 5, total: "-200.00", at_stake: "200.00", banks: ["Fortuneo"], accounts: ["Main"] },
  { key: "SC BRANDELIS", name: "Sc Brandelis", category: null, confidence: null, source: null, reason: "held_back_person_like", n: 2, total: "-30.00", at_stake: "30.00", banks: [], accounts: ["Main"] },
  { key: "WEIRD", name: "Weird", category: "other.uncategorized", confidence: 0.4, source: "knn", reason: "uncategorized", n: 1, total: "-9.00", at_stake: "9.00", banks: [], accounts: ["Main"] },
];
let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/review?")) body = { total: 3, items };
    else if (url.startsWith("/api/v1/meta/taxonomy")) body = { groups: [{ id: "food", categories: [{ id: "food.groceries", description: "" }, { id: "food.restaurants", description: "" }] }] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("review queue page", () => {
  it("lists merchants by stake with their current label and asks for the queue threshold it shows", async () => {
    renderApp(<Review />);
    expect(await screen.findByText("Fresh Market")).toBeInTheDocument();
    expect(screen.getByText(/May be a person/)).toBeInTheDocument();
    expect(screen.getAllByText(/currently/i, { selector: "p" })[0]).toHaveTextContent("Groceries");
    expect(calls.find((c) => c.url.startsWith("/api/v1/review?"))!.url).toContain("max_conf=0.7");
  });

  it("confirming sends the same threshold; Keep is not offered for a guessed or missing label", async () => {
    renderApp(<Review />);
    const card = (await screen.findByText("Fresh Market")).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /keep groceries/i }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/review/confirm")).toBe(true));
    const post = calls.find((c) => c.url === "/api/v1/review/confirm")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({ key: "FRESH MARKET", max_conf: 0.7 });
    expect((post.init!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
    expect(screen.getAllByRole("button", { name: /^keep/i })).toHaveLength(1);
    expect(screen.getByText(/guessed from a similar merchant/i)).toBeInTheDocument();
  });

  it("Set stays disabled until a category is chosen, then corrects the merchant", async () => {
    renderApp(<Review />);
    const card = (await screen.findByText("Sc Brandelis")).closest("section")!;
    const set = within(card).getByRole("button", { name: "Set" });
    expect(set).toBeDisabled();
    await within(card).findByRole("option", { name: "Restaurants" });
    await userEvent.selectOptions(within(card).getByRole("combobox"), "food.restaurants");
    expect(set).toBeEnabled();
    await userEvent.click(set);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/review/correct")).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url === "/api/v1/review/correct")!.init!.body as string)).toEqual({ key: "SC BRANDELIS", category: "food.restaurants" });
  });
});
