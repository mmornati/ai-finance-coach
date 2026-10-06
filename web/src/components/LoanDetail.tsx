import { useMemo, useState } from "react";
import { AlertTriangle, Calculator, CarFront, Check, FileSpreadsheet, Lightbulb } from "lucide-react";
import { Badge, Button, DiffView, Dialog, Disclosure, Field, Input, Money, Notice, Select, Spinner, Stat, Tabs } from "./ui";
import { CopyCommand } from "./CopyCommand";
import { useDryRun, useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtNumber, parseMoney } from "@/lib/format";
import type { EditResult, InferredField, LeaseStatus, Liability, LoanAlert, LoanDetail, ScenarioResult, ScheduleRow } from "@/api/types";

type Tab = "schedule" | "payments" | "scenario" | "suggestions" | "lease";

const num = (s: string): number | undefined => {
  const v = parseMoney(s.replace(",", "."));
  return v === null || Number.isNaN(v) ? undefined : v;
};

export function AlertList({ alerts }: { alerts: LoanAlert[] }) {
  if (!alerts.length) return <p className="text-sm text-muted">No payment alert: every instalment due was seen, at the expected amount.</p>;
  return (
    <ul className="grid gap-2">
      {alerts.map((a) => (
        <li key={a.id} className="flex gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div>
            <div className="font-medium">{a.title}</div>
            <div>{a.body}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** One loan in a dialog: the amortization schedule (or exactly what is missing), the bank payments and their alerts, scenarios on the real
 *  schedule, the suggestions inferred from the payments (never written: only proposed) and, for a lease, the end-of-contract view. */
export default function LoanDialog({ loan, onClose, onEdit }: { loan: Liability; onClose: () => void; onEdit: () => void }) {
  const lease = loan.kind === "loa" || loan.kind === "lld";
  const [tab, setTab] = useState<Tab>(lease ? "lease" : "schedule");
  const detail = useGet<LoanDetail>(`/loans/${loan.id}`);
  const tabs: { value: Tab; label: string; badge?: React.ReactNode }[] = [
    ...(lease ? [{ value: "lease" as Tab, label: "End of contract" }] : [{ value: "schedule" as Tab, label: "Schedule" }]),
    { value: "payments", label: "Payments", badge: loan.alerts.length ? <Badge tone="warn">{loan.alerts.length}</Badge> : undefined },
    ...(lease ? [] : [{ value: "scenario" as Tab, label: "Scenarios" }]),
    ...(lease ? [] : [{ value: "suggestions" as Tab, label: "Suggestions", badge: loan.inferred.length ? <Badge tone="info">{loan.inferred.length}</Badge> : undefined }]),
  ];
  return (
    <Dialog open onClose={onClose} size="lg" title={`${loan.lender ?? loan.id}${loan.asset ? ` · ${loan.asset}` : ""}`} description="Computed from the loan file and the bank payments. A missing figure is listed, never guessed."
      footer={<><Button variant="ghost" onClick={onClose}>Close</Button><Button onClick={onEdit}>Edit the loan</Button></>}>
      <div className="grid gap-4">
        <Tabs label="Loan sections" value={tab} onChange={setTab} tabs={tabs} />
        {detail.isLoading && <Spinner label="Loading the schedule" />}
        {detail.error && <Notice tone="neg">{detail.error.message}</Notice>}
        {detail.data && tab === "schedule" && <ScheduleTab d={detail.data} onEdit={onEdit} />}
        {detail.data && tab === "payments" && <PaymentsTab d={detail.data} loan={loan} />}
        {tab === "scenario" && <ScenarioTab loan={loan} />}
        {tab === "suggestions" && <SuggestionsTab loan={loan} fields={loan.inferred} notes={detail.data?.inference.notes ?? []} />}
        {tab === "lease" && <LeaseTab loan={loan} lease={detail.data?.lease ?? loan.lease} />}
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ schedule */

function ScheduleTab({ d, onEdit }: { d: LoanDetail; onEdit: () => void }) {
  const s = d.schedule;
  const [all, setAll] = useState(false);
  if (s.status !== "computed")
    return (
      <Notice tone="warn" title="No schedule yet">
        {s.status === "not_applicable" ? s.alternative : <>
          To compute it I need: <strong>{(s.missing ?? []).join(", ")}</strong>. {s.alternative}
          <div className="mt-2"><Button size="sm" onClick={onEdit}>Fill these in</Button></div>
        </>}
      </Notice>
    );
  const rows = s.rows ?? [];
  const nextIdx = rows.findIndex((r) => !r.made);
  const shown: ScheduleRow[] = all ? rows : rows.slice(Math.max(0, nextIdx - 1), Math.max(0, nextIdx - 1) + 12);
  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Instalment" value={<Money v={s.payment} />} hint={s.mode === "from_outstanding" ? "from the declared capital" : `${s.term_instalments} instalments`} />
        <Stat label="Capital still due" value={<Money v={s.remaining_capital} round />} hint={`${s.remaining_instalments} instalment${s.remaining_instalments === 1 ? "" : "s"} left`} />
        <Stat label="Next due" value={s.next_due ? fmtDate(s.next_due, "dayMonth") : "–"} hint={s.last_due ? `last ${fmtDate(s.last_due, "medium")}` : undefined} />
        <Stat label={s.total_cost ? "Total cost (interest + insurance)" : "Interest still to pay"} value={<Money v={s.total_cost ?? s.remaining_interest} round />} hint={s.total_interest ? `interest ${fmtMoneyShort(s.total_interest)}` : "the past is unknown"} />
      </div>
      {s.approximate && <Notice tone="warn">Variable rate: the table applies the current nominal rate to the whole term, so the bank's table will differ when the index moves.</Notice>}
      {s.outstanding_check && <Notice tone="warn" title="The table differs from the declared capital">The schedule gives {fmtMoneyShort(s.outstanding_check.computed)} due on {fmtDate(s.outstanding_check.as_of)}, the file says {fmtMoneyShort(s.outstanding_check.declared)}: {s.outstanding_check.hint}.</Notice>}
      {s.payment_check?.status === "differs" && <Notice tone="warn">The declared payment does not match the computed instalment{s.payment_check.hint ? `: ${s.payment_check.hint}` : ""}.</Notice>}
      <div>
        <h3 className="mb-1.5 text-sm font-semibold">Interest by calendar year</h3>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-muted"><tr><th className="px-3 py-1.5 font-medium">Year</th><th className="px-3 py-1.5 text-right font-medium">Instalments</th><th className="px-3 py-1.5 text-right font-medium">Interest</th><th className="px-3 py-1.5 text-right font-medium">Insurance</th><th className="px-3 py-1.5 text-right font-medium">Principal</th></tr></thead>
            <tbody>
              {s.by_year.map((y) => (
                <tr key={y.year} className="border-t border-border"><td className="px-3 py-1.5">{y.year}{y.partial && <span className="text-xs text-faint"> (part of the year)</span>}</td><td className="num px-3 py-1.5 text-right">{y.instalments}</td><td className="num px-3 py-1.5 text-right font-medium"><Money v={y.interest} /></td><td className="num px-3 py-1.5 text-right"><Money v={y.insurance} /></td><td className="num px-3 py-1.5 text-right"><Money v={y.principal} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-xs text-faint">The interest of a year is what a mortgage-interest line (IT 730) or a rental-income declaration (FR, for a rental-property loan) asks for: check it against the lender's annual statement before using it.</p>
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between"><h3 className="text-sm font-semibold">Instalments</h3><Button size="sm" variant="ghost" onClick={() => setAll((v) => !v)}>{all ? "Around today only" : `All ${rows.length}`}</Button></div>
        <div className="max-h-72 overflow-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-2 text-left text-muted"><tr><th className="px-2 py-1.5 font-medium">#</th><th className="px-2 py-1.5 font-medium">Due</th><th className="px-2 py-1.5 text-right font-medium">Interest</th><th className="px-2 py-1.5 text-right font-medium">Principal</th><th className="px-2 py-1.5 text-right font-medium">Insurance</th><th className="px-2 py-1.5 text-right font-medium">Debited</th><th className="px-2 py-1.5 text-right font-medium">Capital after</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.k} className={`border-t border-border ${r.made ? "text-muted" : ""}`}><td className="px-2 py-1">{r.k}{r.kind === "deferral" && " (deferral)"}</td><td className="px-2 py-1">{fmtDate(r.due, "medium")}</td><td className="num px-2 py-1 text-right"><Money v={r.interest} /></td><td className="num px-2 py-1 text-right"><Money v={r.principal} /></td><td className="num px-2 py-1 text-right"><Money v={r.insurance} /></td><td className="num px-2 py-1 text-right"><Money v={r.total} /></td><td className="num px-2 py-1 text-right"><Money v={r.balance} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Disclosure summary="How this is computed">
        <ul className="list-disc pl-5 text-sm text-muted">{(s.assumptions ?? []).map((a) => <li key={a}>{a}</li>)}</ul>
      </Disclosure>
    </div>
  );
}

const fmtMoneyShort = (v: string | null | undefined) => (v == null ? "?" : `${fmtNumber(Number(v), 2)} EUR`);

/* ------------------------------------------------------------------ payments */

function PaymentsTab({ d, loan }: { d: LoanDetail; loan: Liability }) {
  return (
    <div className="grid gap-4">
      <div>
        <h3 className="mb-1.5 text-sm font-semibold">Alerts</h3>
        <AlertList alerts={d.alerts} />
      </div>
      <div>
        <h3 className="mb-1.5 text-sm font-semibold">Payments found in the bank data</h3>
        {!loan.payment_match ? <Notice tone="warn">No bank label is recorded for this loan: set it (Edit the loan) to link its payments.</Notice> : d.payments.count === 0 ? <p className="text-sm text-muted">No payment matches /{loan.payment_match}/.</p> : (
          <>
            <p className="mb-1.5 text-xs text-muted">{d.payments.count} payment{d.payments.count > 1 ? "s" : ""} match /{loan.payment_match}/, typically <Money v={d.payments.median_amount} />; the last on {fmtDate(d.payments.last, "medium")}.</p>
            <ul className="divide-y divide-border rounded-lg border border-border text-sm">
              {[...d.payments.recent].reverse().map((p, i) => <li key={i} className="flex justify-between gap-3 px-3 py-1.5"><span>{fmtDate(p.date, "medium")} <span className="text-xs text-faint">{p.account}</span></span><Money v={p.amount} /></li>)}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ scenarios (E9-5) */

type ScenarioType = "prepay" | "renegotiate" | "insurance";

function ScenarioTab({ loan }: { loan: Liability }) {
  const [type, setType] = useState<ScenarioType>("prepay");
  const [v, setV] = useState({ amount: "", on: "", newRate: "", variant: "", bankFees: "", guaranteeFees: "", penalty: "", alternative: "", fees: "" });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV((c) => ({ ...c, [k]: e.target.value }));
  const body = useMemo(() => ({
    type, amount: num(v.amount), on: v.on || undefined, new_rate: num(v.newRate), variant: v.variant || undefined, bank_fees: num(v.bankFees) ?? 0, guarantee_fees: num(v.guaranteeFees) ?? 0,
    penalty: num(v.penalty), alternative: num(v.alternative), fees: num(v.fees) ?? 0,
  }), [type, v]);
  const [result, setResult] = useState<{ scenario: string; result: ScenarioResult; saved_insight: string | null } | null>(null);
  const run = useWrite(() => api.post<{ scenario: string; result: ScenarioResult; saved_insight: string | null }>(`/loans/${loan.id}/scenario`, { ...body, save: false }), { invalidate: false, onSuccess: setResult });
  const save = useWrite(() => api.post<{ saved_insight: string }>(`/loans/${loan.id}/scenario`, { ...body, save: true }), { success: "Stored as an insight" });
  const ready = type === "prepay" ? body.amount !== undefined : type === "renegotiate" ? body.new_rate !== undefined : body.alternative !== undefined;
  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted">Estimates on this loan's real schedule (capital and instalments left, insurance, deferral). Nothing is changed anywhere; the lender's figures decide.</p>
      <Tabs label="Scenario" value={type} onChange={(t) => { setType(t); setResult(null); }} tabs={[{ value: "prepay", label: "Early repayment" }, { value: "renegotiate", label: "Renegotiation / rachat" }, { value: "insurance", label: "Insurance" }]} />
      <div className="grid gap-3 sm:grid-cols-2">
        {type === "prepay" && <>
          <Field label="Amount to repay (EUR)">{(i) => <Input id={i} inputMode="decimal" value={v.amount} onChange={set("amount")} />}</Field>
          <Field label="On" hint="Blank = today">{(i) => <Input id={i} type="date" value={v.on} onChange={set("on")} />}</Field>
          <Field label="Penalty from your contract (EUR)" hint="Blank = the legal cap for a French / Italian mortgage; 0 for other loans">{(i) => <Input id={i} inputMode="decimal" value={v.penalty} onChange={set("penalty")} />}</Field>
        </>}
        {type === "renegotiate" && <>
          <Field label="Offered nominal rate (%)">{(i) => <Input id={i} inputMode="decimal" value={v.newRate} onChange={set("newRate")} />}</Field>
          <Field label="Procedure">{(i) => <Select id={i} value={v.variant} onChange={set("variant")}><option value="">Default for the country</option><option value="renegotiation">Renegotiation with the same bank</option><option value="rachat">Buy-back by another bank (FR)</option><option value="surroga">Surroga (IT)</option></Select>}</Field>
          <Field label="Bank fees (EUR)">{(i) => <Input id={i} inputMode="decimal" value={v.bankFees} onChange={set("bankFees")} />}</Field>
          <Field label="Guarantee fees (EUR)">{(i) => <Input id={i} inputMode="decimal" value={v.guaranteeFees} onChange={set("guaranteeFees")} />}</Field>
          <Field label="Penalty from your contract (EUR)" hint="Blank = the legal cap (FR IRA) or none (IT surroga)">{(i) => <Input id={i} inputMode="decimal" value={v.penalty} onChange={set("penalty")} />}</Field>
        </>}
        {type === "insurance" && <>
          <Field label="Other policy: EUR per month" hint="A dated quote for equivalent guarantees">{(i) => <Input id={i} inputMode="decimal" value={v.alternative} onChange={set("alternative")} />}</Field>
          <Field label="Switching fees (EUR)">{(i) => <Input id={i} inputMode="decimal" value={v.fees} onChange={set("fees")} />}</Field>
        </>}
      </div>
      <div className="flex gap-2">
        <Button variant="primary" disabled={!ready} busy={run.isPending} onClick={() => run.mutate(undefined as never)}><Calculator className="size-4" aria-hidden /> Calculate</Button>
        {result?.result.status === "computed" && <Button busy={save.isPending} onClick={() => save.mutate(undefined as never)}><Lightbulb className="size-4" aria-hidden /> Store as an insight</Button>}
      </div>
      {result && <ScenarioView type={result.scenario as ScenarioType} r={result.result} />}
    </div>
  );
}

function ScenarioView({ type, r }: { type: ScenarioType; r: ScenarioResult }) {
  if (r.status === "needs_fields") return <Notice tone="warn" title="Not enough information">{r.missing?.length ? <>Record: <strong>{r.missing.join(", ")}</strong>. </> : null}{r.alternative ?? r.note}</Notice>;
  if (r.status === "payoff") return <Notice tone="info">{r.note}</Notice>;
  if (r.status === "nothing_left") return <Notice tone="info">No instalment remains after that date.</Notice>;
  if (type === "prepay")
    return (
      <div className="grid gap-3">
        <p className="text-sm">Repaying <Money v={r.amount} /> on {fmtDate(r.date, "medium")}: capital due <Money v={r.capital_before} round /> → <Money v={r.capital_after} round />, {r.remaining_instalments} instalments of <Money v={r.instalment} /> left. Penalty <strong><Money v={r.penalty} /></strong>: <span className="text-muted">{r.penalty_basis}</span></p>
        <div className="grid gap-3 sm:grid-cols-2">
          {(r.options ?? []).map((o) => (
            <div key={o.mode} className="rounded-lg border border-border p-3 text-sm">
              <div className="font-semibold">{o.label}</div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                <dt className="text-muted">New instalment</dt><dd className="num text-right"><Money v={o.new_instalment} /></dd>
                <dt className="text-muted">Change a month</dt><dd className="num text-right"><Money v={o.monthly_change} signed /></dd>
                <dt className="text-muted">Months saved</dt><dd className="num text-right">{o.months_saved}</dd>
                <dt className="text-muted">Interest saved</dt><dd className="num text-right"><Money v={o.interest_saved} /></dd>
                <dt className="text-muted">Insurance saved</dt><dd className="num text-right"><Money v={o.insurance_saved} /></dd>
                <dt className="text-muted">Net of the penalty</dt><dd className="num text-right font-semibold"><Money v={o.net_saving} signed colored /></dd>
                <dt className="text-muted">Break-even</dt><dd className="num text-right">{o.break_even_months === null ? "never" : o.break_even_months === 0 ? "immediately" : `${o.break_even_months} months`}</dd>
              </dl>
              <p className="mt-2 text-xs text-muted">{o.verdict}</p>
            </div>
          ))}
        </div>
        {(r.notes ?? []).map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
      </div>
    );
  if (type === "renegotiate")
    return (
      <div className="grid gap-2 text-sm">
        <p>{r.variant} at {r.new_rate_pct} % instead of {r.current_rate_pct} %: instalment <Money v={r.current_payment} /> → <Money v={r.new_payment} /> (<Money v={r.monthly_saving} /> less a month).</p>
        <p>Gross interest saving <Money v={r.gross_interest_saving} />, costs <Money v={r.total_costs} /> (penalty <Money v={r.penalty} />: <span className="text-muted">{r.penalty_basis}</span>), net saving <strong><Money v={r.net_saving} signed colored /></strong>, break-even {r.break_even_months === null || r.break_even_months === undefined ? "never" : `${r.break_even_months} months`}.</p>
        <p className="text-muted">{String(r.verdict ?? "").replace(/_/g, " ")}</p>
        {(r.notes ?? []).map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
      </div>
    );
  return (
    <div className="grid gap-2 text-sm">
      <p>Insurance <Money v={r.current_monthly} /> → <Money v={r.alternative_monthly} /> a month over {r.remaining_months} months: <Money v={r.monthly_saving} /> a month, net saving <strong><Money v={r.net_saving} signed colored /></strong> ({String(r.verdict ?? "").replace(/_/g, " ")}).</p>
      {(r.notes ?? []).map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
    </div>
  );
}

/* ------------------------------------------------------------------ suggestions inferred from the payments (E9-1) */

function SuggestionsTab({ loan, fields, notes }: { loan: Liability; fields: InferredField[]; notes: string[] }) {
  const [out, setOut] = useState<{ id: string; accept_command: string } | null>(null);
  const propose = useWrite(() => api.post<{ id: string; accept_command: string }>(`/loans/${loan.id}/infer/propose`, { min_confidence: "medium" }), { success: "Proposal created", invalidate: true, onSuccess: setOut });
  const usable = fields.filter((f) => f.confidence !== "low");
  return (
    <div className="grid gap-3">
      <Notice tone="info" title="Inferred, not recorded">These values are worked out from the instalments seen in the bank data with annuity maths. They are never written to your memory: you can queue them as a proposal, check them against the contract, and accept them yourself.</Notice>
      {fields.length === 0 ? <p className="text-sm text-muted">Nothing can be suggested yet: two of the amount borrowed, the rate and the term must be known (or the capital still due with its date and the end date).</p> : (
        <ul className="divide-y divide-border rounded-lg border border-border text-sm">
          {fields.map((f) => (
            <li key={f.field} className="flex items-start justify-between gap-3 px-3 py-2">
              <div><div className="font-medium">{f.field} = {String(f.value)} <Badge tone={f.confidence === "high" ? "pos" : f.confidence === "medium" ? "info" : "warn"}>inferred, {f.confidence} confidence</Badge></div><div className="text-xs text-muted">{f.method}{f.note ? ` - ${f.note}` : ""}</div></div>
            </li>
          ))}
        </ul>
      )}
      {notes.map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
      {usable.length > 0 && <Button variant="primary" busy={propose.isPending} onClick={() => propose.mutate(undefined as never)}><FileSpreadsheet className="size-4" aria-hidden /> Queue as a memory proposal</Button>}
      {out && <div className="grid gap-1.5"><p className="text-sm">Proposal <strong>{out.id}</strong> is waiting in Memory &gt; Proposals. Accepting it is done in a terminal:</p><CopyCommand command={out.accept_command} /></div>}
    </div>
  );
}

/* ------------------------------------------------------------------ lease end of contract (E9-6) */

function LeaseTab({ loan, lease }: { loan: Liability; lease: LeaseStatus | null }) {
  const [km, setKm] = useState("");
  const [date, setDate] = useState("");
  const body = { km: km ? Number.parseInt(km, 10) : 0, date: date || undefined };
  const enabled = /^\d+$/.test(km);
  const pv = useDryRun<EditResult>(`/loans/${loan.id}/odometer`, body, enabled);
  const save = useWrite(() => api.post<EditResult>(`/loans/${loan.id}/odometer`, body, { dry_run: false }), { success: "Reading recorded", onSuccess: () => { setKm(""); setDate(""); } });
  if (!lease) return <Spinner label="Loading" />;
  const m = lease.mileage;
  return (
    <div className="grid gap-4">
      {lease.end.known ? (
        <Notice tone={lease.end.reminder_active ? "warn" : "info"} title={`Ends ${fmtDate(lease.end.end_date, "medium")}`}>
          {lease.end.ended ? "The contract has ended." : `${lease.end.days_left} days left.`}{lease.end.reminder_active ? " The 6-month reminder is active: decide between buying and returning the car." : ` The reminder starts on ${fmtDate(lease.end.reminder_date, "medium")}.`}
        </Notice>
      ) : <Notice tone="warn" title="End date unknown">Record the end date (Edit the loan) to get the buy-or-return reminder 6 months before.</Notice>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Option price (residual value)" value={lease.decision.residual_value ? <Money v={lease.decision.residual_value} round /> : "unknown"} hint={lease.decision.needs?.join(", ")} />
        <Stat label="Mileage limit" value={m.limit_km != null ? `${fmtNumber(m.limit_km)} km` : "unknown"} hint={m.excess_km_fee ? `${m.excess_km_fee} EUR per excess km` : "excess fee unknown"} />
        <Stat label="Projected at the end" value={m.projected_contract_km != null ? `${fmtNumber(m.projected_contract_km)} km` : "needs readings"} hint={m.pace ? `${fmtNumber(m.pace.km_per_year)} km a year` : undefined} tone={m.status === "over_limit" ? "neg" : m.status === "within_limit" ? "pos" : undefined} />
      </div>
      {m.status === "over_limit" && <Notice tone="neg" title="Over the mileage limit">About {fmtNumber(m.excess_km)} km over{m.excess_cost ? <>: roughly <Money v={m.excess_cost} /> of excess-mileage fees.</> : ". Record the excess fee to price it."}</Notice>}
      {m.needs.length > 0 && <p className="text-xs text-muted">To project the mileage I still need: {m.needs.join("; ")}.</p>}
      <div className="grid gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><CarFront className="size-4" aria-hidden /> Record the odometer</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Kilometres">{(i) => <Input id={i} inputMode="numeric" value={km} onChange={(e) => setKm(e.target.value)} />}</Field>
          <Field label="On" hint="Blank = today">{(i) => <Input id={i} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
        </div>
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} empty="Nothing changes." />}
        <div><Button variant="primary" disabled={!enabled || !pv.data || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}><Check className="size-4" aria-hidden /> Save the reading</Button></div>
        {(loan.odometer ?? []).length > 0 && <p className="text-xs text-muted">Readings: {(loan.odometer ?? []).map((o) => `${fmtNumber(o.km)} km (${fmtDate(o.date, "medium")})`).join(", ")}</p>}
      </div>
      <Disclosure summary="Return checklist" defaultOpen={!!lease.end.reminder_active}>
        <ul className="list-disc pl-5 text-sm text-muted">{lease.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
        <p className="mt-2 text-xs text-faint">General checklist: verify the notice period and the return conditions in your own contract.</p>
      </Disclosure>
    </div>
  );
}
