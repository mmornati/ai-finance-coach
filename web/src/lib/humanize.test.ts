import { describe, expect, it } from "vitest";
import { humanize } from "./humanize";

const cats = new Set(["food.groceries", "subscriptions.software_cloud", "food.cafes_bars"]);
const nb = (s: string) => s.replace(/[  ]/g, " ");

describe("humanize server texts", () => {
  it("turns category ids, ISO months, dates and EUR amounts into reader-friendly text", () => {
    const t = humanize("Spending in subscriptions.software_cloud in 2026-09 is 356.97 EUR, versus a typical 25.98 EUR per month.", cats);
    expect(nb(t)).toBe("Spending in Software and cloud in septembre 2026 is 356,97 €, versus a typical 25,98 € per month.");
  });
  it("formats dates and leaves unknown dotted words alone", () => {
    const t = humanize("A payment on 2026-10-01 to shop.example.com of -1200.00 EUR", cats);
    expect(nb(t)).toBe("A payment on 1 oct. 2026 to shop.example.com of -1 200,00 €");
  });
  it("renders group targets and budget titles", () => {
    expect(humanize("Budget food.groceries: over", cats)).toBe("Budget Groceries: over");
    expect(humanize("Budget group:food", cats)).toBe("Budget all food");
  });
  it("never invents numbers: no EUR, no change", () => {
    expect(humanize("robust z-score 9.7, 22 earlier months", cats)).toBe("robust z-score 9.7, 22 earlier months");
  });
});
