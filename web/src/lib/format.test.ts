import { describe, expect, it } from "vitest";
import { setLanguage } from "@/i18n";
import { addMonthsKey, allOfGroupLabel, catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney, tone } from "./format";
import { qs } from "./utils";

const nbsp = (s: string) => s.replace(/[  ]/g, " ");

describe("money", () => {
  it("parses decimal strings and rejects junk", () => {
    expect(parseMoney("-1234.50")).toBe(-1234.5);
    expect(parseMoney("0.00")).toBe(0);
    expect(parseMoney(null)).toBeNull();
    expect(parseMoney("")).toBeNull();
    expect(parseMoney("abc")).toBeNull();
    expect(parseMoney("NaN")).toBeNull();
  });
  it("formats EUR for fr-FR and en-GB", () => {
    expect(nbsp(fmtMoney("1234.5", { locale: "fr-FR" }))).toBe("1 234,50 €");
    expect(fmtMoney("1234.5", { locale: "en-GB" })).toBe("€1,234.50");
    expect(nbsp(fmtMoney("-12.3", { locale: "fr-FR" }))).toBe("-12,30 €");
  });
  it("signs, rounds and compacts on request", () => {
    expect(nbsp(fmtMoney("5", { signed: true, locale: "fr-FR" }))).toBe("+5,00 €");
    expect(nbsp(fmtMoney("0", { signed: true, locale: "fr-FR" }))).toBe("0,00 €");
    expect(nbsp(fmtMoney("1234.56", { round: true, locale: "fr-FR" }))).toBe("1 235 €");
    expect(nbsp(fmtMoney("12300", { compact: true, locale: "en-GB" }))).toMatch(/^€12\.3[kK]$/); // the ICU of Node 22 says "k", newer "K"
  });
  it("shows a dash for a missing amount, never 0", () => {
    expect(fmtMoney(null)).toBe("–");
    expect(fmtMoney("x")).toBe("–");
  });
  it("tones by sign", () => {
    expect([tone("1"), tone("-1"), tone("0.00"), tone(null)]).toEqual(["pos", "neg", "zero", "zero"]);
  });
});

describe("dates and percentages", () => {
  it("keeps a calendar date on its own day whatever the time zone", () => {
    expect(fmtDate("2026-10-04", "long", "en-GB")).toBe("4 October 2026");
    expect(fmtDate("2026-01-01", "dayMonth", "fr-FR")).toBe("1 janv.");
    expect(fmtDate(null)).toBe("–");
  });
  it("formats months", () => {
    expect(fmtMonth("2026-09", "long", "en-GB")).toBe("September 2026");
    expect(nbsp(fmtMonth("2026-09", "long", "fr-FR"))).toBe("septembre 2026");
  });
  it("adds months across years", () => {
    expect(addMonthsKey("2026-11", 3)).toBe("2027-02");
    expect(addMonthsKey("2026-01", -1)).toBe("2025-12");
  });
  it("formats ratios", () => {
    expect(nbsp(fmtPct(0.1234, 1, { locale: "fr-FR" }))).toBe("12,3 %");
    expect(fmtPct(0.05, 0, { signed: true, locale: "en-GB" })).toBe("+5%");
    expect(fmtPct(null)).toBe("–");
  });
});

describe("labels and query strings", () => {
  it("labels categories, keeping the group for income and transfers", () => {
    expect(catLabel("food.groceries")).toBe("Groceries");
    expect(catLabel("shopping.tobacco_press")).toBe("Tobacco and newsagents");
    expect(catLabel("transfer.internal")).toBe("Transfer: internal");
    expect(catLabel("income.salary")).toBe("Income: salary");
    expect(groupLabel("personal_care")).toBe("Personal care");
    expect(catLabel(null)).toBe("–");
  });
  it("a leaf or group the built-in taxonomy does not have keeps its id, title-cased", () => {
    expect(catLabel("food.street_market")).toBe("Street market");
    expect(catLabel("income.side_gig")).toBe("Income: side gig");
    expect(groupLabel("hobbies_extra")).toBe("Hobbies extra");
    expect(allOfGroupLabel("food")).toBe("all food");
  });
  it("names the categories in the interface language, at call time", async () => {
    await setLanguage("fr", { persist: false });
    expect(catLabel("food.groceries")).toBe("Courses");
    expect(catLabel("transfer.internal")).toBe("Virements : entre vos comptes");
    expect(groupLabel("housing")).toBe("Logement");
    expect(allOfGroupLabel("food")).toBe("toute la catégorie alimentation");
    expect(catLabel("food.street_market")).toBe("Street market");
    await setLanguage("it", { persist: false });
    expect(catLabel("food.restaurants")).toBe("Ristoranti");
    expect(catLabel("income.salary")).toBe("Entrate: stipendio");
    await setLanguage("en", { persist: false });
  });
  it("builds query strings: arrays repeat, empty values vanish", () => {
    expect(qs({ a: "x y", b: ["1", "2"], c: "", d: undefined, e: false, f: 0 })).toBe("?a=x+y&b=1&b=2&f=0");
    expect(qs({})).toBe("");
    expect(qs()).toBe("");
  });
});
