import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import Setup from "./Setup";
import { resetCsrfForTests } from "@/lib/api";
import { setLanguage } from "@/i18n";

const status = {
  as_of: "2026-10-05", progress: { done: 1, total: 7, next_step: "household" },
  steps: [
    { id: "household", heading: "Household and privacy declarations", status: "partial", have: { adults: 1, children: 0, country: null, employers: 0, places: 0, schools: 0 }, missing: ["country (FR or IT; FR is assumed): tax and cancellation rules depend on it"],
      missing_msg: [{ code: "onboarding.missing.country", params: {}, text: "country (FR or IT; FR is assumed): tax and cancellation rules depend on it" }] },
    { id: "accounts", heading: "Accounts: owner and purpose", status: "todo", have: { accounts: 2 }, missing: [{ account: "uid1", label: "Main", missing: ["owner", "purpose"] }] },
    { id: "loans", heading: "Loans and mortgage (fields mortgage-check needs)", status: "partial", have: { liabilities: 1 }, liabilities: [{ id: "mortgage", kind: "mortgage", missing: ["rate.nominal", "principal"], matched_in_bank_data: true }], loan_payments_without_file: [] },
    { id: "questions", heading: "Open questions", status: "done", have: { open: 0 } },
  ],
  next_actions: [{ step: "loans", do: "fill the empty fields", do_msg: { code: "onboarding.do.loans", params: {}, text: "fill the empty fields" },
    command: "uv run coach memory set <liability-id> rate.nominal <value>" }],
  how: [], note: "", declared: { employers: ["Acme"], places: ["Quimper", "Redon"], schools: [] },
};
const wizard = {
  steps: [
    { id: "init", title: "Home, secrets and encrypted database", status: "done", detail: "ready", command: "coach init && coach doctor", optional: false },
    { id: "enablebanking", title: "Your Enable Banking application", status: "todo", detail: "app id, redirect URL or key not set", command: "coach setup enablebanking", optional: false },
    { id: "connect", title: "Connect your first bank", status: "blocked", detail: "finish 'Your Enable Banking application' first", command: "coach connect", optional: false,
      detail_msg: { code: "wizard.finishFirst", params: { step: "Your Enable Banking application", need: "enablebanking" }, text: "finish 'Your Enable Banking application' first" } },
    { id: "schedule", title: "Daily job and alerts (optional)", status: "skipped", detail: "skipped for now", command: "coach schedule install", optional: true },
  ],
  progress: { done: 2, total: 4, next_step: "enablebanking" }, container: false,
  command: "uv run coach setup", commands: { setup: "uv run coach setup", enablebanking: "uv run coach setup enablebanking" },
};
let calls: { url: string; init?: RequestInit }[];

beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.endsWith("/setup/wizard")) body = wizard;
    else if (url.includes("/onboarding/household")) body = { dry_run: url.includes("dry_run=true"), changed: true, diff: "+country: FR\n", change_id: null, warnings: [] };
    else if (url.endsWith("/onboarding")) body = status;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(async () => {
  vi.unstubAllGlobals();
  await setLanguage("en", { persist: false });
});

describe("first-run wizard card", () => {
  it("shows the steps read-only, with the terminal command and no button that starts a step", async () => {
    renderApp(<Setup />);
    expect(await screen.findByTestId("wizard-progress")).toHaveTextContent("2 of 4 first-run steps done");
    const list = screen.getByRole("list", { name: "First-run steps" });
    expect(list).toHaveTextContent("Your Enable Banking application");
    expect(list).toHaveTextContent("(optional)");
    expect(screen.getByText("uv run coach setup")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("/setup/")).every((c) => (c.init?.method ?? "GET") === "GET")).toBe(true);
  });
});

describe("first-run card in Docker", () => {
  it("shows the commands the server returns (docker compose run) and how to mount the key", async () => {
    const dockerWizard = { ...wizard, container: true, command: "docker compose run --rm coach setup",
      commands: { setup: "docker compose run --rm coach setup", enablebanking: 'docker compose run --rm -v "$PWD/eb.pem:/tmp/eb.pem:ro" coach setup enablebanking' } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url.endsWith("/session") ? { csrf_token: "tok" } : url.endsWith("/setup/wizard") ? dockerWizard : url.endsWith("/onboarding") ? status : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    renderApp(<Setup />);
    await screen.findByTestId("wizard-progress");
    expect(screen.getByText("docker compose run --rm coach setup")).toBeInTheDocument();
    expect(screen.queryByText("uv run coach setup")).not.toBeInTheDocument();
    expect(screen.getByText(/-v "\$PWD\/eb.pem:\/tmp\/eb.pem:ro" coach setup enablebanking/)).toBeInTheDocument();
    expect(screen.getByText(/Running in Docker/)).toBeInTheDocument();
  });
});

describe("setup checklist", () => {
  it("shows the progress, what is missing per step and the command for loans", async () => {
    renderApp(<Setup />);
    expect(await screen.findByTestId("setup-progress")).toHaveTextContent("1 of 7 steps done");
    expect(screen.getByText(/account Main: owner, purpose not set/)).toBeInTheDocument();
    expect(screen.getByText(/mortgage \(Mortgage\): nominal rate, amount borrowed/)).toBeInTheDocument();
    expect(screen.getByText(/finish 'Your Enable Banking application' first/)).toBeInTheDocument();
    expect(screen.getByText("uv run coach memory set <liability-id> rate.nominal <value>")).toBeInTheDocument();
  });

  it("previews a household change with dry_run and writes it only after the click", async () => {
    renderApp(<Setup />);
    await screen.findByTestId("setup-progress");
    await userEvent.selectOptions(screen.getByLabelText("Country"), "FR");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/onboarding/household") && c.url.includes("dry_run=true"))).toBe(true));
    expect(calls.some((c) => c.url.includes("/onboarding/household") && !c.url.includes("dry_run=true"))).toBe(false);
    await userEvent.click(await screen.findByRole("button", { name: /write this change/i }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/onboarding/household") && !c.url.includes("dry_run=true") && c.init?.method === "PUT")).toBe(true));
  });

  it("shows the declared terms as chips, adds without removing, and needs an explicit confirmation to remove", async () => {
    renderApp(<Setup />);
    await screen.findByTestId("setup-progress");
    await userEvent.type(screen.getByLabelText(/Add Town/), "Dinan");
    await waitFor(() => expect(calls.some((c) => c.url.includes("dry_run=true") && String(c.init?.body).includes('"add":["Dinan"]'))).toBe(true));
    expect(String(calls.filter((c) => c.url.includes("/onboarding/household")).at(-1)!.init!.body)).not.toContain('"remove":["Quimper"');
    await userEvent.click(screen.getByRole("button", { name: "Remove Quimper" }));
    expect(await screen.findByText(/no longer be masked/)).toBeInTheDocument();
    const write = screen.getByRole("button", { name: /write this change/i });
    await waitFor(() => expect(write).toBeDisabled());
    await userEvent.click(screen.getByLabelText(/I understand/));
    await waitFor(() => expect(write).toBeEnabled());
    await userEvent.click(write);
    await waitFor(() => expect(calls.some((c) => !c.url.includes("dry_run=true") && c.init?.method === "PUT" && String(c.init.body).includes('"confirm_removal":true'))).toBe(true));
  });
});

describe("setup checklist in French", () => {
  it("translates what the server says is missing, the field names, the next action and the wizard details", async () => {
    await setLanguage("fr", { persist: false });
    renderApp(<Setup />);
    expect(await screen.findByText(/pays \(FR ou IT ; FR par défaut\)/)).toBeInTheDocument();
    expect(screen.getByText(/compte Main : titulaire, usage non renseigné/)).toBeInTheDocument();
    expect(screen.getByText(/mortgage \(Prêt immobilier\) : taux nominal, montant emprunté/)).toBeInTheDocument();
    expect(screen.getByText(/remplissez les champs vides/)).toBeInTheDocument();
    expect(screen.getByText("uv run coach memory set <liability-id> rate.nominal <value>")).toBeInTheDocument();     // the command stays as it is
    const list = await screen.findByRole("list", { name: /étapes/i });
    expect(list).toHaveTextContent(/terminez d'abord « .*Enable Banking.* »/);
    expect(list).toHaveTextContent("app id, redirect URL or key not set");                      // no message: the English is kept
  });
});
