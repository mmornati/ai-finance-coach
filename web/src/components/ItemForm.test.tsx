import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test/utils";
import { ItemDialog } from "./ItemForm";
import { resetCsrfForTests } from "@/lib/api";

let calls: { url: string; init?: RequestInit }[];
beforeEach(() => {
  resetCsrfForTests();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = url.includes("/session") ? { csrf_token: "t" } : url.includes("/meta/filters") ? { accounts: [{ uid: "ce", label: "Main", bank: null, owner: null, purpose: null }], owners: [], members: [], purposes: [], tags: [], sources: [], events: [], groups: [], today: "" }
      : { dry_run: url.includes("dry_run=true"), changed: true, diff: "+x: 1", change_id: null, warnings: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("memory item form", () => {
  it("previews with dry_run, sends only the changed fields (nested, typed) and writes on Save", async () => {
    const onClose = vi.fn();
    renderApp(<ItemDialog kind="liabilities" id="home-loan" initial={{ id: "home-loan", kind: "mortgage", lender: "Homebank", outstanding: "180000.00", rate: { type: "fixed", nominal: 2.1, taeg: null }, notes: "" }} onClose={onClose} />);
    await userEvent.clear(screen.getByLabelText("Outstanding capital (EUR)"));
    await userEvent.type(screen.getByLabelText("Outstanding capital (EUR)"), "175000,5");
    await userEvent.clear(screen.getByLabelText("Nominal rate (%)"));
    await userEvent.type(screen.getByLabelText("Nominal rate (%)"), "2.3");
    await waitFor(() => expect(calls.some((c) => c.url.includes("dry_run=true"))).toBe(true), { timeout: 2000 });
    const preview = calls.filter((c) => c.url.includes("dry_run=true")).at(-1)!;
    expect(preview.url).toBe("/api/v1/memory/liabilities/home-loan?dry_run=true");
    expect(preview.init!.method).toBe("PUT");
    expect(JSON.parse(preview.init!.body as string)).toEqual({ fields: { outstanding: 175000.5, rate: { nominal: 2.3 } } }); // unchanged fields are not sent
    expect(await screen.findByText("+x: 1")).toBeInTheDocument();
    const writes = () => calls.filter((c) => c.init?.method === "PUT" && !c.url.includes("dry_run"));
    expect(writes()).toHaveLength(0); // nothing written yet
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(writes().map((c) => c.url)).toEqual(["/api/v1/memory/liabilities/home-loan"]);
  });

  it("asks for a valid id and a kind before a new loan can be saved", async () => {
    renderApp(<ItemDialog kind="liabilities" onClose={() => undefined} />);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Id/), "Bad Id");
    expect(screen.getByText(/lowercase letters/i, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows the lease fields for a lease, the rate and insurance fields for a loan, and the variable-rate fields only for a variable rate (E9-1)", async () => {
    renderApp(<ItemDialog kind="liabilities" onClose={() => undefined} />);
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "mortgage");
    expect(screen.getByLabelText("Nominal rate (%)")).toBeInTheDocument();
    expect(screen.getByLabelText("Deferral at the start (months)")).toBeInTheDocument();
    expect(screen.getByLabelText("Debit day of the month")).toBeInTheDocument();
    expect(screen.queryByLabelText("Residual value: purchase-option price (EUR)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Variable rate: index")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Rate type"), "variable");
    expect(screen.getByLabelText("Variable rate: index")).toBeInTheDocument();
    expect(screen.getByLabelText("Variable rate: cap (%)")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "loa");
    expect(screen.getByLabelText("Residual value: purchase-option price (EUR)")).toBeInTheDocument();
    expect(screen.getByLabelText("Mileage limit over the contract (km)")).toBeInTheDocument();
    expect(screen.getByLabelText("Fee per excess km (EUR)")).toBeInTheDocument();
    expect(screen.getByLabelText("Odometer at the start (km)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Nominal rate (%)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Deferral at the start (months)")).not.toBeInTheDocument();
  });

  it("sends the new loan fields nested and typed", async () => {
    renderApp(<ItemDialog kind="liabilities" id="car" initial={{ id: "car", kind: "car_loan", lender: "CarFin", notes: "" }} onClose={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Number of instalments (months)"), "36");
    await userEvent.type(screen.getByLabelText("Debit day of the month"), "10");
    await userEvent.type(screen.getByLabelText("Borrower insurance: EUR per month"), "15,5");
    await userEvent.selectOptions(screen.getByLabelText("Deferral kind"), "partial");
    await userEvent.type(screen.getByLabelText("Deferral at the start (months)"), "2");
    await waitFor(() => expect(calls.some((c) => c.url.includes("dry_run=true"))).toBe(true), { timeout: 2000 });
    await waitFor(() => {
      const last = calls.filter((c) => c.url.includes("dry_run=true")).at(-1)!;
      expect(JSON.parse(last.init!.body as string)).toEqual({ fields: { term_months: 36, payment_day: 10, insurance: { monthly: 15.5 }, deferral: { kind: "partial", months: 2 } } });
    }, { timeout: 2000 });
  });
});
