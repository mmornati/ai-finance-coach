import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Alerts from "./Alerts";
import { AlertsCard } from "./Dashboard";
import { resetCsrfForTests } from "@/lib/api";
import { setLanguage } from "@/i18n";

const ev = (o: object) => ({ id: "alr_aaaaaaaaaaaa", kind: "consent", severity: "high", created: "2026-10-04T12:00:00+00:00", updated: "2026-10-04T12:00:00+00:00", last_seen: "2026-10-04T12:00:00+00:00", resolved: false, resolved_at: null, title: "Fortuneo (FR): consent URGENT, 2 day(s) left", body: "Reconnect the bank.", payload: {}, status: "new", snoozed_until: null, acked_at: null, channels_sent: {}, escalations: 0, ...o });
const KINDS = [
  { kind: "consent", label: "Bank consent expiring or expired", muted: false, snoozed_until: null, disabled_in_config: false, digest_only: false },
  { kind: "unusual_charge", label: "Unusual charge", muted: true, snoozed_until: null, disabled_in_config: false, digest_only: true },
  { kind: "budget", label: "Budget over or at risk", muted: false, snoozed_until: "2026-10-10", disabled_in_config: true, digest_only: false },
];
const CHANNELS = {
  in_app: { enabled: true, note: "The in-app feed is always on." },
  channels: [
    { channel: "macos", enabled: false, ready: false, problems: [], external: false, target: "this Mac (notification centre)", sent_last_7_days: 0 },
    { channel: "ntfy", enabled: true, ready: true, problems: [], external: true, target: "ntfy.example.net/***", sent_last_7_days: 2 },
    { channel: "email", enabled: true, ready: false, problems: ["secret 'smtp_password' not set: `coach config set-secret smtp_password`"], external: true, target: "o***@example.net (starttls)", sent_last_7_days: 0 },
    { channel: "telegram", enabled: false, ready: false, problems: [], external: true, target: "(no chat id set)", sent_last_7_days: 0 },
  ],
  external_detail: "minimal", min_severity: "low", quiet_hours: "22:00-08:00", max_per_week: 7, weekly_digest: true, weekly_digest_to_channels: false, recent_deliveries: [],
  how_to_enable: "Edit config.toml ([alerts.<channel>] enabled = true), store the secret with `coach config set-secret`, then check it with `coach alerts test-channel <name> --dry-run`. Nothing is enabled from this page.",
};
const TEST = { dry_run: true, sent: false, channel: "ntfy", enabled: true, ready: true, problems: [], detail: "minimal", note: null, message: { title: "Coach (test)", body: "Coach: 2 new alerts (1 high). Open the app." }, text: "channel: ntfy\nPOST https://ntfy.example.net/***\n  Authorization: Bearer ********\nbody:\n  Coach: 2 new alerts (1 high). Open the app.", sample: "built from invented sample events (nothing of your data)" };

let calls: { url: string; init?: RequestInit }[];
let items: object[];
let ready = true;

beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  ready = true;
  items = [ev({}), ev({ id: "alr_bbbbbbbbbbbb", kind: "unusual_charge", severity: "medium", title: "Possible duplicate charge", status: "sent", channels_sent: { ntfy: { at: "x", severity: "medium" } }, escalations: 1 })];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const path = url.split("?")[0];
    let body: unknown = {};
    if (path.endsWith("/session")) body = { csrf_token: "tok" };
    else if (path === "/api/v1/alerts") {
      const q = new URLSearchParams(url.split("?")[1] ?? "");
      const status = q.get("status");
      const shown = (items as { status: string; kind: string; severity: string }[]).filter((e) => (!status || e.status === status) && (!q.get("kind") || e.kind === q.get("kind")) && (!q.get("severity") || e.severity === q.get("severity")));
      body = ready
        ? { ready: true, enabled: true, items: shown, counts: { open: items.length, new: 1, high: 1 }, kinds: KINDS, settings: { min_severity: "low", external_detail: "minimal", quiet_hours: "", max_per_week: 7 } }
        : { ready: false, items: [], counts: { open: 0, new: 0, high: 0 }, kinds: [], message: "The alerts tables do not exist yet: run `coach db migrate`." };
    } else if (path === "/api/v1/alerts/summary") body = { ready: true, open: 2, new: 1, high: 1 };
    else if (path === "/api/v1/alerts/channels") body = CHANNELS;
    else if (path.endsWith("/test")) body = TEST;
    else if (path === "/api/v1/alerts/check") body = { candidates: 3, new: 2, escalated: 0, reopened: 0, resolved: 0, unchanged: 1, sent: false };
    else if (path === "/api/v1/alerts/digest") body = { week_start: "2026-09-27", week_end: "2026-10-03", markdown: "# Weekly summary, 2026-09-27 to 2026-10-03\n\nYou spent **200.00 EUR**" };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const posted = (path: string) => calls.find((c) => c.url === path && c.init?.method === "POST");

describe("alerts center: the list", () => {
  it("shows the open alerts with severity, kind, state and what was sent where", async () => {
    renderApp(<Alerts />);
    const a = (await screen.findByText(/Fortuneo \(FR\): consent URGENT/)).closest("section")!;
    expect(within(a).getByText("high")).toBeInTheDocument();
    expect(within(a).getByText("Bank consent expiring or expired")).toBeInTheDocument();
    expect(within(a).getByText("new")).toBeInTheDocument();
    const b = screen.getByText("Possible duplicate charge").closest("section")!;
    expect(within(b).getByText("escalated")).toBeInTheDocument();
    expect(within(b).getByText(/sent to ntfy/)).toBeInTheDocument();
  });

  it("filters by status, kind and severity through the API", async () => {
    renderApp(<Alerts />);
    await screen.findByText(/Fortuneo/);
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "unusual_charge");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/alerts?") && c.url.includes("kind=unusual_charge"))).toBe(true));
    await waitFor(() => expect(screen.queryByText(/Fortuneo/)).not.toBeInTheDocument());
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "");
    await userEvent.selectOptions(screen.getByLabelText("Severity"), "high");
    await waitFor(() => expect(calls.some((c) => c.url.includes("severity=high"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Snoozed" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=snoozed"))).toBe(true));
    expect(await screen.findByText("Nothing snoozed")).toBeInTheDocument();
  });

  it("acknowledge, snooze and mute call the local endpoints", async () => {
    renderApp(<Alerts />);
    const a = (await screen.findByText(/Fortuneo/)).closest("section")!;
    await userEvent.click(within(a).getByRole("button", { name: /acknowledge/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/alr_aaaaaaaaaaaa/ack")).toBeTruthy());
    await userEvent.click(within(a).getByRole("button", { name: /snooze 7 days/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/alr_aaaaaaaaaaaa/snooze")).toBeTruthy());
    expect(JSON.parse(posted("/api/v1/alerts/alr_aaaaaaaaaaaa/snooze")!.init!.body as string)).toEqual({ days: 7 });
    await userEvent.click(within(a).getByRole("button", { name: /mute this kind/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/kinds/consent/mute")).toBeTruthy());
  });

  it("a snoozed alert can be restored", async () => {
    items = [ev({ status: "snoozed", snoozed_until: "2026-10-09" })];
    renderApp(<Alerts />);
    await userEvent.click(await screen.findByRole("button", { name: "Snoozed" }));
    const a = (await screen.findByText(/snoozed until/)).closest("section")!;
    await userEvent.click(within(a).getByRole("button", { name: /restore/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/alr_aaaaaaaaaaaa/restore")).toBeTruthy());
  });

  it("Check now evaluates and says nothing was sent outside the app", async () => {
    renderApp(<Alerts />);
    await userEvent.click(await screen.findByRole("button", { name: /check now/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/check")).toBeTruthy());
    expect(await screen.findByText(/nothing was sent outside the app/i)).toBeInTheDocument();
  });

  it("says what to do when the migration is missing", async () => {
    ready = false;
    renderApp(<Alerts />);
    expect(await screen.findByText(/coach db migrate/)).toBeInTheDocument();
  });
});

describe("alerts center: kinds", () => {
  it("shows mute / hold / config state and toggles them", async () => {
    renderApp(<Alerts />);
    const list = await screen.findByRole("list", { name: "Kinds of alert" });
    const unusual = within(list).getByText("Unusual charge").closest("li")!;
    expect(within(unusual).getByText("muted")).toBeInTheDocument();
    expect(within(unusual).getByText("digest only")).toBeInTheDocument();
    const budget = within(list).getByText("Budget over or at risk").closest("li")!;
    expect(within(budget).getByText("off in config")).toBeInTheDocument();
    expect(within(budget).getByText(/held until/)).toBeInTheDocument();
    await userEvent.click(within(unusual).getByRole("button", { name: /unmute/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/kinds/unusual_charge/unmute")).toBeTruthy());
    await userEvent.click(within(budget).getByRole("button", { name: /wake/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/kinds/budget/wake")).toBeTruthy());
    await userEvent.click(within(list).getByRole("button", { name: /hold bank consent/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/kinds/consent/snooze")).toBeTruthy());
  });
});

describe("alerts center: channels are read-only and tests are dry runs", () => {
  it("shows each channel's state, target and what is missing, and offers no way to enable one", async () => {
    renderApp(<Alerts />);
    const list = await screen.findByRole("list", { name: "Channels" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    const ntfy = within(list).getByText("ntfy").closest("li")!;
    expect(within(ntfy).getByText("ready")).toBeInTheDocument();
    expect(within(ntfy).getByText(/ntfy\.example\.net\/\*\*\*/)).toBeInTheDocument();
    const mail = within(list).getByText("E-mail (SMTP)").closest("li")!;
    expect(within(mail).getByText("not ready")).toBeInTheDocument();
    expect(within(mail).getByText(/smtp_password/)).toBeInTheDocument();
    expect(within(list).getByText("macOS notification").closest("li")!).toHaveTextContent("off");
    expect(screen.getByText(/Nothing is enabled from this page/)).toBeInTheDocument();
    expect(screen.getByText(/only a count/)).toBeInTheDocument();
    const card = list.closest("section")!;
    expect(within(card).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(card).queryByRole("switch")).not.toBeInTheDocument();
    expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("the test button runs a DRY RUN and shows the exact message, never sending", async () => {
    renderApp(<Alerts />);
    const list = await screen.findByRole("list", { name: "Channels" });
    await userEvent.click(within(list).getByRole("button", { name: /ntfy \(dry run\)/i }));
    await waitFor(() => expect(posted("/api/v1/alerts/channels/ntfy/test")).toBeTruthy());
    const dialog = await screen.findByRole("dialog", { hidden: true });
    expect(within(dialog).getByText(/Dry run: nothing was sent/)).toBeInTheDocument();
    expect(within(dialog).getByTestId("test-output")).toHaveTextContent("Coach: 2 new alerts (1 high). Open the app.");
    expect(within(dialog).getByTestId("test-output")).toHaveTextContent("Bearer ********");
    expect(calls.filter((c) => c.url.includes("/test"))).toHaveLength(1);
    expect(calls.some((c) => /send|enable/.test(c.url))).toBe(false);
  });
});

describe("alerts center: weekly summary preview", () => {
  it("loads the preview only when asked", async () => {
    renderApp(<Alerts />);
    await screen.findByText(/Fortuneo/);
    expect(calls.some((c) => c.url.includes("/alerts/digest"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /preview this week/i }));
    expect(await screen.findByTestId("digest-preview")).toHaveTextContent("You spent **200.00 EUR**");
  });
});

describe("dashboard badge", () => {
  it("the dashboard card counts the open alerts", async () => {
    renderApp(<AlertsCard />);
    const card = (await screen.findByText(/Fortuneo \(FR\): consent URGENT/)).closest("section")!;
    expect(within(card).getByText("2 open")).toBeInTheDocument();
    expect(within(card).getByText("1 high")).toBeInTheDocument();
    for (const l of within(card).getAllByRole("link")) expect(l).toHaveAttribute("href", "/alerts");
  });
});

describe("alerts center in French", () => {
  it("translates the buttons, badges and filters, not the alert written by the server", async () => {
    await setLanguage("fr");
    renderApp(<Alerts />);
    const a = (await screen.findByText(/Fortuneo \(FR\): consent URGENT/)).closest("section")!;
    expect(screen.getByRole("heading", { name: "Alertes" })).toBeInTheDocument();
    expect(within(a).getByText("élevée")).toBeInTheDocument();
    expect(within(a).getByText("nouvelle")).toBeInTheDocument();
    expect(within(a).getByRole("button", { name: /reporter de 7 jours/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Gravité")).toBeInTheDocument();
  });
});
