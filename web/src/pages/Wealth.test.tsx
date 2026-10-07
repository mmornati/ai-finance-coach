import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Wealth from "./Wealth";
import { resetCsrfForTests } from "@/lib/api";
import { setLanguage } from "@/i18n";
import type { Liability, LoanDetail, NetWorth, NetWorthPoint, ScheduleRow } from "@/api/types";

const schedule = (o: object = {}) => ({
  status: "computed", mode: "from_principal", approximate: false, payment: "1276.58", remaining_capital: "177049.99", next_due: "2026-11-01", first_due: "2020-02-01", last_due: "2040-01-01",
  remaining_instalments: 159, payments_made: 81, term_instalments: 240, total_interest: "56379.20", total_insurance: "0.00", total_cost: "56379.20", interest_paid: "29001.10", remaining_interest: "27378.10",
  assumptions: ["annuity loan, interest = capital x nominal rate / 12 per instalment"], by_year: [
    { year: 2025, instalments: 12, interest: "4000.10", principal: "11300.86", insurance: "0.00", total: "15300.96", partial: false },
    { year: 2026, instalments: 12, interest: "3800.20", principal: "11500.76", insurance: "0.00", total: "15300.96", partial: false }], rows_count: 240, ...o,
});
const loan = (o: Partial<Liability> = {}): Liability => ({
  id: "home-loan", file: "liabilities/home-loan.yaml", kind: "mortgage", lender: "Homebank", asset: "family house", start_date: "2020-01-01", end_date: "2040-01-01", principal: "250000.00",
  outstanding: "180000.00", outstanding_as_of: "2026-01-15", outstanding_stale: false, rate: { type: "fixed", nominal: 2.1, taeg: null }, monthly_payment: "1500.00", insurance: null,
  debited_account: null, debited_account_label: "Main", payment_match: "^HOMEBANK", early_repayment_penalty: null, residual_value: null, mileage_limit_km: null, notes: "", missing: [], open_questions: [],
  payments: { series_id: "rec_1", last_date: "2026-09-03", next_expected: "2026-10-03", amount: "1500.00", status: "active" }, schedule: schedule() as any, remaining_capital: "177049.99",
  remaining_capital_source: "schedule", alerts: [], payments_seen: 12, lease: null, inferred: [], ...o,
});
const lease = loan({
  id: "evcar", kind: "loa", lender: "LeaseCo", asset: "EV", principal: null, outstanding: null, monthly_payment: "389.42", end_date: "2027-01-01", remaining_capital: null, remaining_capital_source: null,
  residual_value: "15000.00", mileage_limit_km: 25000, rate: null, schedule: { status: "not_applicable", by_year: [], alternative: "a lease has rents and a residual value" } as any, odometer: [{ date: "2026-01-01", km: 15000 }],
  lease: { id: "evcar", kind: "loa", missing: [], checklist: ["read the end-of-contract clauses"], end: { end_date: "2027-01-01", known: true, days_left: 88, ended: false, reminder_date: "2026-07-01", reminder_active: true, reminder_months: 6 },
    decision: { residual_value: "15000.00" }, mileage: { limit_km: 25000, excess_km_fee: "0.10", readings: 1, status: "over_limit", needs: [], latest: { date: "2026-01-01", km: 15000, age_days: 276 }, pace: { km_per_year: 15010, km_per_month: 1251, since: "2025-01-01" },
      projected_contract_km: 30000, excess_km: 5000, excess_cost: "500.00" } },
});
const stale = loan({ id: "old-loan", lender: "OldBank", remaining_capital: "3000.00", remaining_capital_source: "declared", outstanding: "3000.00", outstanding_stale: true,
  schedule: { status: "not_computable", missing: ["principal", "rate.nominal"], by_year: [] } as any, payments: null, payments_seen: 0,
  alerts: [{ id: "ins_1", type: "missed_payment", severity: "high", loan: "old-loan", title: "OldBank: expected payment of 2026-10-03 not seen", body: "The instalment due on 2026-10-03 was not found within 5 days.", date: "2026-10-03", amount: null, expected_date: "2026-10-03", expected_amount: null, evidence: [] }],
  inferred: [{ field: "rate.nominal", value: 7.95, confidence: "medium", method: "Newton's method", note: "nominal annual rate, excluding insurance" }], missing: ["interest rate"] });

const point = (month: string, net: string, complete: boolean, n: number): NetWorthPoint => ({
  month, as_of: `${month}-28`, source: complete ? "snapshot" : "backfill", net_worth: net, assets: "20000.00", liabilities: "5000.00", complete, n_unknown: n, unknown: n ? [{ type: "asset", id: "house", label: "house", reason: "no value recorded" }] : [],
  by_category: { cash: "10000.00", savings: "10000.00", investments: "0.00", real_estate: "0.00", vehicles: "0.00", other: "0.00" },
});
const networth: NetWorth = {
  as_of: "2026-10-04", net_worth: "-166349.99", complete: false, unknown: [{ kind: "asset", id: "family-house", label: "family-house", reason: "no value recorded" }], stale: [{ kind: "asset", id: "savings-book" }],
  bank: { total: "5700.00", accounts: [{ uid: "ce", label: "Main", bank: "CE", owner: "joint", purpose: "main", balance: "4200.00", as_of: "2026-10-04" }] }, assets: { total: "5000.00", items: [], connected_not_counted: 0 },
  liabilities: { total: "177049.99", items: [{ id: "home-loan", kind: "mortgage", lender: "Homebank", outstanding: "177049.99", as_of: null, stale: false, counted: true, source: "schedule" }, { id: "evcar", kind: "loa", lender: "LeaseCo", outstanding: null, as_of: null, stale: false, counted: false, excluded: true }] },
  by_category: { cash: "5700.00", savings: "5000.00", investments: "430000", real_estate: "0.00", vehicles: "0.00", other: "0.00", liabilities: "177049.99" },
  by_owner: { joint: { assets: "5700.00", liabilities: "0.00", net_worth: "5700.00", n_unknown: 0 }, unassigned: { assets: "605000.00", liabilities: "177049.99", net_worth: "427950.01", n_unknown: 2 } }, n_unknown: 1,
  history: [point("2026-08", "15000.00", false, 1), point("2026-09", "15500.00", false, 1), { ...point("2026-10", "16000.00", true, 0), newly_counted: [{ type: "asset", id: "pee", label: "employee savings plan", reason_before: "value only known from 2026-10-04" }], caveat: "1 account balance(s) are available / expected balances, not booked ones" }], note: "The total counts only what is known; 1 item(s) have no value and are NOT included, so the real figure differs.",
};
const detail = (l: Liability, rows: ScheduleRow[] = []): LoanDetail => ({ id: l.id, kind: l.kind, schedule: { ...(l.schedule as any), rows }, lease: l.lease, alerts: l.alerts,
  payments: { count: l.payments_seen, first: "2025-10-03", last: "2026-09-03", last_amount: "1500.00", median_amount: "1500.00", recent: [{ date: "2026-09-03", amount: "1500.00", account: "Main" }, { date: "2026-08-03", amount: "1500.00", account: "Main" }] },
  inference: { status: "inferred", fields: l.inferred, notes: ["the instalment may include the borrower insurance (not recorded)"] } });
const row = (k: number, made: boolean): ScheduleRow => ({ k, due: `2026-${String(k).padStart(2, "0")}-01`, kind: "regular", interest: "326.59", principal: "950.00", insurance: "0.00", payment: "1276.58", total: "1276.58", balance: "180000.00", made });

let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/net-worth/snapshot")) body = { net_worth: "-166349.99", n_unknown: 1, backfilled_months: 2, as_of: "2026-10-04" };
    else if (url.startsWith("/api/v1/net-worth")) body = networth;
    else if (url.startsWith("/api/v1/liabilities")) body = { as_of: "2026-10-04", liabilities: [loan(), lease, stale], totals: { outstanding_known: "180049.99", monthly_payments: "2200", n_unknown_outstanding: 1 }, note: "" };
    else if (url.startsWith("/api/v1/assets")) body = { as_of: "2026-10-04", assets: [], stale_after_months: 3 };
    else if (url.startsWith("/api/v1/loans/home-loan/scenario")) body = { scenario: JSON.parse(String(init?.body)).type, saved_insight: JSON.parse(String(init?.body)).save ? "cin_1" : null,
      result: JSON.parse(String(init?.body)).type === "prepay" ? { status: "computed", date: "2026-11-01", amount: "20000.00", capital_before: "177049.99", capital_after: "157049.99", remaining_instalments: 159, instalment: "1276.58", penalty: "210.00",
        penalty_basis: "IRA: the lower of six months of interest and 3 %", options: [
          { mode: "keep_payment", label: "keep the instalment, finish earlier", new_instalment: "1276.58", monthly_change: "0.00", months_saved: 21, new_last_instalment: "2038-04-01", interest_saved: "9000.00", insurance_saved: "0.00", penalty: "210.00", net_saving: "8790.00", break_even_months: 1, verdict: "saves money over the life of the loan" },
          { mode: "keep_term", label: "keep the end date, lower the instalment", new_instalment: "1132.00", monthly_change: "-144.58", months_saved: 0, new_last_instalment: "2040-01-01", interest_saved: "7000.00", insurance_saved: "0.00", penalty: "210.00", net_saving: "6790.00", break_even_months: 1, verdict: "saves money over the life of the loan" }], notes: [] } : { status: "computed" } };
    else if (url.startsWith("/api/v1/loans/home-loan/infer/propose")) body = { id: "p-20261004-abc123", status: "pending", accept_command: "uv run coach memory accept p-20261004-abc123" };
    else if (url.startsWith("/api/v1/loans/old-loan/infer/propose")) body = { id: "p-20261004-abc123", status: "pending", accept_command: "uv run coach memory accept p-20261004-abc123" };
    else if (url.startsWith("/api/v1/loans/evcar/odometer")) body = { dry_run: url.includes("dry_run=true"), changed: true, diff: "+  - {date: 2026-06-01, km: 21000}", change_id: "c1", warnings: [] };
    else if (url.startsWith("/api/v1/loans/home-loan")) body = detail(loan(), [row(9, true), row(10, true), row(11, false), row(12, false)]);
    else if (url.startsWith("/api/v1/loans/evcar")) body = detail(lease);
    else if (url.startsWith("/api/v1/loans/old-loan")) body = detail(stale);
    else if (url.startsWith("/api/v1/meta/filters")) body = { accounts: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const writes = () => calls.filter((c) => c.init?.method && c.init.method !== "GET" && !c.url.includes("dry_run=true"));
const csrf = (c: { init?: RequestInit }) => (c.init!.headers as Record<string, string>)["X-CSRF-Token"];

describe("net worth", () => {
  it("shows the known part with its breakdown by category and person, never a complete-looking figure", async () => {
    renderApp(<Wealth />);
    expect(await screen.findByText("Net worth, known part only")).toBeInTheDocument();
    expect(screen.getByText(/1 item not counted/)).toBeInTheDocument();
    expect(screen.getByText("By category")).toBeInTheDocument();
    expect(within(screen.getByText("By category").parentElement!).getByText("Investments")).toBeInTheDocument();
    expect(screen.getByText("By person")).toBeInTheDocument();
    expect(screen.getByText("Not assigned to a person")).toBeInTheDocument();
    expect(screen.getByText("(2 unknown)")).toBeInTheDocument();
    expect(screen.getByText(/family-house/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.startsWith("/api/v1/net-worth"))!.url).toContain("history=true");
  });

  it("charts the monthly history with the unknown months flagged, and lists it as a table", async () => {
    renderApp(<Wealth />);
    expect(await screen.findByRole("figure", { name: "Net worth by month" })).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 months are the known part only/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Table" }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("1 not counted")).toHaveLength(2);                 // August and September: the known part only
    expect(within(table).getByText("none")).toBeInTheDocument();
    expect(within(table).getAllByText("rebuilt")).toHaveLength(2);
    expect(within(table).getByText("recorded (new value counted)")).toBeInTheDocument();      // the month a value is first counted is marked, the jump is not wealth
    expect(screen.getByText(/dashed line marks the month a value is first counted/)).toBeInTheDocument();
    expect(screen.getByText(/can differ slightly from the bank's figure/)).toBeInTheDocument();
  });

  it("records a snapshot with the CSRF token", async () => {
    renderApp(<Wealth />);
    await userEvent.click(await screen.findByRole("button", { name: /record today/i }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toBe("/api/v1/net-worth/snapshot");
    expect(csrf(writes()[0])).toBe("tok");
  });
});

describe("loan cards", () => {
  it("shows where the capital comes from, the alerts and a lease reminder", async () => {
    renderApp(<Wealth />);
    const home = (await screen.findByRole("heading", { name: "Homebank" })).closest("section")!;
    expect(within(home).getByText("schedule")).toBeInTheDocument();
    expect(within(home).getByText(/next instalment/)).toBeInTheDocument();
    const old = screen.getByRole("heading", { name: "OldBank" }).closest("section")!;
    expect(within(old).getByText("declared")).toBeInTheDocument();
    expect(within(old).getByText("old")).toBeInTheDocument();
    expect(within(old).getByText(/expected payment of 2026-10-03 not seen/)).toBeInTheDocument();
    expect(within(old).getByText(/The coach can suggest rate.nominal from the payments/)).toBeInTheDocument();
    const car = screen.getByRole("heading", { name: "LeaseCo" }).closest("section")!;
    expect(within(car).getByText("none (a lease)")).toBeInTheDocument();
    expect(within(car).getByText("Ends in 88 days")).toBeInTheDocument();
    expect(within(car).getByText(/About 5.000 km over the limit|About 5,000 km over the limit|About 5\s?000 km over the limit/)).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });
});

describe("loan details", () => {
  it("shows the interest by calendar year and the instalments, and says what is missing when there is no schedule", async () => {
    renderApp(<Wealth />);
    await userEvent.click(await screen.findByRole("button", { name: "Details of loan home-loan" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Interest by calendar year")).toBeInTheDocument();
    expect(within(dialog).getByText("2026")).toBeInTheDocument();
    expect(within(dialog).getByText(/check it against the lender's annual statement/)).toBeInTheDocument();
    expect(within(dialog).getByText("Capital still due")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /All \d+/ }));
    expect(within(dialog).getAllByRole("row").length).toBeGreaterThan(4);
    await userEvent.click(within(dialog).getAllByRole("button", { name: "Close" }).at(-1)!);
    await userEvent.click(await screen.findByRole("button", { name: "Details of loan old-loan" }));
    const d2 = await screen.findByRole("dialog");
    expect(await within(d2).findByText("No schedule yet")).toBeInTheDocument();
    expect(within(d2).getByText("principal, rate.nominal")).toBeInTheDocument();
  });

  it("runs a prepayment scenario on the schedule, writes nothing, and stores it as an insight only on request", async () => {
    renderApp(<Wealth />);
    await userEvent.click(await screen.findByRole("button", { name: "Details of loan home-loan" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Scenarios" }));
    await userEvent.type(within(dialog).getByLabelText("Amount to repay (EUR)"), "20000");
    await userEvent.click(within(dialog).getByRole("button", { name: /calculate/i }));
    expect(await within(dialog).findByText("keep the instalment, finish earlier")).toBeInTheDocument();
    expect(within(dialog).getByText("keep the end date, lower the instalment")).toBeInTheDocument();
    expect(within(dialog).getByText("21")).toBeInTheDocument();                       // months saved
    const call = calls.find((c) => c.url === "/api/v1/loans/home-loan/scenario")!;
    expect(JSON.parse(call.init!.body as string)).toMatchObject({ type: "prepay", amount: 20000, save: false });
    expect(csrf(call)).toBe("tok");
    await userEvent.click(within(dialog).getByRole("button", { name: /store as an insight/i }));
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/v1/loans/home-loan/scenario")).toHaveLength(2));
    expect(JSON.parse(calls.filter((c) => c.url === "/api/v1/loans/home-loan/scenario")[1].init!.body as string).save).toBe(true);
  });

  it("presents inferred values as suggestions and only queues a proposal", async () => {
    renderApp(<Wealth />);
    await userEvent.click(await screen.findByRole("button", { name: "Details of loan old-loan" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: /Suggestions/ }));
    expect(within(dialog).getByText(/Inferred, not recorded/)).toBeInTheDocument();
    expect(within(dialog).getByText(/rate.nominal = 7.95/)).toBeInTheDocument();
    expect(within(dialog).getByText("inferred, medium confidence")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /queue as a memory proposal/i }));
    expect(await within(dialog).findByText(/uv run coach memory accept p-20261004-abc123/)).toBeInTheDocument();
    const w = writes();
    expect(w).toHaveLength(1);
    expect(w[0].url).toBe("/api/v1/loans/old-loan/infer/propose");
  });

  it("projects a lease's mileage and records an odometer reading after a preview", async () => {
    renderApp(<Wealth />);
    await userEvent.click(await screen.findByRole("button", { name: "Details of loan evcar" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Over the mileage limit/)).toBeInTheDocument();
    expect(within(dialog).getByText(/roughly/)).toBeInTheDocument();
    expect(within(dialog).getByText(/The 6-month reminder is active/)).toBeInTheDocument();
    expect(within(dialog).getByText("read the end-of-contract clauses")).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Kilometres"), "21000");
    expect(await within(dialog).findByText(/km: 21000/)).toBeInTheDocument();       // the preview diff
    expect(writes()).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: /save the reading/i }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toMatch(/^\/api\/v1\/loans\/evcar\/odometer/);
    expect(csrf(writes()[0])).toBe("tok");
  });
});

describe("translations", () => {
  it("speaks French", async () => {
    await setLanguage("fr");
    renderApp(<Wealth />);
    expect(await screen.findByRole("heading", { name: "Prêts et patrimoine" })).toBeInTheDocument();
    expect(await screen.findByText("Patrimoine net, partie connue seulement")).toBeInTheDocument();
    expect(screen.getByText("1 élément non compté")).toBeInTheDocument();
  });

  it("translates the server's sentences by their code and the loan fields by theirs, the English staying the fallback", async () => {
    const note_msg = { code: "netWorth.totalPartial", params: { count: 1 }, text: networth.note };
    const body = { ...networth, note_msg, unknown: [{ ...networth.unknown[0], reason_msg: { code: "netWorth.noValue", params: {}, text: "no value recorded" } }] };
    const old = { ...stale, missing_codes: ["rate"] };
    const base = (globalThis.fetch as unknown as { getMockImplementation: () => (u: string, i?: RequestInit) => Promise<Response> }).getMockImplementation();
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/v1/net-worth") && !url.includes("snapshot")) return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.startsWith("/api/v1/liabilities")) return new Response(JSON.stringify({ as_of: "2026-10-04", liabilities: [old], totals: { outstanding_known: "3000.00", monthly_payments: "0", n_unknown_outstanding: 0 }, note: "" }), { status: 200, headers: { "Content-Type": "application/json" } });
      return base(url, init);
    }));
    await setLanguage("fr");
    renderApp(<Wealth />);
    expect(await screen.findByText(/1 élément n'a pas de valeur et n'est PAS inclus/)).toBeInTheDocument();
    expect(screen.getByTitle("aucune valeur renseignée")).toBeInTheDocument();
    const card = (await screen.findByRole("heading", { name: "OldBank" })).closest("section")!;
    expect(within(card).getByText(/taux d'intérêt/)).toBeInTheDocument();
    await setLanguage("en");
  });
});
