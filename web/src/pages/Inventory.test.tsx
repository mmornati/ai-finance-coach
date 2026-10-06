import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import InventoryView from "./Inventory";
import { resetCsrfForTests } from "@/lib/api";
import type { InvAlternative, InvRow } from "@/api/types";

const cancellation = (o: object = {}) => ({
  country: "FR", family: "subscription", can_cancel_now: true, earliest_effective_date: null, notice_period_days: null, method: "online termination button if you signed online",
  conditions: ["a rolling monthly subscription normally ends at the end of the period already paid"], unknown: [], early_termination_cost: null,
  legal_basis: [{ id: "fr-3-clics", name: "Termination online", law: "loi n° 2022-1158 (art. L215-1-1)", source: "loi n° 2022-1158, art. L215-1-1", last_reviewed: "2026-10" }],
  last_reviewed: "2026-10", verify: "Verify with your contract and the official source before acting.", disclaimer: "General summary, not legal advice.",
  anniversary_route: null, contract_notice_deadline: null, first_request_date: null, send_notice_by: null, ...o,
});
const alt = (o: Partial<InvAlternative>): InvAlternative => ({
  id: "alt_1", provider: "CheapStream", offer: "Basic", monthly_price: "8.99", switching_costs: "0.00", features: "HD", source_url: "https://example.org/cheap", retrieved_at: "2026-09-20", age_days: 14,
  method: "find-cheaper", source: "coach-llm", status: "current", label: "seen 14 day(s) ago", stale: false,
  savings: { monthly: "4.00", yearly: "48.00", net_12m: "48.00", break_even_months: 0, verdict: "saving", computed_by: "code (savings_estimate)", stale_warning: null }, ...o,
});
const row = (o: Partial<InvRow>): InvRow => ({
  ref: "rec_1", name: "StreamBox", entity: "STREAMBOX", group: "streaming_media", group_label: "Streaming & media", category: "subscriptions.video_streaming", kind: "streaming", cost_source: "series",
  cadence: "monthly", expected_amount: "12.99", monthly: "12.99", yearly: "155.88", status: "active", first_seen: "2025-10-05", last_payment: "2026-09-05", next_charge: "2026-10-05", overdue_days: 0, account: "Cards",
  price_history: [{ from: "2025-10-05", amount: "12.99" }], price_changes: [], contract: { status: "on_file", id: "streambox" }, series_id: "rec_1", series: ["rec_1"], contract_id: "streambox", linked_series: ["rec_1"], draftable: false,
  usage: { frequency: "never", last_used: "2026-06-01", note: null, recorded: true, measurable: true, question_asked: false, note_not_measurable: null,
           signals: [{ kind: "unused_60_days", days: 125, last_used: "2026-06-01", measurable: "from the last-used date you recorded" }, { kind: "paid_but_never_used", last_payment: "2026-09-05", measurable: "you recorded never" }] },
  cancellation: cancellation(), alternatives: { count: 2, current: 1, outdated: 1, best: alt({}), items: [alt({}), alt({ id: "alt_2", provider: "OldStream", offer: "Promo", monthly_price: "4.99", retrieved_at: "2026-08-01", age_days: 64, status: "outdated", stale: true, label: "outdated, re-check",
    savings: { monthly: "8.00", yearly: "96.00", net_12m: "96.00", break_even_months: 0, verdict: "saving", computed_by: "code", stale_warning: "price older than 30 days: re-check before relying on it" } })], note: "quotes older than 30 days are outdated" },
  decision: null, proposed_decisions: [], ...o,
});
const fit = row({ ref: "rec_2", name: "Fitclub", group: "memberships", group_label: "Memberships", kind: "membership", monthly: "39.90", yearly: "478.80", series_id: "rec_2", series: ["rec_2"], contract: { status: "missing", id: null }, contract_id: null, draftable: true,
  usage: { frequency: "unknown", last_used: null, note: null, recorded: false, measurable: false, question_asked: false, note_not_measurable: "Whether a service is used is not in the bank data.", signals: [] },
  alternatives: { count: 0, current: 0, outdated: 0, best: null, items: [], note: "quotes older than 30 days are outdated" } });
const telco = row({ ref: "rec_3", name: "TelcoCo", group: "telecom", group_label: "Telecom", kind: "telecom", monthly: "29.99", yearly: "359.88", series_id: "rec_3", contract_id: "telco", contract: { status: "expired", id: "telco", expired_on: "2026-01-15" },
  usage: { frequency: "unknown", last_used: null, note: null, recorded: false, measurable: false, question_asked: false, note_not_measurable: null, signals: [] },
  cancellation: cancellation({ family: "telecom", earliest_effective_date: "2026-10-14", notice_period_days: 10, early_termination_cost: { basis: "25 %", share_pct: 25, remaining_months: 4, free_exit_date: "2027-01-15", amount: 29.99 },
    legal_basis: [{ id: "fr-telecom", name: "Telecom contracts (Loi Chatel)", law: "loi n° 2008-3", source: "loi n° 2008-3 du 3 janvier 2008", last_reviewed: "2026-10" }] }),
  alternatives: { count: 0, current: 0, outdated: 0, best: null, items: [], note: "quotes older than 30 days are outdated" } });

const inventory = {
  as_of: "2026-10-04", country: "FR", rows: [row({}), fit, telco], groups_meta: [{ id: "streaming_media", label: "Streaming & media" }, { id: "memberships", label: "Memberships" }, { id: "telecom", label: "Telecom" }],
  groups: { streaming_media: { label: "Streaming & media", count: 1, monthly: "12.99", yearly: "155.88", without_contract: 0, unknown_cost: 0 }, memberships: { label: "Memberships", count: 1, monthly: "39.90", yearly: "478.80", without_contract: 1, unknown_cost: 0 }, telecom: { label: "Telecom", count: 1, monthly: "29.99", yearly: "359.88", without_contract: 0, unknown_cost: 0 } },
  totals: { services: 3, monthly: "82.88", yearly: "994.56", without_contract: 1, expired_contracts: 1, with_cancellation_rule: 3, cancellation_decidable: 3, usage_unknown: 1, outdated_alternatives: 1, reminders: 2 },
  savings: { realised_monthly: "3.99", realised_since_decisions: "31.92", realised_yearly_run_rate: "47.88", verified: 1, pending: 0, contradicted: 0, claimed_monthly_unverified: "0.00" }, notes: ["loans, rent and taxes are not subscriptions"],
};
const letter = { contract: "streambox", lang: "fr", channel: "lrar", subject: "x", text: "Madame, Monsieur,\nJe résilie le contrat n° [numéro de contrat].", filename: "cancellation-streambox-lrar-fr.txt", placeholders: ["contract_number", "town / city"],
  legal_basis: [{ id: "fr-3-clics", name: "Termination online", law: "l", source: "loi n° 2022-1158, art. L215-1-1", last_reviewed: "2026-10" }], can_cancel_now: true, send_on_or_after: null, send_by: null, notes: ["Envoyez-la en recommandé."], pdf: null, pdf_note: "text only: no PDF writer is installed", sent: false, country: "FR" };

let calls: { url: string; init?: RequestInit }[];
let contactSet = false;
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  contactSet = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/subs/inventory")) body = inventory;
    else if (url.startsWith("/api/v1/subs/savings")) body = { ...inventory.savings, as_of: "2026-10-04", decisions: [], proposed: [{ id: "dec_p1", decision: "cancelled", name: "Oldapp", source: "coach-llm", before_monthly: 3.99, after_monthly: 0, note: null }], reminders: [], note: "" };
    else if (url.startsWith("/api/v1/subs/letter")) body = { ...letter, lang: new URL(url, "http://x").searchParams.get("lang"), channel: new URL(url, "http://x").searchParams.get("channel") };
    else if (url.startsWith("/api/v1/subs/contact") && init?.method === "PUT") body = { dry_run: false, changed: true, diff: "", change_id: null, warnings: [] };
    else if (url.startsWith("/api/v1/subs/contact")) body = { contact: {}, set: contactSet, local_only: true, members: [] };
    else if (url.startsWith("/api/v1/subs/contracts/draft")) body = { dry_run: url.includes("dry_run=true"), changed: true, diff: "+provider: Fitclub\n+kind: membership", change_id: null, warnings: [],
      contract: { series: "rec_2", contract_id: "fitclub", file: "contracts/fitclub.yaml", kind: "membership", provider: "Fitclub", missing: ["renewal", "commitment_end", "notice_period_days"], yearly: "478.80", warnings: [], value: {} } };
    else if (url.startsWith("/api/v1/subs/decisions") && url.includes("dry_run=true")) body = { dry_run: true, valid: true, monthly_saving: 4 };
    else if (url.startsWith("/api/v1/subs/decisions")) body = { id: "dec_1", dry_run: false, monthly_saving: 4 };
    else if (url.startsWith("/api/v1/subs/alternatives")) body = { id: "alt_9", stored: true };
    else if (url.includes("/usage")) body = { dry_run: false, changed: true, diff: "", change_id: "abc", warnings: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const writes = () => calls.filter((c) => c.init?.method && c.init.method !== "GET" && !c.url.includes("dry_run=true"));     // previews (dry run) write nothing
const csrf = (c: { init?: RequestInit }) => (c.init!.headers as Record<string, string>)["X-CSRF-Token"];

describe("the subscriptions inventory", () => {
  it("lists every service by group with its cost, contract status, usage, reminders, cancellation and outdated offers", async () => {
    renderApp(<InventoryView />);
    expect(await screen.findByRole("heading", { name: "StreamBox" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Streaming & media" })).toBeInTheDocument();
    expect(screen.getByText("Contract on file")).toBeInTheDocument();
    expect(screen.getByText("No contract file")).toBeInTheDocument();
    expect(screen.getByText("Contract dates expired")).toBeInTheDocument();
    expect(screen.getAllByText("Usage unknown", { selector: "span" })).toHaveLength(2);       // Fitclub and TelcoCo: nothing recorded, nothing invented
    expect(screen.getByText(/Usage: never, last/)).toBeInTheDocument();
    expect(screen.getByText("Unused for 125 days (your record)")).toBeInTheDocument();
    expect(screen.getByText("Marked never used, still paid")).toBeInTheDocument();
    expect(screen.getAllByText(/Can cancel now/)).toHaveLength(3);
    expect(screen.getByText(/Can cancel now · effective from/)).toBeInTheDocument();           // TelcoCo: the earliest effective date from the rules
    expect(screen.getByText(/saves .*48.*\/year/)).toBeInTheDocument();
    expect(screen.getByText("1 outdated offer(s), re-check")).toBeInTheDocument();
    expect(screen.getByText(/Loans, rent and taxes are not subscriptions/)).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });

  it("creates a contract from a subscription: previews first (dry run), writes only on the button, with the CSRF token", async () => {
    renderApp(<InventoryView />);
    await userEvent.click(await screen.findByRole("button", { name: /create contract from this subscription/i }));
    const dialog = await screen.findByRole("dialog", { name: /create a contract file/i });
    expect(await within(dialog).findByText(/\+provider: Fitclub/)).toBeInTheDocument();
    expect(within(dialog).getByText(/renewal, commitment_end, notice_period_days/)).toBeInTheDocument();
    const preview = calls.find((c) => c.url === "/api/v1/subs/contracts/draft?dry_run=true")!;
    expect(preview.init!.method).toBe("POST");
    expect(JSON.parse(preview.init!.body as string)).toEqual({ series: "rec_2" });
    expect(writes().filter((c) => !c.url.includes("dry_run"))).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Create contract file" }));
    await waitFor(() => expect(writes().filter((c) => !c.url.includes("dry_run"))).toHaveLength(1));
    const w = writes().find((c) => !c.url.includes("dry_run"))!;
    expect(w.url).toBe("/api/v1/subs/contracts/draft");
    expect(csrf(w)).toBe("tok");
  });

  it("shows the cancellation rules with their source, review date and the verify disclaimer", async () => {
    renderApp(<InventoryView />);
    const card = (await screen.findByRole("heading", { name: "TelcoCo" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /details/i }));
    const panel = within(card).getByRole("region", { name: "Cancellation" });
    expect(within(panel).getByText(/Can I cancel\? \(FR\)/)).toBeInTheDocument();
    expect(within(panel).getByText(/about/).textContent).toMatch(/29.99/);
    expect(within(panel).getByText(/4 months left, 25 % still due/)).toBeInTheDocument();
    expect(within(panel).getByText(/Source: loi n° 2008-3 du 3 janvier 2008. Reviewed 2026-10/)).toBeInTheDocument();
    expect(within(panel).getByText(/Verify with your contract and the official source before acting/)).toBeInTheDocument();
  });

  it("records usage in the contract file", async () => {
    renderApp(<InventoryView />);
    const card = (await screen.findByRole("heading", { name: "StreamBox" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /details/i }));
    const usage = within(card).getByRole("region", { name: "Usage" });
    await userEvent.selectOptions(within(usage).getByLabelText("How often"), "rarely");
    await userEvent.click(within(usage).getByRole("button", { name: "Save usage" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toBe("/api/v1/subs/contracts/streambox/usage");
    expect(writes()[0].init!.method).toBe("PUT");
    expect(JSON.parse(writes()[0].init!.body as string)).toEqual({ frequency: "rarely", last_used: "2026-06-01", note: null });
  });

  it("a service without a contract file says why usage cannot be saved yet", async () => {
    renderApp(<InventoryView />);
    const card = (await screen.findByRole("heading", { name: "Fitclub" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /details/i }));
    expect(within(within(card).getByRole("region", { name: "Usage" })).getByText(/Create a contract file first/)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /prepare cancellation letter/i })).not.toBeInTheDocument();
  });

  it("lists the alternatives with the savings, flags the outdated one, and validates the https link before storing", async () => {
    renderApp(<InventoryView />);
    const card = (await screen.findByRole("heading", { name: "StreamBox" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /details/i }));
    const panel = within(card).getByRole("region", { name: "Alternatives" });
    const table = within(panel).getByRole("table");
    expect(within(table).getByText("current best")).toBeInTheDocument();
    expect(within(table).getAllByText("outdated, re-check")).toHaveLength(1);
    expect(within(table).getAllByRole("link", { name: "example.org" })[0]).toHaveAttribute("href", "https://example.org/cheap");
    expect(within(panel).getByText(/computed by the app, not by a model/i)).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Add an offer" }));
    await userEvent.type(within(panel).getByLabelText("Provider"), "NewCo");
    await userEvent.type(within(panel).getByLabelText("Offer name"), "Plus");
    await userEvent.type(within(panel).getByLabelText("Monthly price (EUR)"), "9.5");
    await userEvent.type(within(panel).getByLabelText("Source URL (https)"), "http://example.org/x");
    expect(within(panel).getByText("The link must start with https://")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Store offer" })).toBeDisabled();
    await userEvent.clear(within(panel).getByLabelText("Source URL (https)"));
    await userEvent.type(within(panel).getByLabelText("Source URL (https)"), "https://example.org/x");
    await userEvent.click(within(panel).getByRole("button", { name: "Store offer" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(JSON.parse(writes()[0].init!.body as string)).toMatchObject({ ref: "rec_1", provider: "NewCo", offer_name: "Plus", monthly_price: 9.5, source_url: "https://example.org/x", method: "manual" });
  });

  it("removes a stored offer with DELETE", async () => {
    renderApp(<InventoryView />);
    const card = (await screen.findByRole("heading", { name: "StreamBox" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /details/i }));
    await userEvent.click(within(card).getByRole("button", { name: /remove OldStream Promo/i }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toBe("/api/v1/subs/alternatives/alt_2");
    expect(writes()[0].init!.method).toBe("DELETE");
  });

  it("prepares a cancellation letter locally: language and channel, copy, download, placeholders, and the contact form when no address is stored", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const created: string[] = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn((b: Blob) => { created.push("blob"); return `blob:${b.size}`; }), revokeObjectURL: vi.fn() }));
    renderApp(<InventoryView />);
    const card = (await screen.findByRole("heading", { name: "StreamBox" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /prepare cancellation letter/i }));
    const dialog = await screen.findByRole("dialog", { name: /cancellation letter: streambox/i });
    expect(await within(dialog).findByLabelText("Letter text")).toHaveValue(letter.text);
    expect(within(dialog).getByText(/Nothing is sent: you send it yourself/)).toBeInTheDocument();
    expect(within(dialog).getByText(/contract_number · town \/ city/)).toBeInTheDocument();
    expect(within(dialog).getByText(/text only: no PDF writer/)).toBeInTheDocument();
    const first = calls.find((c) => c.url.startsWith("/api/v1/subs/letter"))!;
    expect(new URL(first.url, "http://x").searchParams.get("contract")).toBe("streambox");
    expect(new URL(first.url, "http://x").searchParams.get("lang")).toBe("fr");
    await userEvent.selectOptions(within(dialog).getByLabelText("Language"), "en");
    await userEvent.selectOptions(within(dialog).getByLabelText("How you will send it"), "email");
    await waitFor(() => expect(calls.some((c) => c.url.includes("lang=en") && c.url.includes("channel=email"))).toBe(true));
    await userEvent.click(within(dialog).getByRole("button", { name: "Copy text" }));
    expect(writeText).toHaveBeenCalledWith(letter.text);
    await userEvent.click(within(dialog).getByRole("button", { name: /download \.txt/i }));
    expect(created).toEqual(["blob"]);
    // no contact stored: the form says it stays on this machine, and saves it with the CSRF token
    expect(within(dialog).getByText(/never sent to a model and the coach cannot read them/i)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Postal address"), "12 rue de l'Exemple");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save contact" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toBe("/api/v1/subs/contact");
    expect(JSON.parse(writes()[0].init!.body as string)).toEqual({ address: "12 rue de l'Exemple", email: null });
    expect(calls.filter((c) => c.url.startsWith("http")).length).toBe(0);                    // nothing leaves the app: same-origin calls only
  });

  it("records a decision after a server-side validation and confirms the coach's proposed one", async () => {
    renderApp(<InventoryView />);
    await userEvent.click(await screen.findByRole("button", { name: "Record it" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].url).toBe("/api/v1/subs/decisions/dec_p1/confirm");
    const card = (await screen.findByRole("heading", { name: "StreamBox" })).closest("section")!;
    await userEvent.click(within(card).getByRole("button", { name: /details/i }));
    const panel = within(card).getByRole("region", { name: "Decision" });
    const record = within(panel).getByRole("button", { name: "Record decision" });
    await waitFor(() => expect(record).toBeEnabled(), { timeout: 2000 });                    // enabled only once the server validated it (dry run)
    await userEvent.click(record);
    await waitFor(() => expect(writes().some((c) => c.url === "/api/v1/subs/decisions")).toBe(true));
    const w = writes().find((c) => c.url === "/api/v1/subs/decisions")!;
    expect(JSON.parse(w.init!.body as string)).toMatchObject({ ref: "rec_1", decision: "cancelled", before_monthly: 12.99, after_monthly: 0 });
    expect(await within(panel).findByText(/Saves/)).toBeInTheDocument();
  });
});
