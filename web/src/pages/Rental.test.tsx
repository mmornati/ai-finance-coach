import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Rental from "./Rental";
import { resetCsrfForTests } from "@/lib/api";
import { setLanguage } from "@/i18n";

// every name, figure and id below is invented
const vacancy = (o: object = {}) => ({ missing_months: ["2026-08"], declared_months: [], late_paid_months: [], unknown_months: [], n_missing: 1, n_declared: 0, months_since_let: 6, occupancy_rate: 0.8333, ...o });
const month = (m: string, o: object = {}) => ({
  month: m, rent: "620.00", loan: "900.00", charges: "35.00", fees: "0.00", taxes: "0.00", insurance: "0.00", works: "0.00", other: "0.00", costs: "935.00", net: "-315.00", effort: "315.00",
  owner_in: "315.00", owner_out: "0.00", complete: true, rent_status: "received", expected_rent: "620.00", rent_evidence: [], n_tx: 3, ...o,
});
const scheme = (o: object = {}) => ({
  declared: true, scheme: "pinel", start_date: "2021-03-05", years: 9, end_date: "2030-03-04", end_source: "start + years", effective_end_date: "2030-03-04", state: "active", progress_pct: 62.1, days_left: 1247, months_left: 41,
  decision_needed: false, next_reminder_date: "2029-03-04", reminders: [{ months_before: 12, date: "2029-03-04" }, { months_before: 6, date: "2029-09-04" }],
  extension: { decision: "undecided" }, rent_cap: { cap: "600.00", cap_basis: "the monthly cap you declared", rent: "620.00", rent_basis: "the lease rent you declared", status: "above_cap", gap: "20.00" },
  tenant_income: { limit: "30000.00", tenant_income: "28000.00", status: "within_limit", headroom: "2000.00" },
  missing: [{ field: "commitment.reduction_rate_pct", needed_for: "the scheme reduction candidate of the tax return" }], ...o,
});
const row = (o: object = {}) => ({
  id: "rental-flat-1", account_link: "declared", loan_link: "declared", n_loans: 1, expected_rent: "620.00", rent_source: "declared",
  last_month: { month: "2026-09", rent: "620.00", net: "-315.00", effort: "315.00", complete: true, rent_status: "received" }, vacancy: vacancy(),
  scheme: { declared: true, scheme: "pinel", state: "active", end_date: "2030-03-04", effective_end_date: "2030-03-04", months_left: 41, decision_needed: false }, net_equity: "74358.15", missing: ["commitment.reduction_rate_pct"], ...o,
});
const detail = (o: object = {}) => ({
  id: "rental-flat-1", kind: "rental property", links: { account: "declared", accounts: 1, loan: "declared", loans: 1, notes: [] },
  cashflow: { months: [month("2026-07"), month("2026-08", { rent: "0.00", net: "-935.00", effort: "935.00", rent_status: "missing" }), month("2026-09")],
    rent: { expected: "620.00", source: "declared", first_month: "2026-03", usual_day: 3 }, n_months: 3, n_complete: 3, average: { rent: "413.33", costs: "935.00", net: "-521.67", effort: "521.67" }, vacancy: vacancy() },
  current_month: { month: "2026-10", rent: "0.00", expected: "620.00", status: "pending" },
  pnl: { year: 2026, totals: { rent: "3720.00", loan: "8100.00", charges: "315.00", fees: "0.00", taxes: "800.00", insurance: "0.00", works: "0.00", other: "0.00", costs: "9215.00", net: "-5495.00", effort: "5495.00", owner_in: "5500.00", owner_out: "0.00" },
    n_months: 9, months_expected: 9, months_incomplete: [], months_missing_data: [], year_in_progress: true, complete: false, monthly_average_effort: "610.56", vacancy: vacancy(),
    loan_split: { interest: "4357.09", insurance: "0.00", principal: "6679.67", partial: false, source: "amortization schedule" }, economic: { result: "-512.09", note: "x" } },
  scheme: scheme(), years: [2026, 2025], flows_to_label: 2, tag: "property-rental-flat-1",
  asset: { id: "rental-flat-1", kind: "real_estate_rental", account: "Rent account", loan: "loan-1", scheme: "pinel", value: 200000, as_of: "2026-09-01", rent_monthly: 620, purchase_price: 190000, purchase_date: "2021-02-01",
    commitment: { start_date: "2021-03-05", years: 9 }, market_rate: null, vacancies: [] }, ...o,
});
const tax = (o: object = {}) => ({
  year: 2026, country: "FR", status: "computed", disclaimer: "Not tax advice: verify on impots.gouv.fr. Nothing is filed by the coach.", gross_rents: "3720.00", months_counted: 9, months_incomplete: [], months_missing_data: [], complete: false,
  micro_foncier: { gross_rents: "3720.00", household_gross_rents: "3720.00", ceiling: "15000.00", within_ceiling: true, abatement_pct: 30, abatement: "1116.00", taxable: "2604.00" },
  reel: { gross_rents: "3720.00", deductible: [{ item: "property tax", amount: "800.00", source: "bank flows", bound: "upper bound: the household-waste part is not deductible" }, { item: "loan interest", amount: "4357.09", source: "schedule", bound: "theoretical table" }], total_deductible: "5157.09", net: "-1437.09", unknown: [], not_deductible: [] },
  difference: "-4041.09", lower_taxable_candidate: "reel", scheme_reduction: { status: "computed", base: "190000.00", total: "34200.00", annual: "3800.00", candidate: "3800.00", rate_pct: 18, years: 9, first_year: 2021, last_year: 2029, in_window: true, notes: ["spread evenly"] },
  documents: [{ id: "leases", item: "The lease(s) of the year", from: "your files" }, { id: "loan-statement", item: "The lender's annual statement of interest", from: "your lender" }], notes: ["candidates computed from the bank flows"], ...o,
});
const indicators = (o: object = {}) => ({
  as_of: "2026-10-04", disclaimer: "Estimate from the figures you gave, not an offer.", trailing_effort: "521.67", signals: [{ id: "commitment_running", reading: "the commitment is running: ending it early can call the advantage into question" }],
  scenarios: ["`coach loans scenario renegotiate <loan> --new-rate R`"], market_rate: { status: "missing", note: "enter the current market rate" },
  loan_rate: { status: "unknown", loan_rate_pct: 3.4, missing: ["a market rate you entered"] },
  commitment: { state: "active", start_date: "2021-03-05", years: 9, end_date: "2030-03-04", effective_end_date: "2030-03-04", months_left: 41, decision_needed: false, extension: { decision: "undecided" } },
  equity: { status: "computed", value: "200000.00", outstanding: "125641.85", net_equity: "74358.15", equity_share_pct: 37.2 }, ...o,
});

let calls: { url: string; init?: RequestInit }[];
let list: object;
let det: object;
let disc: object | undefined;
let taxBody: object | undefined;
let indBody: object | undefined;
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  disc = undefined;
  taxBody = undefined;
  indBody = undefined;
  list = { as_of: "2026-10-04", properties: [row()], unlinked_rental_accounts: [], property_categories: [] };
  det = detail();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const path = url.split("?")[0];
    let body: unknown = {};
    if (path.endsWith("/session")) body = { csrf_token: "tok" };
    else if (path === "/api/v1/meta/disclaimers") body = disc ?? {};
    else if (path === "/api/v1/rental/properties") body = list;
    else if (path === "/api/v1/rental/rental-flat-1/tax") body = taxBody ?? tax();
    else if (path === "/api/v1/rental/rental-flat-1/indicators" && indBody) body = indBody;
    else if (path === "/api/v1/rental/rental-flat-1/indicators") {
      const q = new URLSearchParams(url.split("?")[1] ?? "");
      body = q.get("market_rate")
        ? indicators({ market_rate: { status: "given", rate_pct: 2.6, date: "2026-09-20", basis: "given for this call", age_days: 14 },
          loan_rate: { status: "above_market", loan_rate_pct: 3.4, market_rate_pct: 2.6, gap_pts: 0.8, reading: "the loan rate is 0.80 point(s) above the market rate you entered: a quote may be worth asking for.",
            renegotiation: { status: "computed", current_payment: "919.74", new_payment: "871.63", total_costs: "2135.91", penalty: "2135.91", net_saving: "6187.38", break_even_months: 45 }, renegotiation_note: "no fee was given" } })
        : indicators();
    } else if (path.endsWith("/extension") || path.endsWith("/vacancy") || path.endsWith("/market-rate")) body = { dry_run: url.includes("dry_run=true"), changed: true, diff: "+    extension: {decision: extend}", change_id: "c1", warnings: [] };
    else if (path.startsWith("/api/v1/memory/assets/")) body = { dry_run: url.includes("dry_run=true"), changed: true, diff: "+  - id: rental-flat-9", change_id: "c2", warnings: [] };
    else if (path === "/api/v1/rental/rental-flat-1") body = det;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const writes = () => calls.filter((c) => c.init?.method && c.init.method !== "GET" && !c.url.includes("dry_run=true"));
const csrf = (c: { init?: RequestInit }) => (c.init!.headers as Record<string, string>)["X-CSRF-Token"];

describe("rental property: the cash flow and the P&L", () => {
  it("shows the effort d'epargne, the rent status of each month, the vacancy and the P&L with its sign", async () => {
    renderApp(<Rental />);
    expect(await screen.findByText("Last twelve closed months")).toBeInTheDocument();
    expect(screen.getByText(/Average monthly effort d'épargne/)).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: "Rent and costs of the property per month" })).toBeInTheDocument();
    const table = screen.getAllByRole("table")[0];
    expect(within(table).getByText("no rent")).toBeInTheDocument();
    expect(within(table).getAllByText("rent received").length).toBe(2);
    const vac = screen.getByText("Vacancy").closest("section")!;
    expect(within(vac).getByText("no rent")).toBeInTheDocument();
    expect(screen.getByText("P&L 2026")).toBeInTheDocument();
    expect(screen.getByText(/Result if the principal repaid is not a cost/)).toBeInTheDocument();
    expect(screen.getByText(/2 flow\(s\) of the property account are in no property category/)).toBeInTheDocument();
    expect(screen.getByText("pinel", { exact: false })).toBeInTheDocument();
  });

  it("explains an unlinked account instead of showing numbers", async () => {
    det = detail({ links: { account: "none", accounts: 0, loan: "none", loans: 0, notes: [] }, cashflow: { months: [], rent: { expected: null, source: "unknown", first_month: null, usual_day: null }, n_months: 0, n_complete: 0, vacancy: vacancy({ n_missing: 0, missing_months: [] }) } });
    renderApp(<Rental />);
    expect(await screen.findByText("No closed month of data for this property yet")).toBeInTheDocument();
    expect(screen.getByText(/account: not linked/)).toBeInTheDocument();
  });

  it("declares a vacancy with a preview before it is written", async () => {
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: "Declare a vacancy" }));
    await userEvent.type(screen.getByLabelText(/^From/), "2026-08-01");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/rental/rental-flat-1/vacancy") && c.url.includes("dry_run=true"))).toBe(true));
    expect(writes().filter((c) => c.url.includes("/vacancy"))).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes().filter((c) => c.url.includes("/vacancy"))).toHaveLength(1));
    const w = writes().find((c) => c.url.includes("/vacancy"))!;
    expect(csrf(w)).toBe("tok");
    expect(JSON.parse(String(w.init!.body))).toMatchObject({ start: "2026-08-01" });
  });
});

describe("rental property: the scheme commitment", () => {
  it("shows the dates, the progress, the reminders, the cap and income checks and the missing facts", async () => {
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Scheme commitment/ }));
    expect(await screen.findByText("pinel: commitment")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Share of the commitment elapsed" })).toBeInTheDocument();
    expect(screen.getByText("above the cap")).toBeInTheDocument();
    expect(screen.getByText("within the limit")).toBeInTheDocument();
    expect(screen.getByText(/12 months before/)).toBeInTheDocument();
    expect(screen.getByText("commitment.reduction_rate_pct")).toBeInTheDocument();
    expect(screen.getByText(/Facts the coach does not know/)).toBeInTheDocument();
  });

  it("records the extension decision (previewed first) and flags a pending decision on the tab", async () => {
    list = { as_of: "2026-10-04", properties: [row({ scheme: { declared: true, scheme: "pinel", state: "active", end_date: "2027-03-04", effective_end_date: "2027-03-04", months_left: 5, decision_needed: true } })], unlinked_rental_accounts: [], property_categories: [] };
    det = detail({ scheme: scheme({ decision_needed: true, months_left: 5, days_left: 150 }) });
    renderApp(<Rental />);
    expect(await screen.findByText("decide")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Scheme commitment/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Record the decision" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/extension") && c.url.includes("dry_run=true"))).toBe(true));
    expect(writes().filter((c) => c.url.includes("/extension"))).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes().filter((c) => c.url.includes("/extension"))).toHaveLength(1));
    expect(JSON.parse(String(writes().find((c) => c.url.includes("/extension"))!.init!.body))).toMatchObject({ decision: "extend", years: 3 });
  });

  it("asks to record the scheme when there is none", async () => {
    det = detail({ scheme: scheme({ declared: false, scheme: null, start_date: null, years: null, end_date: null, state: "unknown", missing: [] }) });
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Scheme commitment/ }));
    expect(await screen.findByText("No scheme recorded for this property")).toBeInTheDocument();
    expect(screen.getByText(/nothing is looked up/)).toBeInTheDocument();
  });
});

describe("rental property: the tax year", () => {
  it("shows both candidates, the lower one, the scheme reduction and the documents, with the disclaimer", async () => {
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Tax year/ }));
    expect(await screen.findByText("Micro-foncier")).toBeInTheDocument();
    expect(screen.getByText("Real regime")).toBeInTheDocument();
    expect(screen.getByText(/not a return, nothing is filed, not tax advice/i)).toBeInTheDocument();
    expect(screen.getByText(/Not tax advice: verify on impots.gouv.fr/)).toBeInTheDocument();
    expect(screen.getByText(/real regime/)).toBeInTheDocument();
    expect(screen.getByText("Scheme reduction (candidate)")).toBeInTheDocument();
    const box = screen.getByRole("checkbox", { name: /The lease\(s\) of the year/ });
    await userEvent.click(box);
    expect(box).toBeChecked();
  });
});

describe("rental property: renegotiate or sell", () => {
  it("shows the equity and the commitment, then the loan rate against the market rate the user typed (nothing is looked up)", async () => {
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Renegotiate or sell/ }));
    expect((await screen.findAllByText("Net equity")).length).toBeGreaterThan(0);
    expect(screen.getByText(/the commitment is running/)).toBeInTheDocument();
    expect(screen.getByText(/enter the current market rate/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Market rate (%)"), "2.6");
    expect(await screen.findByText(/0.80 point\(s\) above the market rate you entered/)).toBeInTheDocument();
    expect(await screen.findByText("Renegotiation on the real schedule")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/indicators") && c.url.includes("market_rate=2.6"))).toBe(true);
    expect(screen.getByRole("button", { name: "Remember this rate" })).toBeDisabled();
  });
});

describe("rental property: declaring one", () => {
  it("explains the empty state and previews the new property before writing it", async () => {
    list = { as_of: "2026-10-04", properties: [], unlinked_rental_accounts: [{ uid: "a1", label: "Rent account" }], property_categories: [] };
    renderApp(<Rental />);
    expect(await screen.findByText("No rental property yet")).toBeInTheDocument();
    expect(screen.getByText(/1 account\(s\) are already flagged/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Declare a property" })[0]);
    await userEvent.type(screen.getByLabelText(/^Id/), "rental-flat-9");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/memory/assets/rental-flat-9") && c.url.includes("dry_run=true"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Declare" }));
    await waitFor(() => expect(writes().filter((c) => c.url.includes("/memory/assets/rental-flat-9"))).toHaveLength(1));
    const w = writes().find((c) => c.url.includes("/memory/assets/rental-flat-9"))!;
    expect(w.init!.method).toBe("PUT");
    expect(JSON.parse(String(w.init!.body)).fields).toMatchObject({ kind: "real_estate_rental" });
  });

  it("writes only the facts that changed, with the nested commitment object", async () => {
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit facts" }));
    const years = screen.getByLabelText(/Commitment length/);
    await userEvent.clear(years);
    await userEvent.type(years, "12");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/memory/assets/rental-flat-1") && c.url.includes("dry_run=true"))).toBe(true));
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes().filter((c) => c.url.includes("/memory/assets/rental-flat-1"))).toHaveLength(1));
    expect(JSON.parse(String(writes().find((c) => c.url.includes("/memory/assets/"))!.init!.body)).fields).toEqual({ commitment: { years: 12 } });
  });
});

describe("rental property: translations", () => {
  it("speaks Italian and keeps the French terms of art", async () => {
    await setLanguage("it");
    renderApp(<Rental />);
    expect(await screen.findByRole("heading", { name: "Immobile in affitto" })).toBeInTheDocument();
    expect(await screen.findByText("Ultimi dodici mesi chiusi")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Anno fiscale/ }));
    expect(await screen.findByText("Micro-foncier")).toBeInTheDocument();
    expect(screen.getByText("Regime réel")).toBeInTheDocument();
  });

  // the server's sentences come as codes + params (server.json); the legal texts by KEY, their wording from GET /meta/disclaimers (invented here)
  const msg = (code: string, text: string, params: object = {}) => ({ code, params, text });
  const frDisclaimers = { lang: "fr", texts: { ai_label: "x", ai_label_short: "x", general_advice: "AVIS-GENERAL-FR", loan: "AVIS-PRET-FR", tax: "AVIS-FISCAL-FR" } };

  it("translates the tax year's lines, notes and documents, and shows the tax disclaimer of the interface language", async () => {
    await setLanguage("fr");
    disc = frDisclaimers;
    taxBody = tax({
      disclaimer_key: "tax",
      reel: { gross_rents: "3720.00", deductible: [{ item: "property tax", amount: "800.00", source: "bank flows (housing.property_tax)", bound: "upper bound: x",
        item_msg: msg("rental.tax.item.propertyTax", "property tax"), source_msg: msg("rental.tax.source.bankFlows", "bank flows (housing.property_tax)", { categories: "housing.property_tax" }),
        bound_msg: msg("rental.tax.bound.propertyTax", "upper bound: x") }], total_deductible: "800.00", net: "2920.00", unknown: [], not_deductible: [] },
      notes: ["2026 is not over: only its closed months are counted"], notes_msg: [msg("rental.tax.note.yearInProgress", "2026 is not over: only its closed months are counted", { year: 2026 })],
    });
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Année fiscale/ }));
    expect(await screen.findByText(/AVIS-FISCAL-FR/)).toBeInTheDocument();
    expect(screen.queryByText(/Not tax advice/)).not.toBeInTheDocument();                       // the English fixed text is not shown
    expect(screen.getByText(/^− Taxe foncière/)).toBeInTheDocument();
    expect(screen.getByText(/la taxe d'enlèvement des ordures ménagères/)).toBeInTheDocument();
    expect(screen.getByText("2026 n'est pas terminée : seuls ses mois clos sont comptés.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Le ou les baux de l'année/ })).toBeInTheDocument();   // the document by its id
  });

  it("translates the readings and adds the general-advice line and the loan disclaimer in the interface language", async () => {
    await setLanguage("fr");
    disc = frDisclaimers;
    const ending = "the commitment ends in about 5 month(s) (2027-03-04): extend ...";
    indBody = indicators({
      disclaimer_key: "loan",
      signals: [{ id: "commitment_ending", reading: `${ending} This is general information, not financial advice.`,
        reading_msg: { ...msg("rental.signal.commitmentEnding", ending, { count: 5, end_date: "2027-03-04" }), disclaimer: "general_advice" } }],
      market_rate: { status: "missing", note: "enter the current market rate", note_msg: msg("rental.market.missing", "enter the current market rate") },
      scenarios: ["x"], scenarios_msg: [msg("rental.scenario.prepay", "x")],
    });
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Renégocier ou vendre/ }));
    expect(await screen.findByText(/^L'engagement se termine dans environ 5 mois .* AVIS-GENERAL-FR$/)).toBeInTheDocument();
    expect(screen.getByText(/^Saisissez le taux actuel du marché/)).toBeInTheDocument();
    expect(screen.getByText(/un remboursement anticipé sur l'échéancier exact/)).toBeInTheDocument();
    expect(screen.getByText("AVIS-PRET-FR")).toBeInTheDocument();
    expect(screen.queryByText(/not financial advice/)).not.toBeInTheDocument();
  });

  it("keeps the English reading, with its general-advice line, until the disclaimers are known", async () => {
    const t = "the loan rate is 0.80 point(s) above the market rate you entered: a quote may be worth asking for.";
    indBody = indicators({ signals: [{ id: "rate_above_market", reading: `${t} This is general information, not financial advice.`,
      reading_msg: { ...msg("rental.rate.above", t, { gap_num: "0.80" }), disclaimer: "general_advice" } }] });
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Renegotiate or sell/ }));
    expect(await screen.findByText(/a quote may be worth asking for\. This is general information, not financial advice\./)).toBeInTheDocument();
  });

  it("translates the missing facts of the scheme", async () => {
    await setLanguage("it");
    det = detail({ scheme: scheme({ missing: [{ field: "commitment.reduction_rate_pct", needed_for: "the scheme reduction candidate of the tax return",
      needed_for_msg: msg("rental.need.reductionRate", "the scheme reduction candidate of the tax return") }] }) });
    renderApp(<Rental />);
    await userEvent.click(await screen.findByRole("button", { name: /Vincolo dell'agevolazione/ }));
    expect(await screen.findByText("per l'importo candidato della riduzione d'imposta dell'agevolazione")).toBeInTheDocument();
  });
});
