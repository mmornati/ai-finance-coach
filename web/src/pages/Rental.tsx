import { useMemo, useState } from "react";
import { Building2, CalendarClock, FileText, Plus, Scale } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Segmented, Select, Skeleton, Stat, Tabs } from "@/components/ui";
import { RentalChart } from "@/components/charts";
import { useDryRun, useRental, useRentalIndicators, useRentalList, useRentalTax, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtMoney, fmtMonth, fmtPct } from "@/lib/format";
import type { EditResult, RentalDetail, RentalMonth, RentalPnl, RentalScheme } from "@/api/types";

type TabKey = "cashflow" | "scheme" | "tax" | "sell";
const STATUS_TONE: Record<string, "pos" | "neg" | "warn" | "neutral"> = { received: "pos", missing: "neg", partial: "warn", late_paid: "warn", declared_vacancy: "neutral", unknown: "neutral", not_let_yet: "neutral" };
const STATUS_TEXT: Record<string, string> = {
  received: "rent received", missing: "no rent", partial: "part of the rent", late_paid: "paid late", declared_vacancy: "declared vacancy", unknown: "data incomplete", not_let_yet: "not let yet",
};
const LINK_TEXT: Record<string, string> = { declared: "set on the property", only_one: "the only rental account (confirm it)", none: "not linked", unknown: "not found in the data", asset: "named by the loan", account: "debited from the property account" };

export default function Rental() {
  const list = useRentalList();
  const [sel, setSel] = useState<string>("");
  const [tab, setTab] = useState<TabKey>("cashflow");
  const [declare, setDeclare] = useState(false);
  return (
    <>
      <PageHeader
        title="Rental property"
        subtitle="The cash flow and the P&L of a rental flat, the commitment of its tax-incentive scheme, the figures of the rental-income return and the renegotiate-or-sell indicators. Everything is computed from its bank account, its loan schedule and the facts you recorded: a missing fact is listed, never guessed."
        actions={<Button onClick={() => setDeclare(true)}><Plus className="size-4" aria-hidden /> Declare a property</Button>}
      />
      <Async q={list} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          if (d.properties.length === 0)
            return (
              <Card>
                <EmptyState icon={<Building2 className="size-6" />} title="No rental property yet" action={<Button variant="primary" onClick={() => setDeclare(true)}>Declare a property</Button>}>
                  A rental property is an asset of kind real_estate_rental. Give it its bank account (purpose "rental"), its loan and, for a tax-incentive scheme, the commitment you signed.
                  {d.unlinked_rental_accounts.length > 0 && ` ${d.unlinked_rental_accounts.length} account(s) are already flagged "rental".`}
                </EmptyState>
              </Card>
            );
          const id = d.properties.some((p) => p.id === sel) ? sel : d.properties[0].id;
          return (
            <div className="grid gap-4">
              {d.properties.length > 1 && (
                <Segmented label="Property" value={id} onChange={setSel} options={d.properties.map((p) => ({ value: p.id, label: p.id }))} />
              )}
              {d.unlinked_rental_accounts.length > 0 && (
                <Notice tone="warn">{d.unlinked_rental_accounts.length} account(s) are flagged "rental" but belong to no property: set <code>account</code> on a property (Edit facts).</Notice>
              )}
              <Tabs<TabKey>
                label="Rental sections"
                value={tab}
                onChange={setTab}
                tabs={[
                  { value: "cashflow", label: "Cash flow and P&L" },
                  { value: "scheme", label: "Scheme commitment", badge: d.properties.find((p) => p.id === id)?.scheme.decision_needed ? <Badge tone="warn">decide</Badge> : undefined },
                  { value: "tax", label: "Tax year" },
                  { value: "sell", label: "Renegotiate or sell" },
                ]}
              />
              {tab === "cashflow" && <CashflowTab id={id} />}
              {tab === "scheme" && <SchemeTab id={id} />}
              {tab === "tax" && <TaxTab id={id} />}
              {tab === "sell" && <SellTab id={id} />}
            </div>
          );
        }}
      </Async>
      {declare && <DeclareDialog onClose={() => setDeclare(false)} onDone={(id) => { setSel(id); setDeclare(false); }} />}
    </>
  );
}

/* ------------------------------------------------------------------ cash flow and P&L (E15-1, E15-2) */
function CashflowTab({ id }: { id: string }) {
  const [year, setYear] = useState<number | undefined>();
  const q = useRental(id, { months: 12, ...(year ? { year } : {}) });
  const [facts, setFacts] = useState(false);
  const [vac, setVac] = useState(false);
  return (
    <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
      {(d) => (
        <div className="grid gap-4">
          <Links d={d} onEdit={() => setFacts(true)} />
          {d.cashflow.months.length === 0 ? (
            <Card><EmptyState icon={<Scale className="size-6" />} title="No closed month of data for this property yet">Link its bank account (Edit facts, field "account"), or tag the payments made from another account with <code>{d.tag}</code>.</EmptyState></Card>
          ) : (
            <>
              <Card title="Last twelve closed months" subtitle="A cash flow: the loan principal counts as money out. The effort d'épargne is what you had to add each month when the rent did not cover the costs.">
                <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label="Rent expected" value={<Money v={d.cashflow.rent.expected} />} hint={d.cashflow.rent.source === "declared" ? "the rent you declared" : d.cashflow.rent.source === "observed_median" ? "median of the rents seen" : "unknown"} />
                  <Stat label="Average monthly net" value={<Money v={d.cashflow.average?.net} colored signed />} hint={d.cashflow.average ? `over ${d.cashflow.n_complete} complete month(s)` : "no complete month yet"} />
                  <Stat label="Average monthly effort d'épargne" value={<Money v={d.cashflow.average?.effort} />} hint="out of your pocket" tone={d.cashflow.average && parseFloat(d.cashflow.average.effort) > 0 ? "warn" : undefined} />
                  <Stat label="Occupancy" value={d.cashflow.vacancy.occupancy_rate === null ? "–" : fmtPct(d.cashflow.vacancy.occupancy_rate)} hint={`${d.cashflow.vacancy.n_missing} month(s) without rent, ${d.cashflow.vacancy.n_declared} declared`} tone={d.cashflow.vacancy.n_missing > 0 ? "warn" : undefined} />
                </div>
                <RentalChart months={d.cashflow.months} />
                <MonthsTable months={d.cashflow.months} />
              </Card>
              <Vacancy d={d} onDeclare={() => setVac(true)} />
              <PnlCard p={d.pnl} years={d.years} year={year ?? d.pnl.year} onYear={setYear} />
              {d.flows_to_label > 0 && (
                <Notice tone="info">
                  {d.flows_to_label} flow(s) of the property account are in no property category and sit in "other flows". Label them with a memory annotation (rent, loan, charges, tax, fees, insurance, works), or tag a cost paid from another account <code>{d.tag}</code>.
                </Notice>
              )}
            </>
          )}
          {facts && <FactsDialog d={d} onClose={() => setFacts(false)} />}
          {vac && <VacancyDialog id={id} onClose={() => setVac(false)} />}
        </div>
      )}
    </Async>
  );
}

function Links({ d, onEdit }: { d: RentalDetail; onEdit: () => void }) {
  const ok = d.links.account === "declared" && d.links.loans > 0;
  return (
    <Card title={`Property ${d.id}`} action={<Button size="sm" onClick={onEdit}>Edit facts</Button>}>
      <div className="flex flex-wrap gap-2 text-[13px]">
        <Badge tone={d.links.account === "declared" ? "pos" : d.links.account === "only_one" ? "warn" : "neg"}>account: {LINK_TEXT[d.links.account] ?? d.links.account}</Badge>
        <Badge tone={d.links.loans > 0 ? "pos" : "warn"}>loan: {d.links.loans > 0 ? LINK_TEXT[d.links.loan] ?? d.links.loan : "not linked"}</Badge>
        {d.scheme.declared && <Badge tone="info">scheme: {d.scheme.scheme ?? "recorded"}</Badge>}
        {d.scheme.missing.length > 0 && <Badge tone="warn">{d.scheme.missing.length} fact(s) missing</Badge>}
      </div>
      {!ok && <p className="mt-2 text-xs text-muted">Link the account and the loan to get the full cash flow, the interest of the return and the net equity.</p>}
      {d.links.notes.map((n) => <p key={n} className="mt-1 text-xs text-warn">{n}</p>)}
    </Card>
  );
}

function MonthsTable({ months }: { months: RentalMonth[] }) {
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th scope="col" className="py-1.5 pr-2 font-medium">Month</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Rent</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Loan</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Other costs</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Net</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">Effort</th>
            <th scope="col" className="py-1.5 pl-2 font-medium">Rent status</th>
          </tr>
        </thead>
        <tbody>
          {[...months].reverse().map((m) => (
            <tr key={m.month} className="border-t border-border">
              <td className="py-1.5 pr-2">{fmtMonth(m.month, "long")}{!m.complete && <span title="the account data do not cover the whole month"> *</span>}</td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.rent} /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.loan} /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={String(parseFloat(m.costs) - parseFloat(m.loan))} /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.net} colored signed /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.effort} /></td>
              <td className="py-1.5 pl-2"><Badge tone={STATUS_TONE[m.rent_status] ?? "neutral"}>{STATUS_TEXT[m.rent_status] ?? m.rent_status}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-faint">* the bank data do not cover the whole month: its figures are lighter in the chart and not averaged.</p>
    </div>
  );
}

function Vacancy({ d, onDeclare }: { d: RentalDetail; onDeclare: () => void }) {
  const v = d.cashflow.vacancy;
  const cur = d.current_month;
  return (
    <Card title="Vacancy" subtitle="A month without the expected rent, once the property is let. Months the data do not cover are never counted." action={<Button size="sm" onClick={onDeclare}>Declare a vacancy</Button>}>
      {v.n_missing === 0 && v.n_declared === 0 && cur.status !== "late" ? (
        <p className="text-sm text-muted">No month without rent since the property was let ({v.months_since_let} month(s) followed).</p>
      ) : (
        <ul className="grid gap-1 text-sm">
          {v.missing_months.length > 0 && <li><Badge tone="neg">no rent</Badge> {v.missing_months.map((m) => fmtMonth(m, "long")).join(", ")}</li>}
          {v.declared_months.length > 0 && <li><Badge>declared</Badge> {v.declared_months.map((m) => fmtMonth(m, "long")).join(", ")}</li>}
          {v.late_paid_months.length > 0 && <li><Badge tone="warn">paid late</Badge> {v.late_paid_months.map((m) => fmtMonth(m, "long")).join(", ")}</li>}
          {cur.status === "late" && <li><Badge tone="warn">this month</Badge> the rent of {fmtMonth(cur.month, "long")} has not arrived yet</li>}
        </ul>
      )}
    </Card>
  );
}

function PnlCard({ p, years, year, onYear }: { p: RentalPnl; years: number[]; year: number; onYear: (y: number) => void }) {
  const rows: [string, string][] = [["Rent received", p.totals.rent], ["Loan instalments", p.totals.loan], ["Co-ownership charges", p.totals.charges], ["Management fees", p.totals.fees], ["Property tax", p.totals.taxes], ["Insurance (PNO, GLI)", p.totals.insurance], ["Works and repairs", p.totals.works], ["Other flows", p.totals.other]];
  return (
    <Card
      title={`P&L ${p.year}`}
      subtitle={`${p.n_months} closed month(s)${p.complete ? "" : ": not a complete year"}`}
      action={years.length > 1 ? <Select aria-label="Year" value={String(year)} onChange={(e) => onYear(Number(e.target.value))}>{years.map((y) => <option key={y} value={y}>{y}</option>)}</Select> : undefined}
    >
      <table className="w-full text-sm">
        <tbody>
          {rows.map(([label, v], i) => (
            <tr key={label} className="border-t border-border first:border-0">
              <td className="py-1.5">{i > 0 && "− "}{label}</td>
              <td className="num py-1.5 text-right"><Money v={v} /></td>
            </tr>
          ))}
          <tr className="border-t border-border-strong font-semibold">
            <td className="py-2">= Net cash result</td>
            <td className="num py-2 text-right"><Money v={p.totals.net} colored signed /></td>
          </tr>
        </tbody>
      </table>
      <div className="mt-3 grid gap-1 text-xs text-muted">
        <span>Effort d'épargne: <Money v={p.totals.effort} /> {p.monthly_average_effort && <>(about <Money v={p.monthly_average_effort} /> a month)</>}. Your own transfers into the account (<Money v={p.totals.owner_in} />) are not rent.</span>
        {p.loan_split && <span>Loan of the year from its schedule: interest <Money v={p.loan_split.interest} />, borrower insurance <Money v={p.loan_split.insurance} />, principal <Money v={p.loan_split.principal} />.{p.economic && <> Result if the principal repaid is not a cost: <Money v={p.economic.result} colored signed />.</>}</span>}
        {p.gross_yield_pct !== undefined && <span>Gross yield on the value you declared: {p.gross_yield_pct} %.</span>}
        {p.months_incomplete.length > 0 && <span className="text-warn">Not fully covered by the account data: {p.months_incomplete.join(", ")}.</span>}
        {p.months_missing_data.length > 0 && <span className="text-warn">Closed months without any data: {p.months_missing_data.join(", ")}.</span>}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ scheme commitment (E15-3) */
function SchemeTab({ id }: { id: string }) {
  const q = useRental(id, { months: 3 });
  const [facts, setFacts] = useState(false);
  const [ext, setExt] = useState(false);
  return (
    <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
      {(d) => {
        const s = d.scheme;
        return (
          <div className="grid gap-4">
            {!s.declared ? (
              <Card><EmptyState icon={<CalendarClock className="size-6" />} title="No scheme recorded for this property" action={<Button variant="primary" onClick={() => setFacts(true)}>Record the scheme</Button>}>
                Give the scheme's name, the start of the commitment and its length in years (6, 9 or 12 for a Pinel-type scheme); the end date and its reminders follow. The rent cap and the tenant income limit are the figures you read in your deed: nothing is looked up.
              </EmptyState></Card>
            ) : (
              <Card title={`${s.scheme ?? "Scheme"}: commitment`} subtitle="The end date counts the commitment from its start date; check it against your deed." action={<Button size="sm" onClick={() => setFacts(true)}>Edit facts</Button>}>
                <SchemeBody s={s} onExtension={() => setExt(true)} />
              </Card>
            )}
            {s.declared && (
              <div className="grid gap-4 sm:grid-cols-2">
                <Card title="Rent cap" subtitle="Your own figures: the cap you declared against the rent">
                  <p className="text-sm">
                    {s.rent_cap.status === "unknown" ? "Not checked: record a rent cap and the lease rent." : (
                      <>
                        <Badge tone={s.rent_cap.status === "above_cap" ? "neg" : "pos"}>{s.rent_cap.status === "above_cap" ? "above the cap" : "within the cap"}</Badge>{" "}
                        rent <Money v={s.rent_cap.rent} /> against a cap of <Money v={s.rent_cap.cap} />
                      </>
                    )}
                  </p>
                  {s.rent_cap.rent_basis && <p className="mt-1 text-xs text-faint">Rent: {s.rent_cap.rent_basis}. Cap: {s.rent_cap.cap_basis}.</p>}
                </Card>
                <Card title="Tenant income" subtitle="The limit and the income you declared">
                  <p className="text-sm">
                    {s.tenant_income.status === "unknown" ? "Not checked: record the income limit and the tenant's reference income." : (
                      <>
                        <Badge tone={s.tenant_income.status === "above_limit" ? "neg" : "pos"}>{s.tenant_income.status === "above_limit" ? "above the limit" : "within the limit"}</Badge>{" "}
                        <Money v={s.tenant_income.tenant_income} /> against a limit of <Money v={s.tenant_income.limit} />
                      </>
                    )}
                  </p>
                </Card>
              </div>
            )}
            {s.missing.length > 0 && (
              <Card title="Facts the coach does not know" subtitle="Never guessed: record them, or let the coach ask you (Memory > Questions).">
                <ul className="grid gap-1 text-sm">
                  {s.missing.map((m) => (
                    <li key={m.field}><code className="text-[13px]">{m.field}</code> <span className="text-muted">for {m.needed_for}</span></li>
                  ))}
                </ul>
              </Card>
            )}
            {facts && <FactsDialog d={d} onClose={() => setFacts(false)} />}
            {ext && <ExtensionDialog id={id} onClose={() => setExt(false)} />}
          </div>
        );
      }}
    </Async>
  );
}

function SchemeBody({ s, onExtension }: { s: RentalScheme; onExtension: () => void }) {
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Start" value={s.start_date ? fmtDate(s.start_date) : "–"} />
        <Stat label="Length" value={s.years ? `${s.years} years` : "–"} />
        <Stat label="End" value={s.end_date ? fmtDate(s.end_date) : "–"} hint={s.end_source ?? undefined} />
        <Stat label="Left" value={s.state === "unknown" ? "–" : s.state === "ended" ? "ended" : s.state === "not_started" ? "not started" : `${s.months_left} months`} hint={s.days_left !== undefined ? `${s.days_left} days` : undefined} tone={s.decision_needed ? "warn" : undefined} />
      </div>
      {s.progress_pct !== undefined && <ProgressBar label="Share of the commitment elapsed" value={s.progress_pct} max={100} tone={s.decision_needed ? "warn" : "info"} />}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={s.extension.decision === "undecided" ? "warn" : "pos"}>extension: {s.extension.decision.replace("_", " ")}</Badge>
        {s.extension.years ? <span className="text-muted">{s.extension.years} year(s){s.extension.decided_on ? `, decided on ${fmtDate(s.extension.decided_on)}` : ""}</span> : null}
        <Button size="sm" onClick={onExtension}>Record the decision</Button>
      </div>
      {s.reminders && s.reminders.length > 0 && (
        <p className="text-xs text-muted">
          Reminders while no decision is recorded: {s.reminders.map((r) => `${r.months_before} months before (${fmtDate(r.date)})`).join(", ")}. They go through the alerts (kind "Rental scheme commitment ending"); an external message only says that an alert exists.
        </p>
      )}
      {s.warnings?.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
    </div>
  );
}

function ExtensionDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [decision, setDecision] = useState<"extend" | "not_extend" | "undecided">("extend");
  const [years, setYears] = useState("3");
  const [rate, setRate] = useState("");
  const [note, setNote] = useState("");
  const body = useMemo(
    () => ({ decision, years: decision === "extend" ? Number(years) || undefined : undefined, additional_rate_pct: decision === "extend" && rate ? Number(rate) : undefined, note: note || undefined }),
    [decision, years, rate, note],
  );
  const ready = decision !== "extend" || Number(years) >= 1;
  const pv = useDryRun<EditResult>(`/rental/${id}/extension`, body, ready);
  const save = useWrite(() => api.post<EditResult>(`/rental/${id}/extension`, body), { success: "Decision recorded", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Decision at the end of the commitment" description="Recorded in the memory (with its history); the reminders stop once a decision is recorded."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Decision">{(f) => <Select id={f} value={decision} onChange={(e) => setDecision(e.target.value as typeof decision)}><option value="extend">Extend the commitment</option><option value="not_extend">Do not extend</option><option value="undecided">Not decided yet</option></Select>}</Field>
        {decision === "extend" && <Field label="Extension (years)">{(f) => <Input id={f} inputMode="numeric" value={years} onChange={(e) => setYears(e.target.value)} />}</Field>}
        {decision === "extend" && <Field label="Extra reduction rate of the extension (%)" hint="as the scheme states it, optional">{(f) => <Input id={f} inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />}</Field>}
        <Field label="Note (optional)" className="sm:col-span-2">{(f) => <Input id={f} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

function VacancyDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");
  const body = useMemo(() => ({ start, end: end || undefined, note: note || undefined }), [start, end, note]);
  const ready = /^\d{4}-\d{2}-\d{2}$/.test(start) && (!end || /^\d{4}-\d{2}-\d{2}$/.test(end));
  const pv = useDryRun<EditResult>(`/rental/${id}/vacancy`, body, ready);
  const save = useWrite(() => api.post<EditResult>(`/rental/${id}/vacancy`, body), { success: "Vacancy recorded", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Declare a vacancy" description="A period you know the property was not let (works, between two tenants). The months it covers are not reported as a missing rent."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="From" hint="YYYY-MM-DD">{(f) => <Input id={f} value={start} onChange={(e) => setStart(e.target.value)} placeholder="2026-03-01" />}</Field>
        <Field label="To (blank: still empty)" hint="YYYY-MM-DD">{(f) => <Input id={f} value={end} onChange={(e) => setEnd(e.target.value)} placeholder="2026-04-30" />}</Field>
        <Field label="Note (optional)" className="sm:col-span-2">{(f) => <Input id={f} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ tax year (E15-4) */
function TaxTab({ id }: { id: string }) {
  const [year, setYear] = useState<number | undefined>();
  const q = useRentalTax(id, year);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const now = new Date();
  const first = now.getMonth() >= 9 ? now.getFullYear() : now.getFullYear() - 1;
  const years = [first, first - 1, first - 2, first - 3];
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Income year" value={String(year ?? first)} onChange={(v) => setYear(Number(v))} options={years.map((y) => ({ value: String(y), label: String(y) }))} />
        <span className="text-xs text-muted">The income year is declared the spring after it.</span>
      </div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(t) =>
          t.status !== "computed" ? (
            <Card><EmptyState icon={<FileText className="size-6" />} title="Not modelled">{t.note}</EmptyState></Card>
          ) : (
            <div className="grid gap-4">
              <Notice tone="warn">Candidates for the rental-income return, computed from the bank flows: not a return, nothing is filed, not tax advice. {t.disclaimer}</Notice>
              {(t.months_incomplete?.length || t.months_missing_data?.length) ? (
                <Notice tone="info">The year is not fully covered by the property account{t.months_incomplete?.length ? ` (partial: ${t.months_incomplete.join(", ")})` : ""}{t.months_missing_data?.length ? ` (no data: ${t.months_missing_data.join(", ")})` : ""}: the figures are a lower bound for the rents and for the costs.</Notice>
              ) : null}
              <div className="grid gap-4 lg:grid-cols-2">
                <Card title="Micro-foncier" subtitle={`Flat abatement of ${t.micro_foncier?.abatement_pct} % on the gross rents`}>
                  <Row label="Gross rents" v={t.micro_foncier!.gross_rents} />
                  <Row label="Abatement" v={t.micro_foncier!.abatement} />
                  <Row label="Taxable (candidate)" v={t.micro_foncier!.taxable} bold />
                  {!t.micro_foncier!.within_ceiling && <p className="mt-2 text-xs text-warn">The household's gross rents (<Money v={t.micro_foncier!.household_gross_rents} />) exceed the ceiling (<Money v={t.micro_foncier!.ceiling} />): this regime would not apply.</p>}
                </Card>
                <Card title="Real regime" subtitle="Gross rents less the costs the return lets you deduct">
                  <Row label="Gross rents" v={t.reel!.gross_rents} />
                  {t.reel!.deductible.map((i) => (
                    <div key={i.item} className="flex items-baseline justify-between gap-3 border-t border-border py-1.5 text-sm" title={`${i.source}: ${i.bound}`}>
                      <span>− {i.item}<span className="block text-[11px] text-faint">{i.bound}</span></span>
                      <Money v={i.amount} />
                    </div>
                  ))}
                  <Row label="Net (candidate)" v={t.reel!.net} bold />
                  {t.reel!.unknown.map((u) => <p key={u} className="mt-2 text-xs text-warn">Unknown: {u}</p>)}
                </Card>
              </div>
              <Card title="Which is lower?" subtitle="The lower taxable figure between the two candidates; the choice of a regime has consequences over several years: check with the tax office or an adviser.">
                <p className="text-sm">The <b>{t.lower_taxable_candidate === "reel" ? "real regime" : "micro-foncier"}</b> gives the lower taxable figure ({t.reel!.net} against {t.micro_foncier!.taxable}; difference <Money v={t.difference} signed colored />).</p>
              </Card>
              <SchemeReductionCard t={t} />
              <Card title="Documents to gather" subtitle="A checklist for yourself (not saved)">
                <ul className="grid gap-1.5 text-sm">
                  {t.documents?.map((d) => (
                    <li key={d.id}>
                      <label className="flex items-start gap-2">
                        <input type="checkbox" className="mt-1" checked={!!done[d.id]} onChange={(e) => setDone({ ...done, [d.id]: e.target.checked })} />
                        <span>{d.item} <span className="text-faint">({d.from})</span></span>
                      </label>
                    </li>
                  ))}
                </ul>
                <ul className="mt-3 grid gap-1 text-xs text-muted">{t.notes?.map((n) => <li key={n}>{n}</li>)}</ul>
              </Card>
            </div>
          )
        }
      </Async>
    </div>
  );
}

function Row({ label, v, bold }: { label: string; v: string | null | undefined; bold?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 border-t border-border py-1.5 text-sm first:border-0 ${bold ? "font-semibold" : ""}`}>
      <span>{label}</span>
      <Money v={v} />
    </div>
  );
}

function SchemeReductionCard({ t }: { t: import("@/api/types").RentalTax }) {
  const r = t.scheme_reduction;
  if (!r || r.status === "no_scheme") return null;
  return (
    <Card title="Scheme reduction (candidate)" subtitle="Computed from the price and the total rate you declared; a tax reduction, not a deduction from the rental income">
      {r.status === "needs_fields" ? (
        <p className="text-sm text-muted">Missing: {r.missing?.join(", ")}.</p>
      ) : (
        <div className="grid gap-2 text-sm">
          <p>
            For {t.year}: <b><Money v={r.candidate} /></b> {r.in_window ? "" : "(the year is outside the commitment)"}.
          </p>
          <p className="text-xs text-muted">
            {r.rate_pct} % of <Money v={r.base} /> = <Money v={r.total} /> spread over {r.years} year(s), {r.first_year} to {r.last_year}.
          </p>
          {r.notes?.map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ renegotiate or sell (E15-5) */
function SellTab({ id }: { id: string }) {
  const [rate, setRate] = useState("");
  const [day, setDay] = useState("");
  const [fees, setFees] = useState("");
  const params = useMemo(() => ({ ...(rate ? { market_rate: Number(rate) } : {}), ...(rate && day ? { market_date: day } : {}), ...(fees ? { bank_fees: Number(fees) } : {}) }), [rate, day, fees]);
  const q = useRentalIndicators(id, params);
  const [save, setSave] = useState(false);
  return (
    <div className="grid gap-4">
      <Card title="Market rate" subtitle="A rate you looked up for a loan of similar duration (your own quote, a comparator). The coach never looks it up.">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Market rate (%)">{(f) => <Input id={f} inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="3.1" />}</Field>
          <Field label="As of" hint="YYYY-MM-DD">{(f) => <Input id={f} value={day} onChange={(e) => setDay(e.target.value)} placeholder="2026-10-01" />}</Field>
          <Field label="Fees of a new loan (EUR)" hint="bank + guarantee, optional">{(f) => <Input id={f} inputMode="decimal" value={fees} onChange={(e) => setFees(e.target.value)} />}</Field>
          <div className="flex items-end"><Button disabled={!rate || !/^\d{4}-\d{2}-\d{2}$/.test(day)} onClick={() => setSave(true)}>Remember this rate</Button></div>
        </div>
      </Card>
      <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <div className="grid gap-4 lg:grid-cols-3">
              <Card title="Loan rate vs market">
                {d.loan_rate.loan_rate_pct === undefined ? <p className="text-sm text-muted">Missing: {d.loan_rate.missing?.join(", ")}.</p> : (
                  <div className="grid gap-1 text-sm">
                    <Stat label="Your loan" value={`${d.loan_rate.loan_rate_pct} %`} hint={d.loan_rate.market_rate_pct !== undefined ? `market ${d.loan_rate.market_rate_pct} %, gap ${d.loan_rate.gap_pts?.toFixed(2)} point(s)` : "enter a market rate"} tone={d.loan_rate.status === "above_market" ? "warn" : undefined} />
                    {d.loan_rate.reading && <p className="mt-1 text-xs text-muted">{d.loan_rate.reading}</p>}
                  </div>
                )}
                {d.market_rate.status === "missing" && <p className="mt-2 text-xs text-faint">{d.market_rate.note}</p>}
                {d.market_rate.warning && <p className="mt-2 text-xs text-warn">{d.market_rate.warning}</p>}
              </Card>
              <Card title="End of the commitment">
                <Stat label="State" value={d.commitment.state === "unknown" ? "unknown" : d.commitment.state.replace("_", " ")} hint={d.commitment.end_date ? `ends ${fmtDate(d.commitment.end_date)}${d.commitment.months_left !== undefined ? `, about ${d.commitment.months_left} months` : ""}` : "record the start and the length"} tone={d.commitment.decision_needed ? "warn" : undefined} />
              </Card>
              <Card title="Net equity" subtitle="Declared value − capital still due, before selling costs, penalty and tax">
                {d.equity.status === "computed" ? (
                  <Stat label="Net equity" value={<Money v={d.equity.net_equity} colored />} hint={`value ${fmtMoney(d.equity.value)} − capital due ${fmtMoney(d.equity.outstanding)} (${d.equity.equity_share_pct} % of the value)`} />
                ) : <p className="text-sm text-muted">Unknown: missing {d.equity.missing?.join(", ")}.</p>}
                {d.equity.value_warning && <p className="mt-2 text-xs text-warn">{d.equity.value_warning}</p>}
              </Card>
            </div>
            {d.loan_rate.renegotiation?.status === "computed" && (
              <Card title="Renegotiation on the real schedule" subtitle="An estimate with the fees you gave (none by default): your bank or a broker gives the binding figures">
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label="Instalment" value={`${d.loan_rate.renegotiation.current_payment} → ${d.loan_rate.renegotiation.new_payment}`} hint="EUR a month" />
                  <Stat label="Costs" value={<Money v={d.loan_rate.renegotiation.total_costs} />} hint={`penalty ${fmtMoney(d.loan_rate.renegotiation.penalty)}`} />
                  <Stat label="Net saving" value={<Money v={d.loan_rate.renegotiation.net_saving} colored signed />} />
                  <Stat label="Break-even" value={d.loan_rate.renegotiation.break_even_months === null ? "never" : `${d.loan_rate.renegotiation.break_even_months} months`} />
                </div>
                {d.loan_rate.renegotiation_note && <p className="mt-2 text-xs text-faint">{d.loan_rate.renegotiation_note}</p>}
              </Card>
            )}
            <Card title="Indicators" subtitle={`Trailing effort d'épargne: ${fmtMoney(d.trailing_effort)} a month`}>
              <ul className="grid gap-2 text-sm">{d.signals.length === 0 ? <li className="text-muted">No indicator yet: record the loan, the valuation, the commitment and a market rate.</li> : d.signals.map((s) => <li key={s.id}>{s.reading}</li>)}</ul>
              <ul className="mt-3 grid gap-1 text-xs text-muted">{d.scenarios.map((s) => <li key={s}>{s}</li>)}</ul>
              <p className="mt-3 text-xs text-faint">{d.disclaimer}</p>
            </Card>
          </div>
        )}
      </Async>
      {save && <MarketRateDialog id={id} rate={Number(rate)} day={day} onClose={() => setSave(false)} />}
    </div>
  );
}

function MarketRateDialog({ id, rate, day, onClose }: { id: string; rate: number; day: string; onClose: () => void }) {
  const [source, setSource] = useState("");
  const body = useMemo(() => ({ rate_pct: rate, as_of: day, source: source || undefined }), [rate, day, source]);
  const pv = useDryRun<EditResult>(`/rental/${id}/market-rate`, body, true);
  const save = useWrite(() => api.post<EditResult>(`/rental/${id}/market-rate`, body), { success: "Market rate recorded", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Remember this market rate" description="Written to the property in the memory with its date; after 30 days it is flagged as possibly outdated."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <Field label="Where does it come from? (optional)">{(f) => <Input id={f} value={source} onChange={(e) => setSource(e.target.value)} placeholder="a quote, a comparator, a date" />}</Field>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ the facts of a property */
interface FactField { path: string; label: string; hint?: string; kind: "text" | "number" | "date" | "int" }
const FACT_FIELDS: FactField[] = [
  { path: "account", label: "Bank account (uid or label)", hint: "its purpose should be rental (Household page)", kind: "text" },
  { path: "loan", label: "Loan id", hint: "the loan file that financed it", kind: "text" },
  { path: "value", label: "Value today (EUR)", kind: "number" },
  { path: "as_of", label: "Value as of", hint: "YYYY-MM-DD", kind: "date" },
  { path: "rent_monthly", label: "Monthly rent of the lease (EUR)", kind: "number" },
  { path: "purchase_price", label: "Purchase price (EUR)", kind: "number" },
  { path: "purchase_date", label: "Purchase date", hint: "YYYY-MM-DD", kind: "date" },
  { path: "scheme", label: "Scheme name", hint: "e.g. the name on your deed: free text", kind: "text" },
  { path: "commitment.start_date", label: "Commitment start", hint: "YYYY-MM-DD, the first lease", kind: "date" },
  { path: "commitment.years", label: "Commitment length (years)", hint: "6, 9 or 12 for a Pinel-type scheme", kind: "int" },
  { path: "commitment.surface_m2", label: "Surface (m²)", kind: "number" },
  { path: "commitment.rent_cap_m2", label: "Rent cap per m² (EUR a month)", hint: "as you computed it from the scheme's table", kind: "number" },
  { path: "commitment.rent_cap_monthly", label: "Rent cap per month (EUR)", hint: "wins over the cap per m²", kind: "number" },
  { path: "commitment.tenant_income_limit", label: "Tenant income limit (EUR a year)", kind: "number" },
  { path: "commitment.tenant_income", label: "Tenant reference income (EUR a year)", kind: "number" },
  { path: "commitment.reduction_rate_pct", label: "Total reduction rate over the commitment (%)", hint: "from the scheme's table", kind: "number" },
  { path: "commitment.reduction_base_cap", label: "Ceiling of the eligible price (EUR)", hint: "optional, as the scheme states it", kind: "number" },
  { path: "commitment.reduction_first_year", label: "First tax year of the reduction", kind: "int" },
];

function currentValue(d: RentalDetail, path: string): string {
  const parts = path.split(".");
  let cur: unknown = d.asset;
  for (const p of parts) cur = cur && typeof cur === "object" ? (cur as Record<string, unknown>)[p] : undefined;
  return cur === undefined || cur === null ? "" : String(cur);
}

function FactsDialog({ d, onClose }: { d: RentalDetail; onClose: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(FACT_FIELDS.map((f) => [f.path, currentValue(d, f.path)])));
  const { fields, bad } = useMemo(() => {
    const out: Record<string, unknown> = {};
    const bad: string[] = [];
    for (const f of FACT_FIELDS) {
      const raw = (vals[f.path] ?? "").trim();
      if (raw === currentValue(d, f.path).trim() || raw === "") continue;
      let v: unknown = raw;
      if (f.kind === "number" || f.kind === "int") {
        const n = Number(raw.replace(",", "."));
        if (!Number.isFinite(n) || (f.kind === "int" && !Number.isInteger(n))) { bad.push(f.label); continue; }
        v = n;
      } else if (f.kind === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(raw)) { bad.push(f.label); continue; }
      const parts = f.path.split(".");
      if (parts.length === 1) out[parts[0]] = v;
      else out[parts[0]] = { ...((out[parts[0]] as object) ?? {}), [parts[1]]: v };
    }
    return { fields: out, bad };
  }, [vals, d]);
  const changed = Object.keys(fields).length > 0;
  const body = useMemo(() => ({ fields, reason: "rental property facts (Rental page)" }), [fields]);
  const pv = useDryRun<EditResult>(`/memory/assets/${d.id}`, body, changed && bad.length === 0, "put");
  const save = useWrite(() => api.put<EditResult>(`/memory/assets/${d.id}`, body), { success: "Facts saved", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Facts of the property" size="lg" description="Only what you change is written (blank fields are left as they are). These are YOUR figures from the deed and the scheme's table: the coach never looks them up."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!changed || bad.length > 0 || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {FACT_FIELDS.map((f) => (
          <Field key={f.path} label={f.label} hint={f.hint}>{(id) => <Input id={id} value={vals[f.path] ?? ""} onChange={(e) => setVals({ ...vals, [f.path]: e.target.value })} inputMode={f.kind === "number" || f.kind === "int" ? "decimal" : undefined} />}</Field>
        ))}
      </div>
      <div className="mt-3">
        {bad.length > 0 && <Notice tone="neg">Not a valid value: {bad.join(", ")}.</Notice>}
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} />}
      </div>
    </Dialog>
  );
}

function DeclareDialog({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [id, setId] = useState("");
  const [account, setAccount] = useState("");
  const [loan, setLoan] = useState("");
  const body = useMemo(() => ({ fields: { kind: "real_estate_rental", ...(account ? { account } : {}), ...(loan ? { loan } : {}) }, reason: "declare a rental property (Rental page)" }), [account, loan]);
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id);
  const pv = useDryRun<EditResult>(`/memory/assets/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/memory/assets/${id}`, body), { success: "Property declared", onSuccess: () => onDone(id) });
  return (
    <Dialog open onClose={onClose} title="Declare a rental property" description="An asset of kind real_estate_rental in your memory. Add the scheme and the other facts afterwards (Edit facts)."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Declare</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Id" hint="lowercase letters, digits, - or _ (no address)">{(f) => <Input id={f} value={id} onChange={(e) => setId(e.target.value)} placeholder="rental-flat-1" />}</Field>
        <Field label="Bank account (optional)" hint="uid or label">{(f) => <Input id={f} value={account} onChange={(e) => setAccount(e.target.value)} />}</Field>
        <Field label="Loan id (optional)">{(f) => <Input id={f} value={loan} onChange={(e) => setLoan(e.target.value)} />}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}
