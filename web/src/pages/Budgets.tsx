import { useState } from "react";
import { Link } from "react-router";
import { Pencil, Plus, Target, Trash2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Select, Skeleton, Spinner } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { useBudgets, useDryRun, useFilters, useGet, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney } from "@/lib/format";
import type { BudgetProgress, BudgetSuggestion, EditResult, GoalProgress } from "@/api/types";

interface Draft { target: string; monthly: string; rollover: boolean; owner: string; account: string; note: string; id?: string }

export default function Budgets() {
  const q = useBudgets();
  const sug = useScoped<{ suggestions: BudgetSuggestion[] }>("/budgets/suggestions", { limit: 12 });
  const goals = useGet<{ goals: GoalProgress[]; warnings: string[] }>("/goals");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [goalOpen, setGoalOpen] = useState(false);
  const del = useWrite((id: string) => api.post<EditResult>(`/budgets/${id}/delete`, {}, { dry_run: false }), { success: "Budget removed" });
  return (
    <>
      <PageHeader title="Budgets" subtitle="Monthly envelopes per category, with where you will end the month at the current pace." actions={<Button variant="primary" onClick={() => setDraft({ target: "", monthly: "", rollover: false, owner: "", account: "", note: "" })}><Plus className="size-4" aria-hidden /> New budget</Button>} />
      <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            {d.problems.length > 0 && <Notice tone="warn" title="Some budget entries are invalid and left out">{d.problems.join("; ")}. Fix them under Memory, then Check.</Notice>}
            <div className="flex flex-wrap gap-2 text-sm text-muted">
              <span>{fmtMonth(d.month, "long")}, as of {fmtDate(d.as_of, "dayMonth")}:</span>
              <Badge tone="neg">{d.counts.over ?? 0} over</Badge><Badge tone="warn">{d.counts.at_risk ?? 0} at risk</Badge><Badge tone="pos">{d.counts.ok ?? 0} on track</Badge>
            </div>
            {d.budgets.length === 0 ? (
              <Card><EmptyState icon={<Target className="size-6" />} title="No budget yet">Start from the suggestions below: they are the median of your last covered months.</EmptyState></Card>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {d.budgets.map((b) => <BudgetCard key={b.id} b={b} onEdit={() => setDraft({ id: b.id, target: b.category ?? b.group ?? "", monthly: String(parseMoney(b.monthly) ?? ""), rollover: b.rollover, owner: b.owner ?? "", account: b.account ?? "", note: b.note ?? "" })} onDelete={() => confirm(`Remove the budget "${b.target}"? It is recorded in the memory history.`) && del.mutate(b.id)} />)}
              </div>
            )}
            {d.unbudgeted.length > 0 && (
              <Card title="Biggest unbudgeted spending this month">
                <ul className="divide-y divide-border">
                  {d.unbudgeted.slice(0, 5).map((u) => (
                    <li key={u.category} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <Link to={`/categories/${u.category}`} className="hover:underline">{catLabel(u.category)}</Link>
                      <span className="flex items-center gap-3"><Money v={u.spent} /><Button size="sm" onClick={() => setDraft({ target: u.category, monthly: "", rollover: false, owner: "", account: "", note: "" })}>Set budget</Button></span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {parseMoney(d.unallocated_refunds) ? <Notice>Refunds this month without a category ({fmtMoney(d.unallocated_refunds)}) belong to no budget.</Notice> : null}
            <Async q={sug} skeleton={<Skeleton className="h-40 w-full" />}>
              {(s) => (
                <Card title="Suggested budgets" subtitle="Median of the last covered months, rounded. Accepting writes to your memory after a preview." pad={false}>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[480px] text-sm">
                      <thead className="text-left text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-medium">Category</th><th scope="col" className="px-2 py-2 text-right font-medium">Suggested</th><th scope="col" className="px-2 py-2 text-right font-medium">Median / mean</th><th scope="col" className="px-4 py-2" /></tr></thead>
                      <tbody>
                        {s.suggestions.map((x) => (
                          <tr key={x.category} className="border-t border-border">
                            <td className="px-4 py-2 font-medium">{catLabel(x.category)} <span className="text-xs font-normal text-faint">{x.n_months} mo.</span>{x.low_confidence && <Badge tone="warn" className="ml-2">low confidence</Badge>}</td>
                            <td className="num px-2 py-2 text-right font-semibold">{fmtMoney(x.suggested, { round: true })}</td>
                            <td className="num px-2 py-2 text-right text-muted">{fmtMoney(x.median, { round: true })} / {fmtMoney(x.mean, { round: true })}</td>
                            <td className="px-4 py-2 text-right">{x.existing ? <Badge>set: {fmtMoney(x.existing, { round: true })}</Badge> : <Button size="sm" onClick={() => setDraft({ target: x.category, monthly: String(parseMoney(x.suggested)), rollover: false, owner: "", account: "", note: "" })}>Accept…</Button>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}
            </Async>
            <Async q={goals} skeleton={<Skeleton className="h-32 w-full" />}>
              {(g) => (
                <Card title="Savings goals" action={<Button size="sm" onClick={() => setGoalOpen(true)}><Plus className="size-3.5" aria-hidden /> Goal</Button>}>
                  {g.goals.length === 0 ? <p className="text-[13px] text-muted">No goal yet. A goal follows an asset, an account or the money tagged as savings.</p> : (
                    <ul className="grid gap-4">
                      {g.goals.map((x) => (
                        <li key={x.id}>
                          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm"><span className="font-medium">{x.title ?? x.id}</span><span className="num text-muted"><Money v={x.current} round /> / <Money v={x.target} round /></span></div>
                          <ProgressBar label={`${x.title ?? x.id} progress`} value={x.percent ?? 0} max={1} tone="pos" />
                          <p className="mt-1 text-xs text-muted">{x.status.replace(/_/g, " ")}{x.projected_date ? ` · projected ${fmtDate(x.projected_date)}` : ""}{x.required_monthly ? ` · needs ${fmtMoney(x.required_monthly, { round: true })}/month` : ""}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              )}
            </Async>
          </div>
        )}
      </Async>
      {draft && <BudgetDialog draft={draft} onClose={() => setDraft(null)} />}
      {goalOpen && <GoalDialog onClose={() => setGoalOpen(false)} />}
    </>
  );
}

function BudgetCard({ b, onEdit, onDelete }: { b: BudgetProgress; onEdit: () => void; onDelete: () => void }) {
  const tone = b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "info";
  const spent = parseMoney(b.spent) ?? 0;
  const avail = parseMoney(b.available) ?? 0;
  const proj = parseMoney(b.projected) ?? 0;
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/categories/${b.category ?? b.group}`} className="block truncate font-semibold hover:underline">{b.category ? catLabel(b.category) : `All ${groupLabel(b.group ?? "")}`}</Link>
          <div className="mt-0.5 flex flex-wrap gap-1.5 text-xs text-muted">{b.rollover && <Badge>rollover{parseMoney(b.carry) ? `: ${fmtMoney(b.carry, { signed: true, round: true })} carried` : ""}</Badge>}{b.owner && <Badge>{b.owner}</Badge>}{b.account && <Badge>{b.account}</Badge>}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button size="sm" variant="ghost" onClick={onEdit} aria-label={`Edit budget ${b.target}`}><Pencil className="size-3.5" /></Button>
          <Button size="sm" variant="ghost" onClick={onDelete} aria-label={`Remove budget ${b.target}`}><Trash2 className="size-3.5" /></Button>
        </div>
      </div>
      <div className="mt-3 flex items-baseline justify-between gap-3">
        <div className="num text-xl font-semibold"><Money v={b.spent} round /> <span className="text-sm font-normal text-muted">of <Money v={b.available} round /></span></div>
        <Badge tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "pos"}>{b.status === "over" ? "Over budget" : b.status === "at_risk" ? "At risk" : "On track"}</Badge>
      </div>
      <div className="mt-2"><ProgressBar label={`${b.target} used`} value={spent} max={Math.max(avail, spent, proj)} marker={(avail / Math.max(avail, spent, proj)) * 100} tone={tone} /></div>
      <p className="mt-2 text-[13px] text-muted">
        Projected end of month <b className="num text-text"><Money v={b.projected} round /></b>{b.projected_percent !== null ? ` (${fmtPct(b.projected_percent)})` : ""}; <Money v={b.remaining} round colored /> left.
        {b.excluded && parseMoney(b.excluded) ? <> One-offs left out: <Money v={b.excluded} round />.</> : null}
      </p>
      {b.flags.length > 0 && <p className="mt-1 text-xs text-faint">{b.flags.join(", ")}</p>}
    </Card>
  );
}

function BudgetDialog({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const [v, setV] = useState<Draft>(draft);
  const filters = useFilters();
  const body = { target: v.target, monthly: v.monthly, rollover: v.rollover || undefined, owner: v.owner || undefined, account: v.account || undefined, note: v.note || undefined, id: v.id, replace: !!v.id };
  const valid = !!v.target && parseMoney(v.monthly) !== null && (parseMoney(v.monthly) ?? 0) > 0;
  const pv = useDryRun<EditResult>("/budgets", body, valid);
  const save = useWrite(() => api.post<EditResult>("/budgets", body, { dry_run: false }), { success: "Budget saved to memory", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={v.id ? "Edit budget" : "New budget"} description="Written to your household memory with a visible diff." footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid || !pv.data || pv.loading} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-4">
        <Field label="Category or group" hint="A whole group (such as Food) covers all its categories.">{(id) => <CategoryPicker id={id} allowEmpty emptyLabel="Choose…" includeGroups value={v.target} onChange={(t) => setV({ ...v, target: t })} />}</Field>
        <Field label="Monthly amount (EUR)">{(id) => <Input id={id} inputMode="decimal" value={v.monthly} onChange={(e) => setV({ ...v, monthly: e.target.value.replace(",", ".") })} />}</Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.rollover} onChange={(e) => setV({ ...v, rollover: e.target.checked })} /> Roll over what is left (or overspent) to the next month</label>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Only for">{(id) => <Select id={id} value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })}><option value="">Everyone</option>{filters.data?.owners.map((o) => <option key={o} value={o}>{o}</option>)}</Select>}</Field>
          <Field label="Only on account">{(id) => <Select id={id} value={v.account} onChange={(e) => setV({ ...v, account: e.target.value })}><option value="">All accounts</option>{filters.data?.accounts.map((a) => <option key={a.uid} value={a.uid}>{a.label}</option>)}</Select>}</Field>
        </div>
        <Field label="Note">{(id) => <Input id={id} value={v.note} onChange={(e) => setV({ ...v, note: e.target.value })} />}</Field>
        {pv.loading && <Spinner label="Previewing" />}
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} empty="Already like that." />}
      </div>
    </Dialog>
  );
}

function GoalDialog({ onClose }: { onClose: () => void }) {
  const [v, setV] = useState({ id: "", title: "", target: "", source: "tag", ref: "savings", date: "", monthly: "" });
  const body = { id: v.id, title: v.title || undefined, target: v.target, [v.source]: v.ref, date: v.date || undefined, monthly: v.monthly || undefined };
  const valid = /^[a-z0-9][a-z0-9_-]*$/.test(v.id) && !!v.target && !!v.ref;
  const pv = useDryRun<EditResult>("/goals", body, valid);
  const save = useWrite(() => api.post<EditResult>("/goals", body, { dry_run: false }), { success: "Goal saved to memory", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="New savings goal" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid || !pv.data} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-4">
        <Field label="Id" hint="Lowercase letters, digits, - or _">{(id) => <Input id={id} value={v.id} onChange={(e) => setV({ ...v, id: e.target.value.toLowerCase() })} placeholder="holiday-fund" />}</Field>
        <Field label="Title">{(id) => <Input id={id} value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} />}</Field>
        <Field label="Target (EUR)">{(id) => <Input id={id} inputMode="decimal" value={v.target} onChange={(e) => setV({ ...v, target: e.target.value.replace(",", ".") })} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Progress follows">{(id) => <Select id={id} value={v.source} onChange={(e) => setV({ ...v, source: e.target.value })}><option value="tag">Money tagged…</option><option value="asset">An asset</option><option value="account">An account</option></Select>}</Field>
          <Field label={v.source === "tag" ? "Tag" : v.source === "asset" ? "Asset id" : "Account"}>{(id) => <Input id={id} value={v.ref} onChange={(e) => setV({ ...v, ref: e.target.value })} />}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Target date">{(id) => <Input id={id} type="date" value={v.date} onChange={(e) => setV({ ...v, date: e.target.value })} />}</Field>
          <Field label="Planned per month (EUR)">{(id) => <Input id={id} inputMode="decimal" value={v.monthly} onChange={(e) => setV({ ...v, monthly: e.target.value.replace(",", ".") })} />}</Field>
        </div>
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} />}
      </div>
    </Dialog>
  );
}
