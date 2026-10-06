import { useMemo, useState } from "react";
import { Button, Dialog, DiffView, Field, Input, Notice, Select, Spinner, Textarea } from "./ui";
import { useDryRun, useFilters, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { parseMoney } from "@/lib/format";
import type { EditResult } from "@/api/types";

export type Kind = "liabilities" | "contracts" | "assets" | "members" | "events";
type FT = "text" | "number" | "date" | "select" | "bool" | "textarea" | "list" | "int" | "account";
interface Spec { key: string; label: string; type: FT; options?: [string, string][]; hint?: string; wide?: boolean; when?: (vals: Record<string, string>) => boolean }

const isLease = (v: Record<string, string>) => v.kind === "loa" || v.kind === "lld";
const notLease = (v: Record<string, string>) => !isLease(v);
const isVariable = (v: Record<string, string>) => v["rate.type"] === "variable" || v["rate.type"] === "mixed";

const LIAB_KINDS: [string, string][] = [["mortgage", "Mortgage"], ["car_loan", "Car loan"], ["loa", "Long-term lease with option (LOA)"], ["lld", "Long-term lease (LLD)"], ["consumer_loan", "Consumer loan"], ["bnpl", "Buy now, pay later"]];
const ASSET_KINDS = ["regulated_savings", "savings_account", "employee_savings_plan", "life_insurance_savings", "vehicle", "real_estate", "real_estate_rental", "securities", "pension", "cash", "crypto", "other"].map((k) => [k, k.replace(/_/g, " ")] as [string, string]);

const SPECS: Record<Kind, Spec[]> = {
  liabilities: [
    { key: "kind", label: "Kind", type: "select", options: LIAB_KINDS },
    { key: "lender", label: "Lender", type: "text" },
    { key: "asset", label: "Financed asset", type: "text" },
    { key: "holder", label: "Borrower", type: "text", hint: "Household member id, or joint (for the net worth by person)" },
    { key: "start_date", label: "Start date", type: "date" },
    { key: "first_payment_date", label: "First instalment on", type: "date", hint: "Blank = one month after the start", when: notLease },
    { key: "end_date", label: "End date", type: "date" },
    { key: "term_months", label: "Number of instalments (months)", type: "int", hint: "Blank = from the start to the end date" },
    { key: "payment_day", label: "Debit day of the month", type: "int" },
    { key: "principal", label: "Amount borrowed (EUR)", type: "number", when: notLease },
    { key: "monthly_payment", label: "Monthly payment (EUR)", type: "number", hint: "Debited per month, insurance included (lease: the monthly rent)" },
    { key: "rate.type", label: "Rate type", type: "select", options: [["fixed", "Fixed"], ["variable", "Variable"], ["mixed", "Mixed"]], when: notLease },
    { key: "rate.nominal", label: "Nominal rate (%)", type: "number", when: notLease },
    { key: "rate.taeg", label: "TAEG (%)", type: "number", when: notLease },
    { key: "rate.index", label: "Variable rate: index", type: "text", hint: "Stored only: the schedule uses the nominal rate above", when: (v) => notLease(v) && isVariable(v) },
    { key: "rate.margin", label: "Variable rate: margin (points)", type: "number", when: (v) => notLease(v) && isVariable(v) },
    { key: "rate.cap", label: "Variable rate: cap (%)", type: "number", when: (v) => notLease(v) && isVariable(v) },
    { key: "insurance.provider", label: "Borrower insurance: provider", type: "text" },
    { key: "insurance.monthly", label: "Borrower insurance: EUR per month", type: "number", hint: "A flat premium" },
    { key: "insurance.rate_pct", label: "Borrower insurance: % per year", type: "number", hint: "Instead of a flat amount", when: notLease },
    { key: "insurance.basis", label: "That % applies to", type: "select", options: [["initial", "The initial capital"], ["outstanding", "The capital still due"]], when: notLease },
    { key: "insurance.delegated", label: "Insurance delegated to another insurer", type: "bool", when: notLease },
    { key: "deferral.months", label: "Deferral at the start (months)", type: "int", when: notLease },
    { key: "deferral.kind", label: "Deferral kind", type: "select", options: [["partial", "Partial: interest only"], ["total", "Total: nothing paid, interest added"]], when: notLease },
    { key: "outstanding", label: "Outstanding capital (EUR)", type: "number", hint: "From your latest statement", when: notLease },
    { key: "outstanding_as_of", label: "Outstanding capital as of", type: "date", when: notLease },
    { key: "debited_account", label: "Debited from", type: "account", hint: "The account the instalment leaves" },
    { key: "payment_match", label: "Bank label of the payment (regex)", type: "text", hint: "e.g. ^HOMEBANK ECH PRET" },
    { key: "first_payment", label: "First rent / down payment (EUR)", type: "number", when: isLease },
    { key: "residual_value", label: "Residual value: purchase-option price (EUR)", type: "number", when: isLease },
    { key: "mileage_limit_km", label: "Mileage limit over the contract (km)", type: "int", when: isLease },
    { key: "excess_km_fee", label: "Fee per excess km (EUR)", type: "number", when: isLease },
    { key: "initial_km", label: "Odometer at the start (km)", type: "int", hint: "0 for a new car", when: isLease },
    { key: "notes", label: "Notes", type: "textarea", wide: true },
  ],
  contracts: [
    { key: "provider", label: "Provider", type: "text" },
    { key: "kind", label: "Kind", type: "select", options: ["energy", "telecom", "insurance_home", "insurance_car", "insurance_health", "health", "streaming", "software", "membership", "insurance_other", "water", "other"].map((k) => [k, k.replace(/_/g, " ")] as [string, string]) },
    { key: "merchant_match", label: "Bank label of the payment (regex)", type: "text" },
    { key: "start_date", label: "Start date", type: "date" },
    { key: "renewal", label: "Next renewal", type: "date" },
    { key: "billing.amount", label: "Amount (EUR)", type: "number" },
    { key: "billing.period", label: "Billed", type: "select", options: [["monthly", "Monthly"], ["bimonthly", "Every 2 months"], ["quarterly", "Quarterly"], ["yearly", "Yearly"]] },
    { key: "commitment_end", label: "Commitment ends", type: "date" },
    { key: "notice_period_days", label: "Notice (days)", type: "int" },
    { key: "holder", label: "Holder", type: "text", hint: "Household member id, or joint: who signs cancellation letters" },
    { key: "contract_number", label: "Contract number", type: "text", hint: "Printed on cancellation letters; stays on this machine" },
    { key: "usage.frequency", label: "How often you use it", type: "select", options: [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["rarely", "Rarely"], ["never", "Never"], ["unknown", "I don't know"]] },
    { key: "usage.last_used", label: "Last used", type: "date" },
    { key: "usage.note", label: "Usage note", type: "text" },
    { key: "keep", label: "Decision", type: "select", options: [["true", "Keep"], ["review", "Review"], ["false", "Cancel"]] },
    { key: "notes", label: "Notes", type: "textarea", wide: true },
  ],
  assets: [
    { key: "kind", label: "Kind", type: "select", options: ASSET_KINDS },
    { key: "provider", label: "Provider", type: "text" },
    { key: "holder", label: "Holder", type: "text" },
    { key: "@value", label: "Current value (EUR)", type: "number" },
    { key: "as_of", label: "Value as of", type: "date" },
    { key: "liquidity", label: "Liquidity", type: "select", options: [["immediate", "Immediate"], ["short", "Short term"], ["locked", "Locked"]] },
    { key: "connected", label: "Synced from a bank (counted through its balance)", type: "bool", wide: true },
    { key: "contribution_monthly", label: "Monthly contribution (EUR)", type: "number" },
    { key: "description", label: "Description", type: "text" },
    { key: "notes", label: "Notes", type: "textarea", wide: true },
  ],
  members: [
    { key: "name", label: "Name", type: "text", hint: "Stays on this machine" },
    { key: "role", label: "Role", type: "select", options: [["adult", "Adult"], ["child", "Child"]] },
    { key: "birth_year", label: "Birth year", type: "int" },
    { key: "aliases", label: "Names seen in bank data", type: "list", hint: "Comma separated" },
  ],
  events: [
    { key: "title", label: "Title", type: "text" },
    { key: "start", label: "Start", type: "date" },
    { key: "end", label: "End", type: "date" },
    { key: "budget", label: "Budget (EUR)", type: "number" },
    { key: "status", label: "Status", type: "select", options: [["planned", "Planned"], ["ongoing", "Ongoing"], ["done", "Done"]] },
    { key: "note", label: "Note", type: "textarea", wide: true },
  ],
};

export const KIND_LABEL: Record<Kind, string> = { liabilities: "loan", contracts: "contract", assets: "asset", members: "household member", events: "event" };

function flatten(o: Record<string, any>, prefix = ""): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(o)) {
    if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v, `${prefix}${k}.`));
    else out[`${prefix}${k}`] = v;
  }
  return out;
}
function nest(flat: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(flat)) {
    const parts = k.split(".");
    let cur = out;
    parts.slice(0, -1).forEach((p) => (cur = cur[p] ??= {}));
    cur[parts.at(-1)!] = v;
  }
  return out;
}

function toStr(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

function convert(spec: Spec, raw: string): unknown {
  const s = raw.trim();
  if (s === "") return null;
  switch (spec.type) {
    case "number": return parseMoney(s.replace(",", "."));
    case "int": return Number.parseInt(s, 10);
    case "list": return s.split(",").map((x) => x.trim()).filter(Boolean);
    case "bool": return s === "true";
    default:
      if (spec.key === "keep") return s === "true" ? true : s === "false" ? false : s;
      return s;
  }
}

/** A validated form for one memory item: the server validates (schema + semantics), previews the diff and records it. */
export function ItemDialog({ kind, id: initialId, initial, onClose }: { kind: Kind; id?: string; initial?: Record<string, any>; onClose: () => void }) {
  const filters = useFilters();
  const specs = SPECS[kind];
  const isNew = !initialId;
  const base = useMemo(() => {
    const f = flatten(initial ?? {});
    if (kind === "assets" && initial) f["@value"] = initial.value;
    if (kind === "contracts" && f.keep !== undefined) f.keep = f.keep;
    return f;
  }, [initial, kind]);
  const [id, setId] = useState(initialId ?? "");
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(specs.map((s) => [s.key, toStr(base[s.key])])));
  const valueField = (initial as any)?.value_field ?? (["vehicle", "real_estate", "real_estate_rental", "other"].includes(vals.kind) ? "value" : "balance");

  const fields = useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const s of specs) {
      const cur = vals[s.key] ?? "";
      if (!isNew && cur === toStr(base[s.key])) continue; // only what the user changed
      if (isNew && cur === "") continue;
      const key = s.key === "@value" ? valueField : s.key;
      out[key] = convert(s, cur);
    }
    return nest(out);
  }, [vals, specs, isNew, base, valueField]);
  const idOk = /^[a-z0-9][a-z0-9_-]*$/.test(id);
  const has = Object.keys(fields).length > 0;
  const enabled = idOk && has;
  const path = `/memory/${kind}/${id}`;
  const pv = useDryRun<EditResult>(path, { fields }, enabled, "put");
  const save = useWrite(() => api.put<EditResult>(path, { fields }, { dry_run: false }), { success: "Saved to memory", onSuccess: onClose });
  const missingKind = kind === "liabilities" && isNew && !vals.kind;

  return (
    <Dialog open onClose={onClose} size="lg" title={isNew ? `New ${KIND_LABEL[kind]}` : `Edit ${KIND_LABEL[kind]}: ${initialId}`} description="Validated against the memory schema; the change is recorded in the history with a diff."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" busy={save.isPending} disabled={!enabled || !pv.data || !!pv.error || pv.loading || missingKind} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-4 sm:grid-cols-2">
        {isNew && <Field label="Id" hint="Lowercase letters, digits, - or _. It cannot be changed later." error={id && !idOk ? "Use lowercase letters, digits, - or _" : undefined}>{(i) => <Input id={i} value={id} onChange={(e) => setId(e.target.value.toLowerCase())} placeholder={kind === "members" ? "anna" : "home-loan"} />}</Field>}
        {specs.filter((s) => !s.when || s.when(vals)).map((s) => (
          <Field key={s.key} label={s.label} hint={s.hint} className={s.wide ? "sm:col-span-2" : undefined}>
            {(i) => {
              const v = vals[s.key] ?? "";
              const set = (x: string) => setVals((c) => ({ ...c, [s.key]: x }));
              if (s.type === "select") return <Select id={i} value={v} onChange={(e) => set(e.target.value)}><option value="">–</option>{s.options!.map(([a, b]) => <option key={a} value={a}>{b}</option>)}</Select>;
              if (s.type === "account") return <Select id={i} value={v} onChange={(e) => set(e.target.value)}><option value="">–</option>{filters.data?.accounts.map((a) => <option key={a.uid} value={a.uid}>{a.label}</option>)}</Select>;
              if (s.type === "bool") return <label className="flex min-h-10 items-center gap-2 text-sm"><input id={i} type="checkbox" checked={v === "true"} onChange={(e) => set(e.target.checked ? "true" : "false")} /> {s.label}</label>;
              if (s.type === "textarea") return <Textarea id={i} value={v} onChange={(e) => set(e.target.value)} />;
              return <Input id={i} type={s.type === "date" ? "date" : "text"} inputMode={s.type === "number" || s.type === "int" ? "decimal" : undefined} value={v} onChange={(e) => set(e.target.value)} />;
            }}
          </Field>
        ))}
      </div>
      <div className="mt-4 grid gap-2">
        {missingKind && <Notice tone="warn">Choose the kind of loan first.</Notice>}
        {pv.loading && <Spinner label="Validating" />}
        {pv.error && <Notice tone="neg" title="Not valid">{pv.error}</Notice>}
        {pv.data && <>{pv.data.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}<DiffView diff={pv.data.diff} empty="Nothing changes." /></>}
      </div>
    </Dialog>
  );
}
