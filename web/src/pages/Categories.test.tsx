import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Categories from "./Categories";
import { resetCsrfForTests } from "@/lib/api";

const row = (category: string, avg: string, extra = {}) => ({ category, group: category.split(".")[0], monthly_avg: avg, monthly_avg_with_one_offs: avg, n_months: 6, low_confidence: false, lumpy: false, this_month: "0.00", last_month: "0.00", accounts: [], ...extra });
const overview = {
  as_of: "2026-10-04",
  categories: [row("housing.mortgage", "2000.00"), row("housing.energy", "100.00"), row("food.groceries", "500.00")],
  // the group figures are the analytics': deliberately NOT the sum of the leaves (2100 / 500), as in the real data
  groups: [
    { group: "housing", monthly_avg: "2050.50", n_months: 5, low_confidence: false, this_month: "0.00", last_month: "0.00" },
    { group: "food", monthly_avg: "480.00", n_months: 6, low_confidence: false, this_month: "0.00", last_month: "0.00" },
  ],
  unavailable: [], household_monthly_avg: "2530.50", household_months: ["2026-05", "2026-06"], coverage: { rule: "", months: [], n_months: 0, accounts: [], skipped_months: [], partial_current_month: null, notes: [] },
};

beforeEach(() => {
  resetCsrfForTests();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.startsWith("/api/v1/categories") ? overview : {}), { status: 200, headers: { "Content-Type": "application/json" } })));
});
afterEach(() => vi.unstubAllGlobals());

const nb = (s: string | null) => (s ?? "").replace(/[  ]/g, " ");

describe("categories page shows the analytics' group figures, never its own sums", () => {
  it("displays each group's usual month from the API, and the household figure as given", async () => {
    renderApp(<Categories />);
    const housing = (await screen.findByRole("link", { name: "Housing" })).closest("section")!;
    expect(nb(housing.textContent)).toContain("2 051 €");            // 2050.50 from the API, not 2 100 (the sum of the leaves)
    expect(nb(housing.textContent)).not.toContain("2 100");
    expect(nb(screen.getByText(/A usual month, whole household/).parentElement!.textContent)).toContain("2 530,50 €");
  });

  it("filtering rows hides rows only: a group's figure does not change with the text filter", async () => {
    renderApp(<Categories />);
    await screen.findByRole("link", { name: "Housing" });
    await userEvent.type(screen.getByRole("searchbox", { name: /filter categories/i }), "energy");
    const housing = screen.getByRole("link", { name: "Housing" }).closest("section")!;
    expect(within(housing).queryByText("Mortgage")).not.toBeInTheDocument();
    expect(within(housing).getByText("Electricity and gas")).toBeInTheDocument();          // the id matches too
    expect(nb(housing.textContent)).toContain("2 051 €");
    expect(screen.queryByRole("link", { name: "Food" })).not.toBeInTheDocument();      // a group with no matching row disappears
  });
});
