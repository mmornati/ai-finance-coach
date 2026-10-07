import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { BellRing, ChevronDown, FilePlus2, FileText, FileWarning, Mail, PiggyBank, Scale, ThumbsUp } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, Select, Skeleton, Stat, Textarea } from "@/components/ui";
import { useDryRun, useGet, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";
import { serverLabel } from "@/i18n/server";
import type { ContactInfo, DraftPreview, InvAlternative, InvRow, Inventory as Inv, Letter, UsageFrequency } from "@/api/types";

const FREQ: UsageFrequency[] = ["daily", "weekly", "monthly", "rarely", "never", "unknown"];
const DECISIONS = ["cancelled", "renegotiated", "switched", "downgraded", "kept"] as const;
const STATUS_TONE = { verified: "pos", pending: "warn", contradicted: "neg", not_applicable: "neutral", ambiguous: "warn" } as const;
const STATUS_LABEL: Record<keyof typeof STATUS_TONE, ParseKeys<"subscriptions">> = { verified: "inv.status.verified", pending: "inv.status.pending", contradicted: "inv.status.contradicted", not_applicable: "inv.status.not_applicable", ambiguous: "inv.status.ambiguous" };
const isDecision = (d: string): d is (typeof DECISIONS)[number] => (DECISIONS as readonly string[]).includes(d);
const isFreq = (f: string): f is UsageFrequency => (FREQ as string[]).includes(f);

function byGroup(rows: InvRow[]): Record<string, InvRow[]> {
  const out: Record<string, InvRow[]> = {};
  for (const r of rows) (out[r.group] ??= []).push(r);
  return out;
}

export default function InventoryView() {
  const [ended, setEnded] = useState(false);
  const q = useScoped<Inv>("/subs/inventory", { include_ended: ended });
  const proposed = useGet<{ proposed: { id: string; decision: string; name: string | null; before_monthly: number; after_monthly: number }[] }>("/subs/savings");
  const { t } = useTranslation("subscriptions");
  return (
    <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
      {(d) => (
        <div className="grid gap-4">
          <Card>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
              <Stat label={t("perMonth")} value={fmtMoney(d.totals.monthly)} hint={t("inv.services", { n: d.totals.services })} />
              <Stat label={t("perYear")} value={fmtMoney(d.totals.yearly, { round: true })} />
              <Stat label={t("inv.withoutFile")} value={String(d.totals.without_contract)} tone={d.totals.without_contract ? "warn" : undefined} hint={d.totals.expired_contracts ? t("inv.pastDates", { n: d.totals.expired_contracts }) : undefined} />
              <Stat label={t("inv.achieved")} value={fmtMoney(d.savings.realised_monthly)} hint={t("inv.achievedHint", { n: d.savings.verified, amount: fmtMoney(d.savings.realised_since_decisions) })} tone={Number(d.savings.realised_monthly) > 0 ? "pos" : undefined} />
              <Stat label={t("inv.reminders")} value={String(d.totals.reminders)} tone={d.totals.reminders ? "warn" : undefined} hint={d.totals.outdated_alternatives ? t("inv.outdatedOffers", { n: d.totals.outdated_alternatives }) : undefined} />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-4 text-[13px] text-muted">
              <label className="flex items-center gap-2"><input type="checkbox" checked={ended} onChange={(e) => setEnded(e.target.checked)} /> {t("inv.showStopped")}</label>
              <span>{t("inv.notListed")}</span>
            </div>
          </Card>
          {(proposed.data?.proposed ?? []).length > 0 && <ProposedDecisions items={proposed.data!.proposed} />}
          {d.rows.length === 0 ? <Card><EmptyState title={t("inv.emptyTitle")}>{t("inv.emptyBody")}</EmptyState></Card> : (
            Object.entries(byGroup(d.rows)).map(([g, rows]) => (
              <section key={g} aria-label={serverLabel("subsGroup", g, d.groups_meta.find((m) => m.id === g)?.label)} className="grid gap-3">
                <h2 className="mt-2 flex flex-wrap items-baseline gap-x-3 text-sm font-semibold text-muted">
                  {serverLabel("subsGroup", g, rows[0].group_label)}
                  {d.groups[g] && <span className="text-xs font-normal text-faint">{t("inv.groupTotals", { monthly: fmtMoney(d.groups[g].monthly), yearly: fmtMoney(d.groups[g].yearly, { round: true }) })}</span>}
                </h2>
                <ul className="grid gap-3">{rows.map((r) => <li key={r.ref}><SubRow row={r} country={d.country} /></li>)}</ul>
              </section>
            ))
          )}
          {d.notes.map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
        </div>
      )}
    </Async>
  );
}

function ProposedDecisions({ items }: { items: { id: string; decision: string; name: string | null; before_monthly: number; after_monthly: number }[] }) {
  const { t } = useTranslation("subscriptions");
  const act = useWrite((v: { id: string; what: "confirm" | "reject" }) => api.post(`/subs/decisions/${v.id}/${v.what}`, {}), { success: t("proposed.updated") });
  return (
    <Notice tone="info" title={t("proposed.title")}>
      <ul className="mt-1 grid gap-2">
        {items.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-2 text-[13px]">
            <span>{t("proposed.line", { name: p.name ?? t("proposed.aSubscription"), decision: isDecision(p.decision) ? t(`inv.decisionShort.${p.decision}`) : p.decision, before: fmtMoney(p.before_monthly), after: fmtMoney(p.after_monthly) })}</span>
            <Button size="sm" onClick={() => act.mutate({ id: p.id, what: "confirm" })}>{t("proposed.record")}</Button>
            <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: p.id, what: "reject" })}>{t("proposed.dismiss")}</Button>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs">{t("proposed.note")}</p>
    </Notice>
  );
}

function CancelBadge({ r }: { r: InvRow }) {
  const { t } = useTranslation("subscriptions");
  const c = r.cancellation;
  const date = c.earliest_effective_date ? fmtDate(c.earliest_effective_date, "medium") : null;
  if (c.can_cancel_now === true) return <Badge tone="info" title={c.method}>{t("row.canCancel")}{date ? t("row.effectiveFrom", { date }) : ""}</Badge>;
  if (c.can_cancel_now === false) return <Badge tone="warn" title={c.method}>{t("row.notYet")}{date ? t("row.earliest", { date }) : ""}</Badge>;
  return <Badge title={t("row.missing", { list: c.unknown.join(", ") || t("row.dates") })}>{t("row.moreFacts")}</Badge>;
}

function SubRow({ row: r, country }: { row: InvRow; country: string }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(false);
  const [letter, setLetter] = useState(false);
  const { t } = useTranslation("subscriptions");
  const c = r.contract;
  const freq = isFreq(r.usage.frequency) ? t(`inv.freqShort.${r.usage.frequency}`) : r.usage.frequency;
  return (
    <Card>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-start">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold">{r.name}</h3>
            {r.status === "ended" && <Badge tone="warn">{t("row.stopped", { date: fmtDate(r.last_payment, "dayMonth") })}</Badge>}
            {r.status === "contract_only" && <Badge title={t("row.contractOnlyTitle")}>{t("row.contractOnly")}</Badge>}
            {c.status === "on_file" && <Badge tone="pos">{t("row.onFile")}</Badge>}
            {c.status === "expired" && <Badge tone="warn" title={t("row.expiredTitle", { date: c.expired_on })}>{t("row.expired")}</Badge>}
            {c.status === "missing" && <Badge tone="warn"><FileWarning className="size-3" aria-hidden /> {t("row.noFile")}</Badge>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
            {r.next_charge && <span>{t("row.nextCharge", { date: fmtDate(r.next_charge, "dayMonth") })}</span>}
            {r.first_seen && <span>{t("row.paidSince", { date: fmtDate(r.first_seen, "medium") })}</span>}
            {r.price_changes.map((p) => <span key={p.id}>{t(p.direction === "increase" ? "row.priceUp" : "row.priceDown", { old: fmtMoney(p.old), new: fmtMoney(p.new), date: fmtDate(p.date, "dayMonth") })}</span>)}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CancelBadge r={r} />
            <Badge tone={r.usage.recorded ? "neutral" : "warn"} title={r.usage.note_not_measurable ?? undefined}>
              {r.usage.recorded ? (r.usage.last_used ? t("row.usageLast", { frequency: freq, date: fmtDate(r.usage.last_used, "medium") }) : t("row.usage", { frequency: freq })) : t("row.usageUnknown")}
            </Badge>
            {r.usage.signals.map((s) => (
              <Badge key={s.kind} tone="warn" title={s.measurable}><BellRing className="size-3" aria-hidden /> {s.kind === "unused_60_days" ? t("row.unused", { days: s.days }) : t("row.neverUsed")}</Badge>
            ))}
            {r.alternatives.best && <Badge tone="pos"><PiggyBank className="size-3" aria-hidden /> {t("row.cheaper", { amount: fmtMoney(r.alternatives.best.savings?.yearly, { round: true }) })}</Badge>}
            {r.alternatives.outdated > 0 && <Badge tone="warn">{t("row.outdated", { n: r.alternatives.outdated })}</Badge>}
            {r.decision && <Badge tone={STATUS_TONE[r.decision.status]} title={r.decision.reason}>{t("row.decisionStatus", { decision: isDecision(r.decision.decision) ? t(`inv.decisionShort.${r.decision.decision}`) : r.decision.decision, status: t(STATUS_LABEL[r.decision.status]) })}</Badge>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="text-right">
            <div className="num text-lg font-semibold">{r.monthly ? <><Money v={r.monthly} /> <span className="text-xs font-normal text-muted">{t("row.perMonth")}</span></> : "–"}</div>
            <div className="num text-xs text-muted">{r.yearly ? <Trans t={t} i18nKey="row.perYear" components={{ amount: <Money v={r.yearly} round /> }} /> : t("row.costUnknown")}</div>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {r.draftable && <Button size="sm" onClick={() => setDraft(true)}><FilePlus2 className="size-4" aria-hidden /> {t("row.createContract")}</Button>}
            {r.contract_id && <Button size="sm" onClick={() => setLetter(true)}><Mail className="size-4" aria-hidden /> {t("row.letter")}</Button>}
            <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{t("row.details")} <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden /></Button>
          </div>
        </div>
      </div>
      {open && <Details row={r} country={country} />}
      {draft && <DraftDialog row={r} onClose={() => setDraft(false)} />}
      {letter && <LetterDialog row={r} country={country} onClose={() => setLetter(false)} />}
    </Card>
  );
}

function Details({ row: r, country }: { row: InvRow; country: string }) {
  return (
    <div className="mt-4 grid gap-5 border-t border-border pt-4 lg:grid-cols-2">
      <CancellationPanel row={r} country={country} />
      <div className="grid content-start gap-5">
        <UsagePanel row={r} />
        <DecisionPanel row={r} />
      </div>
      <div className="lg:col-span-2"><AlternativesPanel row={r} /></div>
    </div>
  );
}

/* ------------------------------------------------------------------ E8-3: can I cancel, and how */
function CancellationPanel({ row: r, country }: { row: InvRow; country: string }) {
  const c = r.cancellation;
  const ec = c.early_termination_cost;
  const { t } = useTranslation("subscriptions");
  return (
    <section aria-label={t("cancel.region")} className="grid content-start gap-2 text-[13px]">
      <h4 className="flex items-center gap-2 text-sm font-semibold"><Scale className="size-4" aria-hidden /> {t("cancel.title", { country })}</h4>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">{t("cancel.now")}</dt>
        <dd>{c.can_cancel_now === true ? t("cancel.yes") : c.can_cancel_now === false ? t("cancel.notYet") : t("cancel.cannotTell")}</dd>
        <dt className="text-muted">{t("cancel.earliest")}</dt>
        <dd>{c.earliest_effective_date ? fmtDate(c.earliest_effective_date, "medium") : "–"}</dd>
        <dt className="text-muted">{t("cancel.notice")}</dt>
        <dd>{c.notice_period_days ? t("cancel.noticeDays", { n: c.notice_period_days }) : t("cancel.seeContract")}</dd>
        <dt className="text-muted">{t("cancel.method")}</dt>
        <dd>{c.method}</dd>
        {ec && (<><dt className="text-muted">{t("cancel.early")}</dt><dd>{ec.amount !== null ? <Trans t={t} i18nKey="cancel.earlyCost" values={{ months: ec.remaining_months, share: ec.share_pct ?? "?" }} components={{ amount: <Money v={ec.amount} /> }} /> : t("cancel.costUnknown")}{t("cancel.freeFrom", { date: fmtDate(ec.free_exit_date, "medium") })}</dd></>)}
        {c.contract_notice_deadline && (<><dt className="text-muted">{t("cancel.noticeBy")}</dt><dd>{t("cancel.noticeByValue", { date: fmtDate(c.contract_notice_deadline.send_notice_by, "medium"), n: c.contract_notice_deadline.days_left })}</dd></>)}
        {c.anniversary_route && (<><dt className="text-muted">{t("cancel.anniversary")}</dt><dd>{t("cancel.anniversaryValue", { date: fmtDate(c.anniversary_route.send_notice_by, "medium"), end: fmtDate(c.anniversary_route.effective, "medium") })}</dd></>)}
        {c.first_request_date && (<><dt className="text-muted">{t("cancel.freeOpens")}</dt><dd>{fmtDate(c.first_request_date, "medium")}</dd></>)}
      </dl>
      {c.conditions.length > 0 && <ul className="list-disc pl-5 text-muted">{c.conditions.map((x) => <li key={x}>{x}</li>)}</ul>}
      {c.unknown.length > 0 && <p className="text-warn">{t("cancel.missing", { list: c.unknown.join(", ") })}</p>}
      <div>
        <p className="font-medium">{t("cancel.legalBasis")}</p>
        <ul className="mt-1 grid gap-1">
          {c.legal_basis.map((l) => <li key={l.id} className="text-muted"><span className="text-text">{l.name}</span>{t("cancel.legalLine", { law: l.law, source: l.source, reviewed: l.last_reviewed })}</li>)}
        </ul>
      </div>
      <p className="text-xs text-faint">{c.verify} {c.disclaimer}</p>
    </section>
  );
}

/* ------------------------------------------------------------------ E8-2: usage */
function UsagePanel({ row: r }: { row: InvRow }) {
  const [freq, setFreq] = useState<UsageFrequency>(r.usage.frequency);
  const [last, setLast] = useState(r.usage.last_used ?? "");
  const [note, setNote] = useState("");
  const { t } = useTranslation("subscriptions");
  const save = useWrite(() => api.put(`/subs/contracts/${r.contract_id}/usage`, { frequency: freq, last_used: last || null, note: note || null }), { success: t("usage.saved") });
  return (
    <section aria-label={t("usage.region")} className="grid gap-2 text-[13px]">
      <h4 className="text-sm font-semibold">{t("usage.title")}</h4>
      {!r.contract_id ? <p className="text-muted">{t("usage.createFirst")}</p> : (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label={t("usage.howOften")}>{(id) => <Select id={id} value={freq} onChange={(e) => setFreq(e.target.value as UsageFrequency)}>{FREQ.map((v) => <option key={v} value={v}>{t(`inv.freq.${v}`)}</option>)}</Select>}</Field>
          <Field label={t("usage.lastUsed")}>{(id) => <Input id={id} type="date" value={last} onChange={(e) => setLast(e.target.value)} />}</Field>
          <Field label={t("usage.note")} className="sm:col-span-2">{(id) => <Input id={id} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />}</Field>
          <div className="sm:col-span-2"><Button size="sm" busy={save.isPending} onClick={() => save.mutate(undefined)}>{t("usage.save")}</Button></div>
        </div>
      )}
      <p className="text-xs text-faint">{t("usage.rule")} {r.usage.note_not_measurable ?? ""}</p>
    </section>
  );
}

/* ------------------------------------------------------------------ E8-6: decisions */
function DecisionPanel({ row: r }: { row: InvRow }) {
  const [decision, setDecision] = useState("cancelled");
  const [before, setBefore] = useState(r.monthly ?? "");
  const [after, setAfter] = useState("");
  const [date, setDate] = useState("");
  const [eff, setEff] = useState("");
  const body = { ref: r.ref, decision, before_monthly: before === "" ? null : Number(before), after_monthly: decision === "cancelled" ? 0 : decision === "kept" ? Number(before) : after === "" ? null : Number(after), decided_on: date || null, effective_on: eff || null };
  const check = useDryRun<{ valid: boolean; monthly_saving: number }>("/subs/decisions", body, true);
  const { t } = useTranslation("subscriptions");
  const save = useWrite(() => api.post("/subs/decisions", body), { success: t("decision.recorded") });
  const d = r.decision;
  return (
    <section aria-label={t("decision.region")} className="grid gap-2 text-[13px]">
      <h4 className="flex items-center gap-2 text-sm font-semibold"><ThumbsUp className="size-4" aria-hidden /> {t("decision.title")}</h4>
      {d && <p><Trans t={t} i18nKey="decision.last" count={d.months_counted} values={{ decision: isDecision(d.decision) ? t(`inv.decisionShort.${d.decision}`) : d.decision, date: fmtDate(d.decided_on, "medium"), before: fmtMoney(d.before_monthly), after: fmtMoney(d.after_monthly), reason: d.reason, since: fmtMoney(d.since_decision) }} components={{ b: <strong />, status: <Badge tone={STATUS_TONE[d.status]}>{t(STATUS_LABEL[d.status])}</Badge> }} /></p>}
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label={t("decision.decision")}>{(id) => <Select id={id} value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map((v) => <option key={v} value={v}>{t(`inv.decision.${v}`)}</option>)}</Select>}</Field>
        <Field label={t("decision.before")}>{(id) => <Input id={id} inputMode="decimal" value={before} onChange={(e) => setBefore(e.target.value)} />}</Field>
        {decision !== "cancelled" && decision !== "kept" && <Field label={t("decision.after")}>{(id) => <Input id={id} inputMode="decimal" value={after} onChange={(e) => setAfter(e.target.value)} />}</Field>}
        <Field label={t("decision.decidedOn")}>{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
        <Field label={t("decision.effective")}>{(id) => <Input id={id} type="date" value={eff} onChange={(e) => setEff(e.target.value)} />}</Field>
      </div>
      {check.error && <p role="alert" className="text-neg">{check.error}</p>}
      {check.data?.valid && decision !== "kept" && <p className="text-muted"><Trans t={t} i18nKey="decision.saves" components={{ amount: <Money v={check.data.monthly_saving} /> }} /></p>}
      <div><Button size="sm" disabled={!check.data?.valid} busy={save.isPending} onClick={() => save.mutate(undefined)}>{t("decision.record")}</Button></div>
    </section>
  );
}

/* ------------------------------------------------------------------ E8-4: alternatives */
function AlternativesPanel({ row: r }: { row: InvRow }) {
  const [adding, setAdding] = useState(false);
  const { t } = useTranslation("subscriptions");
  const remove = useWrite((id: string) => api.delete(`/subs/alternatives/${id}`), { success: t("alt.removed") });
  const a = r.alternatives;
  return (
    <section aria-label={t("alt.region")} className="grid gap-2 text-[13px]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{t("alt.title")}</h4>
        <Button size="sm" onClick={() => setAdding(!adding)}>{adding ? t("alt.cancel") : t("alt.add")}</Button>
      </div>
      {a.items.length === 0 ? <p className="text-muted">{t("alt.none")}</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead className="text-xs text-muted"><tr><th className="py-1 pr-3">{t("alt.offer")}</th><th className="pr-3">{t("alt.price")}</th><th className="pr-3">{t("alt.saves")}</th><th className="pr-3">{t("alt.seen")}</th><th className="pr-3">{t("alt.source")}</th><th /></tr></thead>
            <tbody>
              {a.items.map((i) => <AltRow key={i.id} i={i} best={a.best?.id === i.id} onRemove={() => remove.mutate(i.id)} />)}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-faint">{a.note}. {t("alt.computed")}</p>
      {adding && <AlternativeForm row={r} onDone={() => setAdding(false)} />}
    </section>
  );
}

function AltRow({ i, best, onRemove }: { i: InvAlternative; best: boolean; onRemove: () => void }) {
  const { t } = useTranslation("subscriptions");
  return (
    <tr className="border-t border-border align-top">
      <td className="py-1.5 pr-3"><span className="font-medium">{i.provider}</span> · {i.offer}{best && <Badge tone="pos" className="ml-2">{t("alt.best")}</Badge>}{i.features && <div className="text-xs text-muted">{i.features}</div>}</td>
      <td className="pr-3 num"><Money v={i.monthly_price} /></td>
      <td className="pr-3 num">{i.savings ? <><Money v={i.savings.net_12m} />{i.savings.break_even_months ? <span className="text-xs text-muted">{t("alt.paysBack", { n: i.savings.break_even_months })}</span> : null}</> : "–"}</td>
      <td className="pr-3">{fmtDate(i.retrieved_at, "medium")} {i.stale ? <Badge tone="warn" title={i.savings?.stale_warning ?? undefined}>{t("alt.outdated")}</Badge> : <span className="text-xs text-muted">{t("alt.age", { n: i.age_days })}</span>}</td>
      <td className="pr-3">{i.source_url ? <a className="text-accent underline" href={i.source_url} target="_blank" rel="noopener noreferrer">{new URL(i.source_url).hostname}</a> : <span className="text-muted">{t("alt.noLink")}</span>}<div className="text-xs text-faint">{i.method} · {i.source}</div></td>
      <td><button className="text-xs text-muted underline hover:text-text" onClick={onRemove} aria-label={t("alt.removeLabel", { provider: i.provider, offer: i.offer })}>{t("alt.remove")}</button></td>
    </tr>
  );
}

function AlternativeForm({ row: r, onDone }: { row: InvRow; onDone: () => void }) {
  const [f, setF] = useState({ provider: "", offer_name: "", monthly_price: "", features: "", source_url: "", retrieved_at: "", switching_costs: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const { t } = useTranslation("subscriptions");
  const save = useWrite(() => api.post("/subs/alternatives", { ref: r.ref, provider: f.provider, offer_name: f.offer_name, monthly_price: Number(f.monthly_price), features: f.features || null,
    source_url: f.source_url || null, retrieved_at: f.retrieved_at || null, switching_costs: f.switching_costs === "" ? 0 : Number(f.switching_costs), method: "manual" }), { success: t("alt.stored"), onSuccess: onDone });
  const bad = f.source_url !== "" && !f.source_url.startsWith("https://");
  return (
    <form className="grid gap-2 rounded-lg border border-border p-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
      <Field label={t("alt.provider")}>{(id) => <Input id={id} required value={f.provider} onChange={set("provider")} />}</Field>
      <Field label={t("alt.offerName")}>{(id) => <Input id={id} required value={f.offer_name} onChange={set("offer_name")} />}</Field>
      <Field label={t("alt.monthlyPrice")}>{(id) => <Input id={id} required inputMode="decimal" value={f.monthly_price} onChange={set("monthly_price")} />}</Field>
      <Field label={t("alt.switching")}>{(id) => <Input id={id} inputMode="decimal" value={f.switching_costs} onChange={set("switching_costs")} />}</Field>
      <Field label={t("alt.url")} error={bad ? t("alt.urlError") : undefined}>{(id) => <Input id={id} value={f.source_url} onChange={set("source_url")} />}</Field>
      <Field label={t("alt.date")}>{(id) => <Input id={id} type="date" value={f.retrieved_at} onChange={set("retrieved_at")} />}</Field>
      <Field label={t("alt.features")} className="sm:col-span-2">{(id) => <Input id={id} value={f.features} maxLength={400} onChange={set("features")} />}</Field>
      <div className="sm:col-span-2"><Button type="submit" size="sm" variant="primary" disabled={bad || !f.provider || !f.offer_name || !f.monthly_price} busy={save.isPending}>{t("alt.store")}</Button></div>
    </form>
  );
}

/* ------------------------------------------------------------------ E8-1: create a contract from a subscription */
function DraftDialog({ row: r, onClose }: { row: InvRow; onClose: () => void }) {
  const pv = useDryRun<DraftPreview>("/subs/contracts/draft", { series: r.series_id }, true);
  const { t } = useTranslation("subscriptions");
  const create = useWrite(() => api.post<DraftPreview>("/subs/contracts/draft", { series: r.series_id }), { success: t("draft.created"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("draft.title")} size="lg"
      description={t("draft.description")}
      footer={<><Button variant="ghost" onClick={onClose}>{t("draft.cancel")}</Button><Button variant="primary" disabled={!pv.data} busy={create.isPending} onClick={() => create.mutate(undefined)}>{t("draft.create")}</Button></>}>
      {pv.error && <Notice tone="neg">{pv.error}</Notice>}
      {pv.loading && !pv.data && <Skeleton className="h-32 w-full" />}
      {pv.data && (
        <div className="grid gap-3 text-[13px]">
          <p><Trans t={t} i18nKey="draft.summary" values={{ kind: pv.data.contract.kind, provider: pv.data.contract.provider, missing: pv.data.contract.missing.join(", ") }} components={{ b: <strong /> }} /></p>
          {pv.data.contract.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          <DiffView diff={pv.data.diff} />
          <p className="text-xs text-faint">{t("draft.startNote")}</p>
        </div>
      )}
    </Dialog>
  );
}

/* ------------------------------------------------------------------ E8-5: the cancellation letter (local; nothing is sent) */
function LetterDialog({ row: r, country, onClose }: { row: InvRow; country: string; onClose: () => void }) {
  const [lang, setLang] = useState<"fr" | "it" | "en">(country === "IT" ? "it" : "fr");
  const [channel, setChannel] = useState<"lrar" | "email" | "online">("lrar");
  const q = useGet<Letter>("/subs/letter", { contract: r.contract_id, lang, channel }, { staleTime: 0 });
  const contact = useGet<ContactInfo>("/subs/contact");
  const [copied, setCopied] = useState(false);
  const { t } = useTranslation("subscriptions");
  const text = q.data?.text ?? "";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = q.data?.filename ?? "cancellation-letter.txt";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Dialog open onClose={onClose} title={t("letter.title", { name: r.name })} size="lg"
      description={t("letter.description")}
      footer={<><Button variant="ghost" onClick={onClose}>{t("letter.close")}</Button><Button onClick={copy} disabled={!text}>{copied ? t("letter.copied") : t("letter.copy")}</Button><Button variant="primary" onClick={download} disabled={!text}><FileText className="size-4" aria-hidden /> {t("letter.download")}</Button></>}>
      <div className="grid gap-3 text-[13px]">
        <div className="grid grid-cols-2 gap-3">
          <Field label={t("letter.language")}>{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value as typeof lang)}><option value="fr">Français</option><option value="it">Italiano</option><option value="en">English</option></Select>}</Field>
          <Field label={t("letter.channel")}>{(id) => <Select id={id} value={channel} onChange={(e) => setChannel(e.target.value as typeof channel)}><option value="lrar">{t("letter.lrar")}</option><option value="email">{t("letter.email")}</option><option value="online">{t("letter.online")}</option></Select>}</Field>
        </div>
        <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
          {(d) => (
            <>
              <Textarea aria-label={t("letter.text")} readOnly rows={16} className="font-mono text-xs" value={d.text} />
              {d.placeholders.length > 0 && <Notice tone="warn" title={t("letter.toComplete")}>{d.placeholders.join(" · ")}</Notice>}
              <ul className="grid gap-1 text-muted">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              <p className="text-xs text-faint">{t("letter.legal", { list: d.legal_basis.map((l) => `${l.name} (${l.source})`).join("; ") || t("letter.noneStated"), pdf: d.pdf_note })}</p>
            </>
          )}
        </Async>
        {contact.data && !contact.data.set && <ContactForm />}
      </div>
    </Dialog>
  );
}

function ContactForm() {
  const [address, setAddress] = useState("");
  const [email, setEmail] = useState("");
  const { t } = useTranslation("subscriptions");
  const save = useWrite(() => api.put("/subs/contact", { address: address || null, email: email || null }), { success: t("contact.saved") });
  const ok = useMemo(() => address.trim() !== "" || email.trim() !== "", [address, email]);
  return (
    <div className="grid gap-2 rounded-lg border border-border p-3">
      <p className="font-medium">{t("contact.title")}</p>
      <p className="text-xs text-muted">{t("contact.note")}</p>
      <Field label={t("contact.address")}>{(id) => <Textarea id={id} rows={3} value={address} onChange={(e) => setAddress(e.target.value)} />}</Field>
      <Field label={t("contact.email")}>{(id) => <Input id={id} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      <div><Button size="sm" disabled={!ok} busy={save.isPending} onClick={() => save.mutate(undefined)}>{t("contact.save")}</Button></div>
    </div>
  );
}
