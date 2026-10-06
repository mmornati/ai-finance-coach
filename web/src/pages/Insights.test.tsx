import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Insights from "./Insights";
import { resetCsrfForTests } from "@/lib/api";

const REF = "h_0123456789";
const coachItem = (o: object) => ({ id: "cin_1", created: "2026-10-04T08:00:00+00:00", kind: "digest", title: "Weekly digest 2026-10-04", body: `Quiet week: one large payment (${REF}).`, findings: [], evidence: [REF], skill: "digest-weekly", backend: "anthropic-api", model: "claude-sonnet-5-5", usage_ref: 1, status: "new", snoozed_until: null, unverified_numbers: [], suspicious: false, question: null, ...o });
let calls: { url: string; init?: RequestInit }[];
let items: object[];
let cards: object[] = [];

beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  cards = [];
  items = [coachItem({}), coachItem({ id: "cin_2", kind: "finding", title: "Odd number", body: "x", evidence: [], unverified_numbers: ["999.99"], suspicious: true, status: "read" })];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.endsWith("/insights")) body = { as_of: "2026-10-04", cards, hidden: 0, counts: { loan: cards.length }, coach: { configured: true, items, hidden: 0, message: "" } };
    else if (url.endsWith("/coach/resolve")) body = { refs: { [REF]: { kind: "transaction", tx_key: "real-key-1", date: "2026-09-27", amount: "-1200.00", category: "shopping.electronics", merchant: "Big shop" } } };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("insights feed: from the coach", () => {
  it("shows each insight with its evidence links, provenance and trust badges", async () => {
    renderApp(<Insights />);
    expect(await screen.findByText("Weekly digest 2026-10-04")).toBeInTheDocument();
    const card = screen.getByText("Weekly digest 2026-10-04").closest("section")!;
    expect(within(card).getByText("Digest")).toBeInTheDocument();
    expect(within(card).getByText("new")).toBeInTheDocument();
    expect(within(card).getByText(/anthropic-api claude-sonnet-5-5 · digest-weekly/)).toBeInTheDocument();
    await waitFor(() => expect(within(card).getAllByRole("link").some((a) => a.getAttribute("href") === "/transactions?tx=real-key-1")).toBe(true));
    const odd = screen.getByText("Odd number").closest("section")!;
    expect(within(odd).getByText("1 number unverified")).toHaveAttribute("title", expect.stringContaining("999.99"));
    expect(within(odd).getByText(/suspicious text seen/)).toBeInTheDocument();
  });

  it("read, snooze, done and dismiss call the coach insight endpoints", async () => {
    renderApp(<Insights />);
    const card = (await screen.findByText("Weekly digest 2026-10-04")).closest("section")!;
    for (const [name, path, body] of [[/mark read/i, "/api/v1/insights/cin_1/read", {}], [/snooze 7 days/i, "/api/v1/insights/cin_1/snooze", { days: 7 }], [/done/i, "/api/v1/insights/cin_1/done", {}], [/dismiss/i, "/api/v1/insights/cin_1/dismiss", {}]] as const) {
      await userEvent.click(within(card).getByRole("button", { name }));
      await waitFor(() => expect(calls.some((c) => c.url === path)).toBe(true));
      expect(JSON.parse(calls.find((c) => c.url === path)!.init!.body as string)).toEqual(body);
    }
  });

  it("with nothing from the coach it says how to get insights", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith("/insights") ? { as_of: "2026-10-04", cards: [], hidden: 0, counts: {}, coach: { configured: true, items: [], hidden: 0, message: "Nothing written by the coach yet: ask a question." } } : { csrf_token: "t" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    renderApp(<Insights />);
    expect(await screen.findByText(/Nothing written by the coach yet/)).toBeInTheDocument();
  });
});

describe("insights feed: subscription reminders (E8)", () => {
  it("shows a reminder card built from the user's own record, with a link to the subscriptions page", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith("/insights")
      ? { as_of: "2026-10-04", cards: [{ id: "ins_1", kind: "subscription", subtype: "unused", severity: "medium", title: "StreamBox: unused for 125 days", body: "You recorded 2026-06-01 as the last time you used it. This comes from your own record, not from the bank data.", amount: "155.88", date: "2026-10-04", subject: "StreamBox", evidence: [], persist: "ui" }],
        hidden: 0, counts: { subscription: 1 }, coach: { configured: true, items: [], hidden: 0, message: "" } } : { csrf_token: "t" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    renderApp(<Insights />);
    const card = (await screen.findByText("StreamBox: unused for 125 days")).closest("section")!;
    expect(within(card).getByText("Subscription")).toBeInTheDocument();
    expect(within(card).getByText(/your own record, not from the bank data/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open subscriptions" })).toHaveAttribute("href", "/subscriptions");
  });
});

describe("insights feed: loans (E9)", () => {
  it("shows a missed-payment card linked to its transaction and a lease reminder linked to the loan page, never to a transaction", async () => {
    cards = [
      { id: "ins_1", kind: "loan", subtype: "missed_payment", severity: "high", title: "Homebank: expected payment of 2026-10-03 not seen", body: "The instalment was not found within 5 days.", amount: null, date: "2026-10-03", subject: "Homebank", evidence: ["tx-1", "home-loan"], persist: "ui" },
      { id: "ins_2", kind: "loan", subtype: "loa_end", severity: "high", title: "LeaseCo ends in 88 days: buy or return?", body: "The contract ends on 2027-01-01.", amount: "15000.00", date: "2026-07-01", subject: "LeaseCo", evidence: ["evcar"], persist: "ui" },
    ];
    renderApp(<Insights />);
    const missed = (await screen.findByText(/Homebank: expected payment of .* not seen/)).closest("section")!;
    expect(within(missed).getByText("Loan")).toBeInTheDocument();
    expect(within(missed).getByRole("link", { name: /see the transaction/i })).toHaveAttribute("href", "/transactions?tx=tx-1");
    expect(within(missed).getByRole("link", { name: "Open the loan" })).toHaveAttribute("href", "/wealth");
    const lease = screen.getByText(/ends in 88 days/).closest("section")!;
    expect(within(lease).queryByRole("link", { name: /see the transaction/i })).not.toBeInTheDocument();
    expect(within(lease).getByRole("link", { name: "Open the loan" })).toHaveAttribute("href", "/wealth");
  });
});

describe("insights feed: rental property (E15)", () => {
  it("shows the scheme commitment reminder linked to the rental page, never to a transaction", async () => {
    cards = [
      { id: "ins_9", kind: "rental", subtype: "scheme_end", severity: "medium", title: "rental-flat-1: the scheme commitment ends on 2027-03-04 (about 5 month(s) left)", body: "No decision about the extension is recorded.", amount: null, date: "2027-03-04", subject: "rental-flat-1", evidence: ["rental-flat-1"], persist: "ui" },
    ];
    renderApp(<Insights />);
    const card = (await screen.findByText(/the scheme commitment ends on/)).closest("section")!;
    expect(within(card).getByText("Rental property")).toBeInTheDocument();
    expect(within(card).queryByRole("link", { name: /see the transaction/i })).not.toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open the property" })).toHaveAttribute("href", "/rental");
  });
});

describe("E11-5: insights from the coach carry the AI label and the advice banner", () => {
  it("shows an AI-generated badge on every coach insight and the banner on a flagged one", async () => {
    items = [coachItem({ ai_generated: true, ai_label: "AI-generated content: it can contain mistakes.", compliance: [], compliance_banner: null }),
      coachItem({ id: "cin_3", title: "Where to put savings", ai_generated: true, compliance: ["recommendation"], compliance_banner: "General information only, not personalised investment advice (FR: AMF / CIF; IT: Consob)." })];
    renderApp(<Insights />);
    const flagged = (await screen.findByText("Where to put savings")).closest("section")!;
    expect(within(flagged).getByText("AI-generated")).toBeInTheDocument();
    expect(within(flagged).getByTestId("compliance-banner")).toHaveTextContent(/AMF \/ CIF/);
    const clean = screen.getByText("Weekly digest 2026-10-04").closest("section")!;
    expect(within(clean).getByText("AI-generated")).toHaveAttribute("title", expect.stringContaining("can contain mistakes"));
    expect(within(clean).queryByTestId("compliance-banner")).toBeNull();
  });
});
