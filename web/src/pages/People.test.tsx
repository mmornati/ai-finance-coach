import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import { render } from "@testing-library/react";
import Household from "./Household";
import Kids from "./Kids";
import WhoPays from "./WhoPays";
import KidHome from "./KidHome";
import { App } from "@/App";
import { ScopeSwitch } from "@/components/Layout";
import { resetCsrfForTests } from "@/lib/api";

const members = [
  { id: "anna", name: "Anna Rossi", role: "adult" },
  { id: "luca", name: "Luca Rossi", role: "adult" },
  { id: "mia", name: "Mia Rossi", role: "child" },
];
const filters = { accounts: [], owners: ["joint", "mia"], members, purposes: ["main", "kids"], tags: [], sources: [], events: [], groups: [], today: "2026-10-04" };
const overview = {
  as_of: "2026-10-04",
  members: [
    { id: "anna", name: "Anna Rossi", role: "adult", birth_year: 1984, aliases: [], pocket_money: null, accounts: ["an"], attributed_transactions: 12 },
    { id: "mia", name: "Mia Rossi", role: "child", birth_year: 2012, aliases: [], pocket_money: null, accounts: ["rl"], attributed_transactions: 30 },
  ],
  accounts: [
    { uid: "rl", label: "Mia's account", bank: "Revolut", owner: "mia", owner_member: "mia", joint: false, owner_known: true, purpose: "kids", attributed: { mia: 30 } },
    { uid: "fo", label: "Cards", bank: "Fortuneo", owner: "joint", owner_member: null, joint: true, owner_known: true, purpose: "cards", attributed: { joint: 5, mia: 1 } },
    { uid: "xx", label: "Old account", bank: "Old Bank", owner: "Somebody", owner_member: null, joint: false, owner_known: false, purpose: null, attributed: { unassigned: 3 } },
  ],
  purposes: ["main", "cards", "rental", "kids", "savings"],
  attribution: { counts: {}, manual: 2 },
  rules: [{ id: "mia-card", member: "mia", match: { card_last4: "4242", account: "fo" } }],
  kid_budgets: [], allocations: [],
  users: [{ id: "mia-kid", role: "child", member_id: "mia", created_at: "2026-10-01", disabled: false, prefs: {} }],
  warnings: ["account Old account: owner 'Somebody' is not 'joint' or a declared member"],
};
const kid = {
  member: "mia", as_of: "2026-10-04", window: { months: ["2026-04", "2026-09"], from: "2026-04-01", to: "2026-09-30" },
  accounts: [{ account: "rl", label: "Mia's account", balance: "57.00", balance_as_of: "2026-10-04" }],
  balance: { current: "57.00", as_of: "2026-10-04", trend: [{ month: "2026-08", end_balance: "40.00" }, { month: "2026-09", end_balance: "62.00" }, { month: "2026-10", end_balance: "57.00" }], unknown: [], estimated: true },
  pocket_money: { series: [{ id: "pm_1", source: "anna", amount: "10.00", cadence: "monthly", count: 6, first: "2026-04-06", last: "2026-09-06", next_expected: "2026-10-06", day: 6 }], declared: null, total: "60.00", monthly_equivalent: "10.00" },
  extra_topups: { total: "55.00", count: 2, by_source: { luca: "25.00", unknown: "30.00" }, items: [{ date: "2026-09-14", amount: "25.00", source: "luca", linked: true, category: "transfer.internal" }, { date: "2026-08-20", amount: "30.00", source: "unknown", linked: false, category: "transfer.from_people" }] },
  inflow: { total: "115.00", pocket: "60.00", extra: "55.00" }, ratio: { pocket_share: 0.5217, extra_share: 0.4783, pocket_to_extra: 1.09 },
  spending: { total: "43.60", monthly_avg: "7.27", this_month_to_date: "5.00", by_category: [{ category: "leisure.hobbies", total: "21.90", n: 2, share: 0.5 }, { category: "food.restaurants", total: "7.70", n: 2, share: 0.18 }], by_month: [{ month: "2026-09", total: "43.60" }] },
  notes: [],
};
const budgetRows = [{ id: "mia-weekly", member: "mia", period: "weekly", category: null, group: null, limit: "20.00", spent: "5.00", remaining: "15.00", ratio: 0.25, status: "ok", period_start: "2026-09-28", period_end: "2026-10-04", days_left: 0, projected: "5.00", note: null }];
const allocation = {
  as_of: "2026-10-04", window: { months: ["2026-09"] },
  rules: [{ id: "housing", title: "Housing", method: "income", among: ["anna", "luca"], total: "1200.00", joint_paid: "1000.00", personal_paid: "200.00", unattributed: "0.00", n: 4, notes: [],
    members: [{ member: "anna", share_pct: 75, owed: "900.00", paid: "150.00", joint_share: "750.00", net: "50.00" }, { member: "luca", share_pct: 25, owed: "300.00", paid: "50.00", joint_share: "250.00", net: "-50.00" }] }],
  by_member: [{ member: "anna", owed: "900.00", paid: "150.00", net: "50.00" }, { member: "luca", owed: "300.00", paid: "50.00", net: "-50.00" }], notes: [],
};
const me = {
  member: "mia", name: "Mia Rossi",
  report: { ...kid, accounts: undefined, pocket_money: { ...kid.pocket_money, series: [{ ...kid.pocket_money.series[0], source: "a parent" }] }, extra_topups: { ...kid.extra_topups, by_source: { "a parent": "25.00", "someone else": "30.00" } } },
  budgets: budgetRows.map(({ member, ...b }) => ({ ...b, status: "over", ratio: 1.3, spent: "26.00", remaining: "0.00" })),
};

let calls: { method: string; url: string; body?: string }[];
function serve(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    const path = url.split("?")[0].replace("/api/v1", "");
    const hit = path === "/session" ? { csrf_token: "tok", user: routes.__user } : routes[path];
    return new Response(JSON.stringify(hit ?? {}), { status: hit === undefined && path !== "/session" ? 404 : 200, headers: { "Content-Type": "application/json" } });
  }));
}
beforeEach(() => { resetCsrfForTests(); calls = []; localStorage.clear(); });
afterEach(() => vi.unstubAllGlobals());

describe("household page", () => {
  it("shows members, account owners with the unknown one flagged, rules and logins, and edits an owner", async () => {
    serve({ "/household/overview": overview, "/meta/filters": filters, "/accounts/xx": { uid: "xx" } });
    renderApp(<Household />);
    expect((await screen.findAllByText("Anna Rossi")).length).toBeGreaterThan(0);
    expect(screen.getByText(/owner 'Somebody' is not 'joint' or a declared member/)).toBeInTheDocument();
    expect(screen.getByText(/card ending 4242/)).toBeInTheDocument();
    expect(screen.getByText("mia-kid")).toBeInTheDocument();
    expect(screen.getByText(/2 transaction\(s\) reassigned by hand/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Owner of Old account"), "joint");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/accounts/xx") && c.body?.includes('"owner":"joint"'))).toBe(true));
    // logins are created in a terminal only: the page offers the commands, never a form
    expect(screen.getByText(/uv run coach users add/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create login/i })).toBeNull();
  });

  it("previews a new rule (how many transactions it would attribute) before saving", async () => {
    serve({ "/household/overview": overview, "/meta/filters": filters, "/household/attribution/rules/skate": { dry_run: true, changed: true, diff: "+ id: skate", matches: 3, would_change: 3, warnings: [] } });
    renderApp(<Household />);
    await screen.findAllByText("Anna Rossi");
    await userEvent.click(screen.getByRole("button", { name: "Rule" }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.type(within(dlg).getByLabelText("Rule id"), "skate");
    await userEvent.type(within(dlg).getByLabelText("Merchant matches (regex)"), "^SKATE");
    expect(await within(dlg).findByText(/It matches/)).toHaveTextContent("3");
    expect(calls.some((c) => c.method === "PUT" && c.url.includes("dry_run=true"))).toBe(true);
    expect(calls.some((c) => c.method === "PUT" && !c.url.includes("dry_run"))).toBe(false);
  });
});

describe("kids' money page", () => {
  it("shows pocket money, extra top-ups with their source, spending and the budgets", async () => {
    serve({ "/household/kids": { as_of: "2026-10-04", children: [kid] }, "/household/kid-budgets": { as_of: "2026-10-04", budgets: budgetRows }, "/meta/filters": filters, "/meta/taxonomy": { groups: [] } });
    renderApp(<Kids />);
    expect(await screen.findByText(/monthly from Anna Rossi/)).toBeInTheDocument();
    expect(screen.getByText(/Luca Rossi/)).toBeInTheDocument();
    expect(screen.getByText("internal transfer")).toBeInTheDocument();
    expect(screen.getByText(/Unknown source/)).toBeInTheDocument();
    expect(screen.getByText(/52 % \/ 48 %|52 ?% \/ 48 ?%/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /regular pocket money/i })).toBeInTheDocument();
    expect(screen.getByText(/This week/)).toBeInTheDocument();
    expect(screen.getByText(/never sent outside this machine|nothing about a child is ever sent outside this machine/i)).toBeInTheDocument();
  });

  it("explains an empty household", async () => {
    serve({ "/household/kids": { as_of: "2026-10-04", children: [] }, "/household/kid-budgets": { as_of: "x", budgets: [] }, "/meta/filters": filters });
    renderApp(<Kids />);
    expect(await screen.findByText("No child declared")).toBeInTheDocument();
  });
});

describe("who pays what page", () => {
  it("shows the shares, the fair share, what each paid and the settlement with its sign", async () => {
    serve({ "/household/allocation": allocation, "/meta/filters": filters, "/household/overview": overview, "/meta/taxonomy": { groups: [] } });
    renderApp(<WhoPays />);
    expect(await screen.findByText("Housing")).toBeInTheDocument();
    expect(screen.getByText(/in proportion to income/)).toBeInTheDocument();
    expect(screen.getAllByText("Anna Rossi").length).toBeGreaterThan(0);
    expect(screen.getByText(/moves no money/)).toBeInTheDocument();
    expect(screen.getAllByText(/joint account/).length).toBeGreaterThan(0);
  });
});

describe("the person switch", () => {
  it("narrows every figure to one member through the member parameter", async () => {
    serve({ "/meta/filters": filters });
    renderApp(<ScopeSwitch />);
    await userEvent.click(screen.getByRole("button", { name: /Household/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Person"), "mia");
    expect(JSON.parse(localStorage.getItem("coach.scope") ?? "{}")).toMatchObject({ member: "mia" });
    expect(screen.getAllByText(/Mia/).length).toBeGreaterThan(0);
  });
});

describe("the child's own view", () => {
  it("shows only the own money, with a gentle message when over a limit and no name of anyone else", async () => {
    serve({ "/me/summary": me, "/me/transactions": { total: 1, limit: 30, offset: 0, items: [{ date: "2026-10-01", amount: "-5.00", category: "food.restaurants", merchant: "Boulangerie" }] } });
    renderApp(<KidHome />);
    expect(await screen.findByText(/Hello Mia/)).toBeInTheDocument();
    expect(screen.getByText(/You went over this limit/)).toBeInTheDocument();
    expect(screen.getAllByText(/from a parent/).length).toBeGreaterThan(0);
    expect(await screen.findByText("Boulangerie")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Anna|Luca|Rossi/);
    expect(calls.map((c) => c.url).every((u) => /\/me\//.test(u) || u.endsWith("/session"))).toBe(true);
  });

  it("the app gives a child login ONLY that page: no navigation, no household pages", async () => {
    serve({ __user: { id: "mia-kid", role: "child", member_id: "mia", prefs: {} }, "/me/summary": me, "/me/transactions": { total: 0, limit: 30, offset: 0, items: [] } });
    render(<App />);
    expect(await screen.findByText(/Hello Mia/)).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("Household")).toBeNull();
    expect(calls.some((c) => c.url.includes("/household/") || c.url.includes("/analytics/") || c.url.includes("/api/v1/transactions"))).toBe(false);
  });

  it("an adult login gets the full app and the People pages", async () => {
    serve({ __user: { id: "papa", role: "adult", member_id: "luca", prefs: {} }, "/meta/filters": filters });
    render(<App />);
    expect((await screen.findAllByText("Household")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Kids' money").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Who pays what").length).toBeGreaterThan(0);
  });
});
