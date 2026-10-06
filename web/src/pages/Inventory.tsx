import { useMemo, useState } from "react";
import { BellRing, ChevronDown, FilePlus2, FileText, FileWarning, Mail, PiggyBank, Scale, ThumbsUp } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, Select, Skeleton, Stat, Textarea } from "@/components/ui";
import { useDryRun, useGet, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/format";
import type { ContactInfo, DraftPreview, InvAlternative, InvRow, Inventory as Inv, Letter, UsageFrequency } from "@/api/types";

const FREQ: [UsageFrequency, string][] = [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["rarely", "Rarely"], ["never", "Never"], ["unknown", "I don't know"]];
const DECISIONS: [string, string][] = [["cancelled", "Cancelled"], ["renegotiated", "Renegotiated"], ["switched", "Switched to another provider"], ["downgraded", "Downgraded"], ["kept", "Kept"]];
const STATUS_TONE = { verified: "pos", pending: "warn", contradicted: "neg", not_applicable: "neutral", ambiguous: "warn" } as const;
const STATUS_LABEL = { verified: "confirmed by the bank data", pending: "not confirmed yet", contradicted: "the bank data disagrees", not_applicable: "nothing to verify", ambiguous: "which series? name one" } as const;

function byGroup(rows: InvRow[]): Record<string, InvRow[]> {
  const out: Record<string, InvRow[]> = {};
  for (const r of rows) (out[r.group] ??= []).push(r);
  return out;
}

export default function InventoryView() {
  const [ended, setEnded] = useState(false);
  const q = useScoped<Inv>("/subs/inventory", { include_ended: ended });
  const proposed = useGet<{ proposed: { id: string; decision: string; name: string | null; before_monthly: number; after_monthly: number }[] }>("/subs/savings");
  return (
    <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
      {(d) => (
        <div className="grid gap-4">
          <Card>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
              <Stat label="Per month" value={fmtMoney(d.totals.monthly)} hint={`${d.totals.services} services`} />
              <Stat label="Per year" value={fmtMoney(d.totals.yearly, { round: true })} />
              <Stat label="Without a contract file" value={String(d.totals.without_contract)} tone={d.totals.without_contract ? "warn" : undefined} hint={d.totals.expired_contracts ? `${d.totals.expired_contracts} with dates in the past` : undefined} />
              <Stat label="Savings achieved" value={fmtMoney(d.savings.realised_monthly)} hint={`per month, confirmed (${d.savings.verified}); ${fmtMoney(d.savings.realised_since_decisions)} so far`} tone={Number(d.savings.realised_monthly) > 0 ? "pos" : undefined} />
              <Stat label="Reminders" value={String(d.totals.reminders)} tone={d.totals.reminders ? "warn" : undefined} hint={d.totals.outdated_alternatives ? `${d.totals.outdated_alternatives} outdated offer(s)` : undefined} />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-4 text-[13px] text-muted">
              <label className="flex items-center gap-2"><input type="checkbox" checked={ended} onChange={(e) => setEnded(e.target.checked)} /> Show stopped subscriptions</label>
              <span>Loans, rent and taxes are not subscriptions and are not listed.</span>
            </div>
          </Card>
          {(proposed.data?.proposed ?? []).length > 0 && <ProposedDecisions items={proposed.data!.proposed} />}
          {d.rows.length === 0 ? <Card><EmptyState title="No recurring cost found">They appear once the bank history shows a repeating payment.</EmptyState></Card> : (
            Object.entries(byGroup(d.rows)).map(([g, rows]) => (
              <section key={g} aria-label={d.groups_meta.find((m) => m.id === g)?.label ?? g} className="grid gap-3">
                <h2 className="mt-2 flex flex-wrap items-baseline gap-x-3 text-sm font-semibold text-muted">
                  {rows[0].group_label}
                  {d.groups[g] && <span className="text-xs font-normal text-faint">{fmtMoney(d.groups[g].monthly)} /month · {fmtMoney(d.groups[g].yearly, { round: true })} /year</span>}
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
  const act = useWrite((v: { id: string; what: "confirm" | "reject" }) => api.post(`/subs/decisions/${v.id}/${v.what}`, {}), { success: "Updated" });
  return (
    <Notice tone="info" title="The coach proposed to record these decisions">
      <ul className="mt-1 grid gap-2">
        {items.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-2 text-[13px]">
            <span>{p.name ?? "A subscription"}: {p.decision}, {fmtMoney(p.before_monthly)} → {fmtMoney(p.after_monthly)} a month.</span>
            <Button size="sm" onClick={() => act.mutate({ id: p.id, what: "confirm" })}>Record it</Button>
            <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: p.id, what: "reject" })}>Dismiss</Button>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs">Nothing counts until you record it; the bank data then checks it.</p>
    </Notice>
  );
}

function cancelBadge(r: InvRow) {
  const c = r.cancellation;
  if (c.can_cancel_now === true) return <Badge tone="info" title={c.method}>Can cancel now{c.earliest_effective_date ? ` · effective from ${fmtDate(c.earliest_effective_date, "medium")}` : ""}</Badge>;
  if (c.can_cancel_now === false) return <Badge tone="warn" title={c.method}>Not yet{c.earliest_effective_date ? ` · earliest ${fmtDate(c.earliest_effective_date, "medium")}` : ""}</Badge>;
  return <Badge title={`Missing: ${c.unknown.join(", ") || "dates"}`}>Cancellation: more facts needed</Badge>;
}

function SubRow({ row: r, country }: { row: InvRow; country: string }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(false);
  const [letter, setLetter] = useState(false);
  const c = r.contract;
  return (
    <Card>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-start">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold">{r.name}</h3>
            {r.status === "ended" && <Badge tone="warn">stopped {fmtDate(r.last_payment, "dayMonth")}</Badge>}
            {r.status === "contract_only" && <Badge title="Not seen in the bank data: the cost is the billing amount of the contract file">contract file only</Badge>}
            {c.status === "on_file" && <Badge tone="pos">Contract on file</Badge>}
            {c.status === "expired" && <Badge tone="warn" title={`Every date on file is in the past (${c.expired_on}): it may have been renewed or ended. Refresh it.`}>Contract dates expired</Badge>}
            {c.status === "missing" && <Badge tone="warn"><FileWarning className="size-3" aria-hidden /> No contract file</Badge>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
            {r.next_charge && <span>next charge {fmtDate(r.next_charge, "dayMonth")}</span>}
            {r.first_seen && <span>paid since {fmtDate(r.first_seen, "medium")}</span>}
            {r.price_changes.map((p) => <span key={p.id}>{p.direction === "increase" ? "price up" : "price down"} {fmtMoney(p.old)} → {fmtMoney(p.new)} on {fmtDate(p.date, "dayMonth")}</span>)}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {cancelBadge(r)}
            <Badge tone={r.usage.recorded ? "neutral" : "warn"} title={r.usage.note_not_measurable ?? undefined}>
              {r.usage.recorded ? `Usage: ${r.usage.frequency}${r.usage.last_used ? `, last ${fmtDate(r.usage.last_used, "medium")}` : ""}` : "Usage unknown"}
            </Badge>
            {r.usage.signals.map((s) => (
              <Badge key={s.kind} tone="warn" title={s.measurable}><BellRing className="size-3" aria-hidden /> {s.kind === "unused_60_days" ? `Unused for ${s.days} days (your record)` : "Marked never used, still paid"}</Badge>
            ))}
            {r.alternatives.best && <Badge tone="pos"><PiggyBank className="size-3" aria-hidden /> Cheaper offer: saves {fmtMoney(r.alternatives.best.savings?.yearly, { round: true })} /year</Badge>}
            {r.alternatives.outdated > 0 && <Badge tone="warn">{r.alternatives.outdated} outdated offer(s), re-check</Badge>}
            {r.decision && <Badge tone={STATUS_TONE[r.decision.status]} title={r.decision.reason}>{r.decision.decision}: {STATUS_LABEL[r.decision.status]}</Badge>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="text-right">
            <div className="num text-lg font-semibold">{r.monthly ? <><Money v={r.monthly} /> <span className="text-xs font-normal text-muted">/month</span></> : "–"}</div>
            <div className="num text-xs text-muted">{r.yearly ? <><Money v={r.yearly} round /> /year</> : "cost unknown"}</div>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {r.draftable && <Button size="sm" onClick={() => setDraft(true)}><FilePlus2 className="size-4" aria-hidden /> Create contract from this subscription</Button>}
            {r.contract_id && <Button size="sm" onClick={() => setLetter(true)}><Mail className="size-4" aria-hidden /> Prepare cancellation letter</Button>}
            <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>Details <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden /></Button>
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
  return (
    <section aria-label="Cancellation" className="grid content-start gap-2 text-[13px]">
      <h4 className="flex items-center gap-2 text-sm font-semibold"><Scale className="size-4" aria-hidden /> Can I cancel? ({country})</h4>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-muted">Now</dt>
        <dd>{c.can_cancel_now === true ? "Yes" : c.can_cancel_now === false ? "Not yet" : "Cannot tell from the dates on file"}</dd>
        <dt className="text-muted">Earliest effective date</dt>
        <dd>{c.earliest_effective_date ? fmtDate(c.earliest_effective_date, "medium") : "–"}</dd>
        <dt className="text-muted">Notice</dt>
        <dd>{c.notice_period_days ? `${c.notice_period_days} days` : "see the contract"}</dd>
        <dt className="text-muted">Method</dt>
        <dd>{c.method}</dd>
        {ec && (<><dt className="text-muted">Early termination</dt><dd>{ec.amount !== null ? <>about <Money v={ec.amount} /> ({ec.remaining_months} months left, {ec.share_pct ?? "?"} % still due)</> : "cost unknown"}; free from {fmtDate(ec.free_exit_date, "medium")}</dd></>)}
        {c.contract_notice_deadline && (<><dt className="text-muted">Give notice by</dt><dd>{fmtDate(c.contract_notice_deadline.send_notice_by, "medium")} ({c.contract_notice_deadline.days_left} days left)</dd></>)}
        {c.anniversary_route && (<><dt className="text-muted">Anniversary route</dt><dd>send notice by {fmtDate(c.anniversary_route.send_notice_by, "medium")} for the end on {fmtDate(c.anniversary_route.effective, "medium")}</dd></>)}
        {c.first_request_date && (<><dt className="text-muted">Free cancellation opens</dt><dd>{fmtDate(c.first_request_date, "medium")}</dd></>)}
      </dl>
      {c.conditions.length > 0 && <ul className="list-disc pl-5 text-muted">{c.conditions.map((x) => <li key={x}>{x}</li>)}</ul>}
      {c.unknown.length > 0 && <p className="text-warn">Missing to decide: {c.unknown.join(", ")}.</p>}
      <div>
        <p className="font-medium">Legal basis</p>
        <ul className="mt-1 grid gap-1">
          {c.legal_basis.map((l) => <li key={l.id} className="text-muted"><span className="text-text">{l.name}</span>: {l.law}. Source: {l.source}. Reviewed {l.last_reviewed}.</li>)}
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
  const save = useWrite(() => api.put(`/subs/contracts/${r.contract_id}/usage`, { frequency: freq, last_used: last || null, note: note || null }), { success: "Usage saved" });
  return (
    <section aria-label="Usage" className="grid gap-2 text-[13px]">
      <h4 className="text-sm font-semibold">How do you use it?</h4>
      {!r.contract_id ? <p className="text-muted">Create a contract file first (button above): the usage is stored in it.</p> : (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="How often">{(id) => <Select id={id} value={freq} onChange={(e) => setFreq(e.target.value as UsageFrequency)}>{FREQ.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>}</Field>
          <Field label="Last used">{(id) => <Input id={id} type="date" value={last} onChange={(e) => setLast(e.target.value)} />}</Field>
          <Field label="Note (optional)" className="sm:col-span-2">{(id) => <Input id={id} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />}</Field>
          <div className="sm:col-span-2"><Button size="sm" busy={save.isPending} onClick={() => save.mutate(undefined)}>Save usage</Button></div>
        </div>
      )}
      <p className="text-xs text-faint">Only what you record counts: a reminder appears when your own last-used date is more than 60 days ago, or when you record "never" and the payments continue. {r.usage.note_not_measurable ?? ""}</p>
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
  const save = useWrite(() => api.post("/subs/decisions", body), { success: "Decision recorded" });
  const d = r.decision;
  return (
    <section aria-label="Decision" className="grid gap-2 text-[13px]">
      <h4 className="flex items-center gap-2 text-sm font-semibold"><ThumbsUp className="size-4" aria-hidden /> What did you decide?</h4>
      {d && <p>Last decision: <strong>{d.decision}</strong> on {fmtDate(d.decided_on, "medium")} ({fmtMoney(d.before_monthly)} → {fmtMoney(d.after_monthly)} a month): <Badge tone={STATUS_TONE[d.status]}>{STATUS_LABEL[d.status]}</Badge> {d.reason}. Counted so far: {fmtMoney(d.since_decision)} ({d.months_counted} month{d.months_counted === 1 ? "" : "s"}).</p>}
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Decision">{(id) => <Select id={id} value={decision} onChange={(e) => setDecision(e.target.value)}>{DECISIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>}</Field>
        <Field label="Monthly cost before (EUR)">{(id) => <Input id={id} inputMode="decimal" value={before} onChange={(e) => setBefore(e.target.value)} />}</Field>
        {decision !== "cancelled" && decision !== "kept" && <Field label="Monthly cost after (EUR)">{(id) => <Input id={id} inputMode="decimal" value={after} onChange={(e) => setAfter(e.target.value)} />}</Field>}
        <Field label="Decided on (default today)">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
        <Field label="Takes effect on (optional)">{(id) => <Input id={id} type="date" value={eff} onChange={(e) => setEff(e.target.value)} />}</Field>
      </div>
      {check.error && <p role="alert" className="text-neg">{check.error}</p>}
      {check.data?.valid && decision !== "kept" && <p className="text-muted">Saves <Money v={check.data.monthly_saving} /> a month once the bank data confirms it.</p>}
      <div><Button size="sm" disabled={!check.data?.valid} busy={save.isPending} onClick={() => save.mutate(undefined)}>Record decision</Button></div>
    </section>
  );
}

/* ------------------------------------------------------------------ E8-4: alternatives */
function AlternativesPanel({ row: r }: { row: InvRow }) {
  const [adding, setAdding] = useState(false);
  const remove = useWrite((id: string) => api.delete(`/subs/alternatives/${id}`), { success: "Offer removed" });
  const a = r.alternatives;
  return (
    <section aria-label="Alternatives" className="grid gap-2 text-[13px]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">Cheaper alternatives</h4>
        <Button size="sm" onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add an offer"}</Button>
      </div>
      {a.items.length === 0 ? <p className="text-muted">No offer stored. Ask the coach to look for cheaper offers (the find-cheaper skill stores what it finds here), or add one you found.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead className="text-xs text-muted"><tr><th className="py-1 pr-3">Offer</th><th className="pr-3">Price</th><th className="pr-3">Saves (12 months, net)</th><th className="pr-3">Seen</th><th className="pr-3">Source</th><th /></tr></thead>
            <tbody>
              {a.items.map((i) => <AltRow key={i.id} i={i} best={a.best?.id === i.id} onRemove={() => remove.mutate(i.id)} />)}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-faint">{a.note}. Savings are computed by the app, not by a model.</p>
      {adding && <AlternativeForm row={r} onDone={() => setAdding(false)} />}
    </section>
  );
}

function AltRow({ i, best, onRemove }: { i: InvAlternative; best: boolean; onRemove: () => void }) {
  return (
    <tr className="border-t border-border align-top">
      <td className="py-1.5 pr-3"><span className="font-medium">{i.provider}</span> · {i.offer}{best && <Badge tone="pos" className="ml-2">current best</Badge>}{i.features && <div className="text-xs text-muted">{i.features}</div>}</td>
      <td className="pr-3 num"><Money v={i.monthly_price} /></td>
      <td className="pr-3 num">{i.savings ? <><Money v={i.savings.net_12m} />{i.savings.break_even_months ? <span className="text-xs text-muted"> · pays back in {i.savings.break_even_months} mo</span> : null}</> : "–"}</td>
      <td className="pr-3">{fmtDate(i.retrieved_at, "medium")} {i.stale ? <Badge tone="warn" title={i.savings?.stale_warning ?? undefined}>outdated, re-check</Badge> : <span className="text-xs text-muted">({i.age_days} d)</span>}</td>
      <td className="pr-3">{i.source_url ? <a className="text-accent underline" href={i.source_url} target="_blank" rel="noopener noreferrer">{new URL(i.source_url).hostname}</a> : <span className="text-muted">no link</span>}<div className="text-xs text-faint">{i.method} · {i.source}</div></td>
      <td><button className="text-xs text-muted underline hover:text-text" onClick={onRemove} aria-label={`Remove ${i.provider} ${i.offer}`}>remove</button></td>
    </tr>
  );
}

function AlternativeForm({ row: r, onDone }: { row: InvRow; onDone: () => void }) {
  const [f, setF] = useState({ provider: "", offer_name: "", monthly_price: "", features: "", source_url: "", retrieved_at: "", switching_costs: "" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const save = useWrite(() => api.post("/subs/alternatives", { ref: r.ref, provider: f.provider, offer_name: f.offer_name, monthly_price: Number(f.monthly_price), features: f.features || null,
    source_url: f.source_url || null, retrieved_at: f.retrieved_at || null, switching_costs: f.switching_costs === "" ? 0 : Number(f.switching_costs), method: "manual" }), { success: "Offer stored", onSuccess: onDone });
  const bad = f.source_url !== "" && !f.source_url.startsWith("https://");
  return (
    <form className="grid gap-2 rounded-lg border border-border p-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
      <Field label="Provider">{(id) => <Input id={id} required value={f.provider} onChange={set("provider")} />}</Field>
      <Field label="Offer name">{(id) => <Input id={id} required value={f.offer_name} onChange={set("offer_name")} />}</Field>
      <Field label="Monthly price (EUR)">{(id) => <Input id={id} required inputMode="decimal" value={f.monthly_price} onChange={set("monthly_price")} />}</Field>
      <Field label="Switching costs (EUR)">{(id) => <Input id={id} inputMode="decimal" value={f.switching_costs} onChange={set("switching_costs")} />}</Field>
      <Field label="Source URL (https)" error={bad ? "The link must start with https://" : undefined}>{(id) => <Input id={id} value={f.source_url} onChange={set("source_url")} />}</Field>
      <Field label="Date the price was seen (default today)">{(id) => <Input id={id} type="date" value={f.retrieved_at} onChange={set("retrieved_at")} />}</Field>
      <Field label="Features" className="sm:col-span-2">{(id) => <Input id={id} value={f.features} maxLength={400} onChange={set("features")} />}</Field>
      <div className="sm:col-span-2"><Button type="submit" size="sm" variant="primary" disabled={bad || !f.provider || !f.offer_name || !f.monthly_price} busy={save.isPending}>Store offer</Button></div>
    </form>
  );
}

/* ------------------------------------------------------------------ E8-1: create a contract from a subscription */
function DraftDialog({ row: r, onClose }: { row: InvRow; onClose: () => void }) {
  const pv = useDryRun<DraftPreview>("/subs/contracts/draft", { series: r.series_id }, true);
  const create = useWrite(() => api.post<DraftPreview>("/subs/contracts/draft", { series: r.series_id }), { success: "Contract file created", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Create a contract file from this subscription" size="lg"
      description="Filled from the bank payments only; the dates and terms stay empty and become open questions."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!pv.data} busy={create.isPending} onClick={() => create.mutate(undefined)}>Create contract file</Button></>}>
      {pv.error && <Notice tone="neg">{pv.error}</Notice>}
      {pv.loading && !pv.data && <Skeleton className="h-32 w-full" />}
      {pv.data && (
        <div className="grid gap-3 text-[13px]">
          <p>Kind <strong>{pv.data.contract.kind}</strong>, provider <strong>{pv.data.contract.provider}</strong>; still to fill: {pv.data.contract.missing.join(", ")}.</p>
          {pv.data.contract.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          <DiffView diff={pv.data.diff} />
          <p className="text-xs text-faint">The start date is the first payment seen: the contract may be older.</p>
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
    <Dialog open onClose={onClose} title={`Cancellation letter: ${r.name}`} size="lg"
      description="Written on this machine from templates and your contract file. Nothing is sent: you send it yourself."
      footer={<><Button variant="ghost" onClick={onClose}>Close</Button><Button onClick={copy} disabled={!text}>{copied ? "Copied" : "Copy text"}</Button><Button variant="primary" onClick={download} disabled={!text}><FileText className="size-4" aria-hidden /> Download .txt</Button></>}>
      <div className="grid gap-3 text-[13px]">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Language">{(id) => <Select id={id} value={lang} onChange={(e) => setLang(e.target.value as typeof lang)}><option value="fr">Français</option><option value="it">Italiano</option><option value="en">English</option></Select>}</Field>
          <Field label="How you will send it">{(id) => <Select id={id} value={channel} onChange={(e) => setChannel(e.target.value as typeof channel)}><option value="lrar">Registered letter (LRAR)</option><option value="email">E-mail</option><option value="online">Online form / chat</option></Select>}</Field>
        </div>
        <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
          {(d) => (
            <>
              <Textarea aria-label="Letter text" readOnly rows={16} className="font-mono text-xs" value={d.text} />
              {d.placeholders.length > 0 && <Notice tone="warn" title="Still to complete before sending">{d.placeholders.join(" · ")}</Notice>}
              <ul className="grid gap-1 text-muted">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              <p className="text-xs text-faint">Legal basis: {d.legal_basis.map((l) => `${l.name} (${l.source})`).join("; ") || "none stated"}. {d.pdf_note}.</p>
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
  const save = useWrite(() => api.put("/subs/contact", { address: address || null, email: email || null }), { success: "Contact saved on this machine" });
  const ok = useMemo(() => address.trim() !== "" || email.trim() !== "", [address, email]);
  return (
    <div className="grid gap-2 rounded-lg border border-border p-3">
      <p className="font-medium">Add your address and e-mail to the letter</p>
      <p className="text-xs text-muted">Stored in your household file on this machine only. They are never sent to a model and the coach cannot read them.</p>
      <Field label="Postal address">{(id) => <Textarea id={id} rows={3} value={address} onChange={(e) => setAddress(e.target.value)} />}</Field>
      <Field label="E-mail">{(id) => <Input id={id} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
      <div><Button size="sm" disabled={!ok} busy={save.isPending} onClick={() => save.mutate(undefined)}>Save contact</Button></div>
    </div>
  );
}
