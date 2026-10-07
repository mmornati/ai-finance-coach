import { afterEach, beforeAll, describe, expect, it } from "vitest";
import i18n, { setLanguage } from "@/i18n";
import { ApiError } from "@/lib/api";
import { errorText, flagLabel, forecastLabel, formatParams, holdingKindLabel, purposeLabel, serverLabel, tServer } from "./server";

const nb = (s: string) => s.replace(/[  ]/g, " ");

// a sentence with one param of every type, added to the English bundle for these tests only
beforeAll(() => {
  i18n.addResource("en", "server", "test.everything", "{{count}} on {{start_date}} in {{start_month}}: {{spent_amount}} ({{change_pct}}) of {{top_category}} in {{top_group}}, by {{who}}");
});
afterEach(() => setLanguage("en", { persist: false }));

describe("server text: code + params, the English text as fallback", () => {
  it("translates a known code and formats each param from its name", () => {
    const msg = { code: "test.everything", params: { count: 2, start_date: "2026-09-27", start_month: "2026-09", spent_amount: "-1234.50", change_pct: 0.12, top_category: "food.groceries", top_group: "personal_care", who: "adult-1" }, text: "English" };
    expect(nb(tServer(msg))).toBe("2 on 27 sept. 2026 in septembre 2026: -1 234,50 € (12 %) of Groceries in Personal care, by adult-1");
  });

  it("shows the English text when the code is unknown, or the fallback when there is no message", () => {
    expect(tServer({ code: "nothing.here", params: { count: 1 }, text: "The server's own sentence." })).toBe("The server's own sentence.");
    expect(tServer(null, "fallback")).toBe("fallback");
    expect(tServer(undefined)).toBe("");
  });

  it("picks the plural form from count", () => {
    expect(tServer({ code: "balances.mixedTypes", params: { count: 1, accounts: "Main" }, text: "x" })).toContain("1 account only gives a non-booked balance (Main)");
    expect(tServer({ code: "balances.mixedTypes", params: { count: 3, accounts: "A, B, C" }, text: "x" })).toContain("3 accounts only give");
  });

  it("leaves null params visible as a dash and unknown names as they are", () => {
    expect(formatParams({ end_date: null, label: "Rent", n: 3 })).toEqual({ end_date: "–", label: "Rent", n: 3 });
  });

  it("follows the interface language", async () => {
    await setLanguage("fr", { persist: false });
    expect(tServer({ code: "balances.oneBalance", params: {}, text: "x" })).toBe("Un solde par compte, le type le plus comptabilisé que fournit la banque.");
    expect(serverLabel("alertKind", "unusual_charge", "Unusual charge")).toBe("Prélèvement inhabituel");
    await setLanguage("it", { persist: false });
    expect(serverLabel("balanceType", "ITAV", "available (interim)")).toBe("disponibile (provvisorio)");
  });
});

describe("labels of a fixed vocabulary", () => {
  it("translates a known code and falls back to the English label, then to the code", () => {
    expect(serverLabel("setupStep", "sync", "First sync (longest history)")).toBe("First sync (longest history)");
    expect(serverLabel("alertKind", "new_kind_from_a_newer_server", "Something new")).toBe("Something new");
    expect(serverLabel("alertKind", "new_kind_from_a_newer_server")).toBe("new_kind_from_a_newer_server");
    expect(serverLabel("subsGroup", null, null)).toBe("");
  });

  it("reads a flag with a value (code:value) and passes it as count", () => {
    expect(flagLabel("forecastFlag", "accounts_without_balance:1")).toBe("1 account without a balance left out");
    expect(flagLabel("forecastFlag", "accounts_without_balance:2")).toBe("2 accounts without a balance left out");
    expect(flagLabel("forecastFlag", "at_risk")).toBe("could run short");
    expect(flagLabel("forecastFlag", "shares_source_with:b")).toBe("shares_source_with:b");
  });

  it("names the household line of the forecast, purposes and memory kinds", async () => {
    expect(forecastLabel({ account: null, label: "household" })).toBe("Household");
    expect(forecastLabel({ account: "a1", label: "Main account" })).toBe("Main account");
    expect(purposeLabel("kids")).toBe("Kids");
    expect(purposeLabel("travel_fund")).toBe("Travel fund");
    expect(holdingKindLabel("employee_savings_plan")).toBe("Employee savings plan");
    expect(holdingKindLabel("car_loan")).toBe("Car loan");
    expect(holdingKindLabel("insurance_home")).toBe("Home insurance");
    expect(holdingKindLabel("boat")).toBe("Boat");
    await setLanguage("fr", { persist: false });
    expect(forecastLabel({ account: null, label: "household" })).toBe("Foyer");
    expect(purposeLabel("savings")).toBe("Épargne");
  });
});

describe("API errors by code", () => {
  it("translates the generic codes and keeps the server's message for the others", async () => {
    expect(errorText(new ApiError(403, "csrf", "missing or wrong CSRF token (reload the page)"))).toBe("The page is out of date: reload it and try again.");
    expect(errorText(new ApiError(409, "rejected", "the account 'x' is already linked"))).toBe("the account 'x' is already linked");
    expect(errorText(new ApiError(500, "error", ""), "Something went wrong")).toBe("Something went wrong");
    expect(errorText(new Error("network down"))).toBe("network down");
    expect(errorText("weird", "fallback")).toBe("fallback");
    await setLanguage("it", { persist: false });
    expect(errorText(new ApiError(429, "rate_limited", "too many changes in a short time: wait a moment"))).toBe("Troppi tentativi in poco tempo: aspetta un momento e riprova.");
  });
});
