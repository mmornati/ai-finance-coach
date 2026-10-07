import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  if (!alerts.length) return <p className="text-sm text-muted">{t("loan.noAlert")}</p>;
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
  const { t } = useTranslation();
  const lease = loan.kind === "loa" || loan.kind === "lld";
  const [tab, setTab] = useState<Tab>(lease ? "lease" : "schedule");
  const detail = useGet<LoanDetail>(`/loans/${loan.id}`);
  const tabs: { value: Tab; label: string; badge?: React.ReactNode }[] = [
    ...(lease ? [{ value: "lease" as Tab, label: t("loan.tab.lease") }] : [{ value: "schedule" as Tab, label: t("loan.tab.schedule") }]),
    { value: "payments", label: t("loan.tab.payments"), badge: loan.alerts.length ? <Badge tone="warn">{loan.alerts.length}</Badge> : undefined },
    ...(lease ? [] : [{ value: "scenario" as Tab, label: t("loan.tab.scenario") }]),
    ...(lease ? [] : [{ value: "suggestions" as Tab, label: t("loan.tab.suggestions"), badge: loan.inferred.length ? <Badge tone="info">{loan.inferred.length}</Badge> : undefined }]),
  ];
  return (
    <Dialog open onClose={onClose} size="lg" title={`${loan.lender ?? loan.id}${loan.asset ? ` · ${loan.asset}` : ""}`} description={t("loan.dialogDescription")}
      footer={<><Button variant="ghost" onClick={onClose}>{t("loan.close")}</Button><Button onClick={onEdit}>{t("loan.edit")}</Button></>}>
      <div className="grid gap-4">
        <Tabs label={t("loan.sections")} value={tab} onChange={setTab} tabs={tabs} />
        {detail.isLoading && <Spinner label={t("loan.loadingSchedule")} />}
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
  const { t } = useTranslation();
  const s = d.schedule;
  const [all, setAll] = useState(false);
  if (s.status !== "computed")
    return (
      <Notice tone="warn" title={t("loan.schedule.none")}>
        {s.status === "not_applicable" ? s.alternative : <>
          {t("loan.schedule.toCompute")} <strong>{(s.missing ?? []).join(", ")}</strong>. {s.alternative}
          <div className="mt-2"><Button size="sm" onClick={onEdit}>{t("loan.schedule.fillIn")}</Button></div>
        </>}
      </Notice>
    );
  const rows = s.rows ?? [];
  const nextIdx = rows.findIndex((r) => !r.made);
  const shown: ScheduleRow[] = all ? rows : rows.slice(Math.max(0, nextIdx - 1), Math.max(0, nextIdx - 1) + 12);
  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label={t("loan.schedule.instalment")} value={<Money v={s.payment} />} hint={s.mode === "from_outstanding" ? t("loan.schedule.fromOutstanding") : t("loan.schedule.instalments", { count: s.term_instalments })} />
        <Stat label={t("loan.schedule.capitalDue")} value={<Money v={s.remaining_capital} round />} hint={t("loan.schedule.instalmentsLeft", { count: s.remaining_instalments })} />
        <Stat label={t("loan.schedule.nextDue")} value={s.next_due ? fmtDate(s.next_due, "dayMonth") : "–"} hint={s.last_due ? t("loan.schedule.last", { date: fmtDate(s.last_due, "medium") }) : undefined} />
        <Stat label={s.total_cost ? t("loan.schedule.totalCost") : t("loan.schedule.interestToPay")} value={<Money v={s.total_cost ?? s.remaining_interest} round />} hint={s.total_interest ? t("loan.schedule.interestHint", { amount: fmtMoneyShort(s.total_interest) }) : t("loan.schedule.pastUnknown")} />
      </div>
      {s.approximate && <Notice tone="warn">{t("loan.schedule.variable")}</Notice>}
      {s.outstanding_check && <Notice tone="warn" title={t("loan.schedule.differsTitle")}>{t("loan.schedule.differs", { computed: fmtMoneyShort(s.outstanding_check.computed), date: fmtDate(s.outstanding_check.as_of), declared: fmtMoneyShort(s.outstanding_check.declared), hint: s.outstanding_check.hint })}</Notice>}
      {s.payment_check?.status === "differs" && <Notice tone="warn">{s.payment_check.hint ? t("loan.schedule.paymentDiffersHint", { hint: s.payment_check.hint }) : t("loan.schedule.paymentDiffers")}</Notice>}
      <div>
        <h3 className="mb-1.5 text-sm font-semibold">{t("loan.schedule.byYear")}</h3>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-muted"><tr><th className="px-3 py-1.5 font-medium">{t("loan.schedule.year")}</th><th className="px-3 py-1.5 text-right font-medium">{t("loan.schedule.instalmentsTitle")}</th><th className="px-3 py-1.5 text-right font-medium">{t("loan.schedule.interest")}</th><th className="px-3 py-1.5 text-right font-medium">{t("loan.schedule.insurance")}</th><th className="px-3 py-1.5 text-right font-medium">{t("loan.schedule.principal")}</th></tr></thead>
            <tbody>
              {s.by_year.map((y) => (
                <tr key={y.year} className="border-t border-border"><td className="px-3 py-1.5">{y.year}{y.partial && <span className="text-xs text-faint">{t("loan.schedule.partialYear")}</span>}</td><td className="num px-3 py-1.5 text-right">{y.instalments}</td><td className="num px-3 py-1.5 text-right font-medium"><Money v={y.interest} /></td><td className="num px-3 py-1.5 text-right"><Money v={y.insurance} /></td><td className="num px-3 py-1.5 text-right"><Money v={y.principal} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-xs text-faint">{t("loan.schedule.yearNote")}</p>
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between"><h3 className="text-sm font-semibold">{t("loan.schedule.instalmentsTitle")}</h3><Button size="sm" variant="ghost" onClick={() => setAll((v) => !v)}>{all ? t("loan.schedule.aroundToday") : t("loan.schedule.all", { count: rows.length })}</Button></div>
        <div className="max-h-72 overflow-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-2 text-left text-muted"><tr><th className="px-2 py-1.5 font-medium">#</th><th className="px-2 py-1.5 font-medium">{t("loan.schedule.due")}</th><th className="px-2 py-1.5 text-right font-medium">{t("loan.schedule.interest")}</th><th className="px-2 py-1.5 text-right font-medium">{t("loan.schedule.principal")}</th><th className="px-2 py-1.5 text-right font-medium">{t("loan.schedule.insurance")}</th><th className="px-2 py-1.5 text-right font-medium">{t("loan.schedule.debited")}</th><th className="px-2 py-1.5 text-right font-medium">{t("loan.schedule.capitalAfter")}</th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.k} className={`border-t border-border ${r.made ? "text-muted" : ""}`}><td className="px-2 py-1">{r.k}{r.kind === "deferral" && t("loan.schedule.deferral")}</td><td className="px-2 py-1">{fmtDate(r.due, "medium")}</td><td className="num px-2 py-1 text-right"><Money v={r.interest} /></td><td className="num px-2 py-1 text-right"><Money v={r.principal} /></td><td className="num px-2 py-1 text-right"><Money v={r.insurance} /></td><td className="num px-2 py-1 text-right"><Money v={r.total} /></td><td className="num px-2 py-1 text-right"><Money v={r.balance} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Disclosure summary={t("loan.schedule.how")}>
        <ul className="list-disc pl-5 text-sm text-muted">{(s.assumptions ?? []).map((a) => <li key={a}>{a}</li>)}</ul>
      </Disclosure>
    </div>
  );
}

const fmtMoneyShort = (v: string | null | undefined) => (v == null ? "?" : `${fmtNumber(Number(v), 2)} EUR`);

/* ------------------------------------------------------------------ payments */

function PaymentsTab({ d, loan }: { d: LoanDetail; loan: Liability }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-4">
      <div>
        <h3 className="mb-1.5 text-sm font-semibold">{t("loan.payments.alerts")}</h3>
        <AlertList alerts={d.alerts} />
      </div>
      <div>
        <h3 className="mb-1.5 text-sm font-semibold">{t("loan.payments.found")}</h3>
        {!loan.payment_match ? <Notice tone="warn">{t("loan.payments.noLabel")}</Notice> : d.payments.count === 0 ? <p className="text-sm text-muted">{t("loan.payments.noMatch", { pattern: loan.payment_match })}</p> : (
          <>
            <p className="mb-1.5 text-xs text-muted"><Trans i18nKey="loan.payments.summary" count={d.payments.count} values={{ pattern: loan.payment_match, date: fmtDate(d.payments.last, "medium") }} components={{ amount: <Money v={d.payments.median_amount} /> }} /></p>
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
  const { t } = useTranslation();
  const [type, setType] = useState<ScenarioType>("prepay");
  const [v, setV] = useState({ amount: "", on: "", newRate: "", variant: "", bankFees: "", guaranteeFees: "", penalty: "", alternative: "", fees: "" });
  const set = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV((c) => ({ ...c, [k]: e.target.value }));
  const body = useMemo(() => ({
    type, amount: num(v.amount), on: v.on || undefined, new_rate: num(v.newRate), variant: v.variant || undefined, bank_fees: num(v.bankFees) ?? 0, guarantee_fees: num(v.guaranteeFees) ?? 0,
    penalty: num(v.penalty), alternative: num(v.alternative), fees: num(v.fees) ?? 0,
  }), [type, v]);
  const [result, setResult] = useState<{ scenario: string; result: ScenarioResult; saved_insight: string | null } | null>(null);
  const run = useWrite(() => api.post<{ scenario: string; result: ScenarioResult; saved_insight: string | null }>(`/loans/${loan.id}/scenario`, { ...body, save: false }), { invalidate: false, onSuccess: setResult });
  const save = useWrite(() => api.post<{ saved_insight: string }>(`/loans/${loan.id}/scenario`, { ...body, save: true }), { success: t("loan.scenario.stored") });
  const ready = type === "prepay" ? body.amount !== undefined : type === "renegotiate" ? body.new_rate !== undefined : body.alternative !== undefined;
  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted">{t("loan.scenario.intro")}</p>
      <Tabs label={t("loan.scenario.label")} value={type} onChange={(next) => { setType(next); setResult(null); }} tabs={[{ value: "prepay", label: t("loan.scenario.prepay") }, { value: "renegotiate", label: t("loan.scenario.renegotiate") }, { value: "insurance", label: t("loan.scenario.insurance") }]} />
      <div className="grid gap-3 sm:grid-cols-2">
        {type === "prepay" && <>
          <Field label={t("loan.scenario.amountToRepay")}>{(i) => <Input id={i} inputMode="decimal" value={v.amount} onChange={set("amount")} />}</Field>
          <Field label={t("loan.scenario.on")} hint={t("loan.scenario.blankToday")}>{(i) => <Input id={i} type="date" value={v.on} onChange={set("on")} />}</Field>
          <Field label={t("loan.scenario.penaltyMortgage")} hint={t("loan.scenario.penaltyMortgageHint")}>{(i) => <Input id={i} inputMode="decimal" value={v.penalty} onChange={set("penalty")} />}</Field>
        </>}
        {type === "renegotiate" && <>
          <Field label={t("loan.scenario.offeredRate")}>{(i) => <Input id={i} inputMode="decimal" value={v.newRate} onChange={set("newRate")} />}</Field>
          <Field label={t("loan.scenario.procedure")}>{(i) => <Select id={i} value={v.variant} onChange={set("variant")}><option value="">{t("loan.scenario.procDefault")}</option><option value="renegotiation">{t("loan.scenario.procRenegotiation")}</option><option value="rachat">{t("loan.scenario.procRachat")}</option><option value="surroga">{t("loan.scenario.procSurroga")}</option></Select>}</Field>
          <Field label={t("loan.scenario.bankFees")}>{(i) => <Input id={i} inputMode="decimal" value={v.bankFees} onChange={set("bankFees")} />}</Field>
          <Field label={t("loan.scenario.guaranteeFees")}>{(i) => <Input id={i} inputMode="decimal" value={v.guaranteeFees} onChange={set("guaranteeFees")} />}</Field>
          <Field label={t("loan.scenario.penaltyMortgage")} hint={t("loan.scenario.penaltyHint")}>{(i) => <Input id={i} inputMode="decimal" value={v.penalty} onChange={set("penalty")} />}</Field>
        </>}
        {type === "insurance" && <>
          <Field label={t("loan.scenario.otherPolicy")} hint={t("loan.scenario.otherPolicyHint")}>{(i) => <Input id={i} inputMode="decimal" value={v.alternative} onChange={set("alternative")} />}</Field>
          <Field label={t("loan.scenario.switchingFees")}>{(i) => <Input id={i} inputMode="decimal" value={v.fees} onChange={set("fees")} />}</Field>
        </>}
      </div>
      <div className="flex gap-2">
        <Button variant="primary" disabled={!ready} busy={run.isPending} onClick={() => run.mutate(undefined as never)}><Calculator className="size-4" aria-hidden /> {t("loan.scenario.calculate")}</Button>
        {result?.result.status === "computed" && <Button busy={save.isPending} onClick={() => save.mutate(undefined as never)}><Lightbulb className="size-4" aria-hidden /> {t("loan.scenario.store")}</Button>}
      </div>
      {result && <ScenarioView type={result.scenario as ScenarioType} r={result.result} />}
    </div>
  );
}

function ScenarioView({ type, r }: { type: ScenarioType; r: ScenarioResult }) {
  const { t } = useTranslation();
  if (r.status === "needs_fields") return <Notice tone="warn" title={t("loan.scenario.notEnough")}>{r.missing?.length ? <>{t("loan.scenario.record")} <strong>{r.missing.join(", ")}</strong>. </> : null}{r.alternative ?? r.note}</Notice>;
  if (r.status === "payoff") return <Notice tone="info">{r.note}</Notice>;
  if (r.status === "nothing_left") return <Notice tone="info">{t("loan.scenario.nothingLeft")}</Notice>;
  const months = (n: number | null | undefined) => (n === null || n === undefined ? t("loan.scenario.never") : n === 0 ? t("loan.scenario.immediately") : t("loan.scenario.months", { count: n }));
  const basis = <span className="text-muted" />;
  if (type === "prepay")
    return (
      <div className="grid gap-3">
        <p className="text-sm">
          <Trans i18nKey="loan.scenario.prepaySummary" count={r.remaining_instalments} values={{ date: fmtDate(r.date, "medium"), basis: r.penalty_basis }}
            components={{ amount: <Money v={r.amount} />, before: <Money v={r.capital_before} round />, after: <Money v={r.capital_after} round />, instalment: <Money v={r.instalment} />, penalty: <strong><Money v={r.penalty} /></strong>, basis }} />
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {(r.options ?? []).map((o) => (
            <div key={o.mode} className="rounded-lg border border-border p-3 text-sm">
              <div className="font-semibold">{o.label}</div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                <dt className="text-muted">{t("loan.scenario.newInstalment")}</dt><dd className="num text-right"><Money v={o.new_instalment} /></dd>
                <dt className="text-muted">{t("loan.scenario.monthlyChange")}</dt><dd className="num text-right"><Money v={o.monthly_change} signed /></dd>
                <dt className="text-muted">{t("loan.scenario.monthsSaved")}</dt><dd className="num text-right">{o.months_saved}</dd>
                <dt className="text-muted">{t("loan.scenario.interestSaved")}</dt><dd className="num text-right"><Money v={o.interest_saved} /></dd>
                <dt className="text-muted">{t("loan.scenario.insuranceSaved")}</dt><dd className="num text-right"><Money v={o.insurance_saved} /></dd>
                <dt className="text-muted">{t("loan.scenario.netOfPenalty")}</dt><dd className="num text-right font-semibold"><Money v={o.net_saving} signed colored /></dd>
                <dt className="text-muted">{t("loan.scenario.breakEven")}</dt><dd className="num text-right">{months(o.break_even_months)}</dd>
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
        <p><Trans i18nKey="loan.scenario.renegotiateLine" values={{ variant: r.variant, newRate: r.new_rate_pct, currentRate: r.current_rate_pct }}
          components={{ current: <Money v={r.current_payment} />, next: <Money v={r.new_payment} />, saving: <Money v={r.monthly_saving} /> }} /></p>
        <p><Trans i18nKey="loan.scenario.renegotiateCosts" values={{ basis: r.penalty_basis, breakEven: months(r.break_even_months) }}
          components={{ gross: <Money v={r.gross_interest_saving} />, costs: <Money v={r.total_costs} />, penalty: <Money v={r.penalty} />, basis, net: <strong><Money v={r.net_saving} signed colored /></strong> }} /></p>
        <p className="text-muted">{String(r.verdict ?? "").replace(/_/g, " ")}</p>
        {(r.notes ?? []).map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
      </div>
    );
  return (
    <div className="grid gap-2 text-sm">
      <p><Trans i18nKey="loan.scenario.insuranceLine" values={{ months: r.remaining_months, verdict: String(r.verdict ?? "").replace(/_/g, " ") }}
        components={{ current: <Money v={r.current_monthly} />, alternative: <Money v={r.alternative_monthly} />, saving: <Money v={r.monthly_saving} />, net: <strong><Money v={r.net_saving} signed colored /></strong> }} /></p>
      {(r.notes ?? []).map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
    </div>
  );
}

/* ------------------------------------------------------------------ suggestions inferred from the payments (E9-1) */

function SuggestionsTab({ loan, fields, notes }: { loan: Liability; fields: InferredField[]; notes: string[] }) {
  const { t } = useTranslation();
  const [out, setOut] = useState<{ id: string; accept_command: string } | null>(null);
  const propose = useWrite(() => api.post<{ id: string; accept_command: string }>(`/loans/${loan.id}/infer/propose`, { min_confidence: "medium" }), { success: t("loan.suggestions.created"), invalidate: true, onSuccess: setOut });
  const usable = fields.filter((f) => f.confidence !== "low");
  return (
    <div className="grid gap-3">
      <Notice tone="info" title={t("loan.suggestions.title")}>{t("loan.suggestions.intro")}</Notice>
      {fields.length === 0 ? <p className="text-sm text-muted">{t("loan.suggestions.empty")}</p> : (
        <ul className="divide-y divide-border rounded-lg border border-border text-sm">
          {fields.map((f) => (
            <li key={f.field} className="flex items-start justify-between gap-3 px-3 py-2">
              <div><div className="font-medium">{f.field} = {String(f.value)} <Badge tone={f.confidence === "high" ? "pos" : f.confidence === "medium" ? "info" : "warn"}>{t("loan.suggestions.badge", { confidence: f.confidence })}</Badge></div><div className="text-xs text-muted">{f.method}{f.note ? ` - ${f.note}` : ""}</div></div>
            </li>
          ))}
        </ul>
      )}
      {notes.map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
      {usable.length > 0 && <Button variant="primary" busy={propose.isPending} onClick={() => propose.mutate(undefined as never)}><FileSpreadsheet className="size-4" aria-hidden /> {t("loan.suggestions.queue")}</Button>}
      {out && <div className="grid gap-1.5"><p className="text-sm"><Trans i18nKey="loan.suggestions.waiting" values={{ id: out.id }} components={{ strong: <strong /> }} /></p><CopyCommand command={out.accept_command} /></div>}
    </div>
  );
}

/* ------------------------------------------------------------------ lease end of contract (E9-6) */

function LeaseTab({ loan, lease }: { loan: Liability; lease: LeaseStatus | null }) {
  const { t } = useTranslation();
  const [km, setKm] = useState("");
  const [date, setDate] = useState("");
  const body = { km: km ? Number.parseInt(km, 10) : 0, date: date || undefined };
  const enabled = /^\d+$/.test(km);
  const pv = useDryRun<EditResult>(`/loans/${loan.id}/odometer`, body, enabled);
  const save = useWrite(() => api.post<EditResult>(`/loans/${loan.id}/odometer`, body, { dry_run: false }), { success: t("loan.lease.readingSaved"), onSuccess: () => { setKm(""); setDate(""); } });
  if (!lease) return <Spinner label={t("loan.lease.loading")} />;
  const m = lease.mileage;
  return (
    <div className="grid gap-4">
      {lease.end.known ? (
        <Notice tone={lease.end.reminder_active ? "warn" : "info"} title={t("loan.lease.endsTitle", { date: fmtDate(lease.end.end_date, "medium") })}>
          {lease.end.ended ? t("loan.lease.ended") : t("loan.lease.daysLeft", { count: lease.end.days_left })}{lease.end.reminder_active ? t("loan.lease.reminderActive") : t("loan.lease.reminderStarts", { date: fmtDate(lease.end.reminder_date, "medium") })}
        </Notice>
      ) : <Notice tone="warn" title={t("loan.lease.endUnknownTitle")}>{t("loan.lease.endUnknown")}</Notice>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label={t("loan.lease.optionPrice")} value={lease.decision.residual_value ? <Money v={lease.decision.residual_value} round /> : t("loan.unknown")} hint={lease.decision.needs?.join(", ")} />
        <Stat label={t("loan.lease.mileageLimit")} value={m.limit_km != null ? t("loan.lease.km", { km: fmtNumber(m.limit_km) }) : t("loan.unknown")} hint={m.excess_km_fee ? t("loan.lease.excessPerKm", { amount: m.excess_km_fee }) : t("loan.lease.excessUnknown")} />
        <Stat label={t("loan.lease.projected")} value={m.projected_contract_km != null ? t("loan.lease.km", { km: fmtNumber(m.projected_contract_km) }) : t("loan.lease.needsReadings")} hint={m.pace ? t("loan.lease.kmPerYear", { km: fmtNumber(m.pace.km_per_year) }) : undefined} tone={m.status === "over_limit" ? "neg" : m.status === "within_limit" ? "pos" : undefined} />
      </div>
      {m.status === "over_limit" && <Notice tone="neg" title={t("loan.lease.overTitle")}>{t("loan.lease.overBy", { km: fmtNumber(m.excess_km) })}{m.excess_cost ? <Trans i18nKey="loan.lease.overCost" components={{ cost: <Money v={m.excess_cost} /> }} /> : t("loan.lease.overNoFee")}</Notice>}
      {m.needs.length > 0 && <p className="text-xs text-muted">{t("loan.lease.needs", { needs: m.needs.join("; ") })}</p>}
      <div className="grid gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><CarFront className="size-4" aria-hidden /> {t("loan.lease.odometer")}</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("loan.lease.kilometres")}>{(i) => <Input id={i} inputMode="numeric" value={km} onChange={(e) => setKm(e.target.value)} />}</Field>
          <Field label={t("loan.scenario.on")} hint={t("loan.scenario.blankToday")}>{(i) => <Input id={i} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
        </div>
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} empty={t("itemForm.nothingChanges")} />}
        <div><Button variant="primary" disabled={!enabled || !pv.data || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}><Check className="size-4" aria-hidden /> {t("loan.lease.saveReading")}</Button></div>
        {(loan.odometer ?? []).length > 0 && <p className="text-xs text-muted">{t("loan.lease.readings", { list: (loan.odometer ?? []).map((o) => t("loan.lease.reading", { km: fmtNumber(o.km), date: fmtDate(o.date, "medium") })).join(", ") })}</p>}
      </div>
      <Disclosure summary={t("loan.lease.checklist")} defaultOpen={!!lease.end.reminder_active}>
        <ul className="list-disc pl-5 text-sm text-muted">{lease.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
        <p className="mt-2 text-xs text-faint">{t("loan.lease.checklistNote")}</p>
      </Disclosure>
    </div>
  );
}
