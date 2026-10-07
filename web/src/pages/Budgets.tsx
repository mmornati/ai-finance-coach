import { useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Pencil, Plus, Target, Trash2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Select, Skeleton, Spinner } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { useBudgets, useDryRun, useFilters, useGet, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney } from "@/lib/format";
import type { BudgetProgress, BudgetSuggestion, EditResult, GoalProgress } from "@/api/types";

interface Draft { target: string; monthly: string; rollover: boolean; owner: string; account: string; note: string; id?: string }

// codes sent by the analytics; an unknown one is shown as it is
const GOAL_STATUS: Record<string, ParseKeys<"budgets">> = { no_data: "goals.status.no_data", achieved: "goals.status.achieved", on_track: "goals.status.on_track", behind: "goals.status.behind", no_pace: "goals.status.no_pace", overdue: "goals.status.overdue" };
const FLAG: Record<string, ParseKeys<"budgets">> = { no_matching_account: "card.flag.no_matching_account", stale_data: "card.flag.stale_data", includes_carry_over: "card.flag.includes_carry_over" };

export default function Budgets() {
  const q = useBudgets();
  const sug = useScoped<{ suggestions: BudgetSuggestion[] }>("/budgets/suggestions", { limit: 12 });
  const goals = useGet<{ goals: GoalProgress[]; warnings: string[] }>("/goals");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [goalOpen, setGoalOpen] = useState(false);
  const { t } = useTranslation("budgets");
  const del = useWrite((id: string) => api.post<EditResult>(`/budgets/${id}/delete`, {}, { dry_run: false }), { success: t("removed") });
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} actions={<Button variant="primary" onClick={() => setDraft({ target: "", monthly: "", rollover: false, owner: "", account: "", note: "" })}><Plus className="size-4" aria-hidden /> {t("new")}</Button>} />
      <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            {d.problems.length > 0 && <Notice tone="warn" title={t("invalidTitle")}>{t("invalidBody", { problems: d.problems.join("; ") })}</Notice>}
            <div className="flex flex-wrap gap-2 text-sm text-muted">
              <span>{t("asOf", { month: fmtMonth(d.month, "long"), date: fmtDate(d.as_of, "dayMonth") })}</span>
              <Badge tone="neg">{t("count.over", { n: d.counts.over ?? 0 })}</Badge><Badge tone="warn">{t("count.atRisk", { n: d.counts.at_risk ?? 0 })}</Badge><Badge tone="pos">{t("count.ok", { n: d.counts.ok ?? 0 })}</Badge>
            </div>
            {d.budgets.length === 0 ? (
              <Card><EmptyState icon={<Target className="size-6" />} title={t("emptyTitle")}>{t("emptyBody")}</EmptyState></Card>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {d.budgets.map((b) => <BudgetCard key={b.id} b={b} onEdit={() => setDraft({ id: b.id, target: b.category ?? b.group ?? "", monthly: String(parseMoney(b.monthly) ?? ""), rollover: b.rollover, owner: b.owner ?? "", account: b.account ?? "", note: b.note ?? "" })} onDelete={() => confirm(t("confirmRemove", { target: b.target })) && del.mutate(b.id)} />)}
              </div>
            )}
            {d.unbudgeted.length > 0 && (
              <Card title={t("unbudgeted")}>
                <ul className="divide-y divide-border">
                  {d.unbudgeted.slice(0, 5).map((u) => (
                    <li key={u.category} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <Link to={`/categories/${u.category}`} className="hover:underline">{catLabel(u.category)}</Link>
                      <span className="flex items-center gap-3"><Money v={u.spent} /><Button size="sm" onClick={() => setDraft({ target: u.category, monthly: "", rollover: false, owner: "", account: "", note: "" })}>{t("setBudget")}</Button></span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {parseMoney(d.unallocated_refunds) ? <Notice>{t("refunds", { amount: fmtMoney(d.unallocated_refunds) })}</Notice> : null}
            <Async q={sug} skeleton={<Skeleton className="h-40 w-full" />}>
              {(s) => (
                <Card title={t("suggested.title")} subtitle={t("suggested.subtitle")} pad={false}>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[480px] text-sm">
                      <thead className="text-left text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-medium">{t("suggested.category")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("suggested.suggested")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("suggested.medianMean")}</th><th scope="col" className="px-4 py-2" /></tr></thead>
                      <tbody>
                        {s.suggestions.map((x) => (
                          <tr key={x.category} className="border-t border-border">
                            <td className="px-4 py-2 font-medium">{catLabel(x.category)} <span className="text-xs font-normal text-faint">{t("suggested.months", { n: x.n_months })}</span>{x.low_confidence && <Badge tone="warn" className="ml-2">{t("suggested.lowConfidence")}</Badge>}</td>
                            <td className="num px-2 py-2 text-right font-semibold">{fmtMoney(x.suggested, { round: true })}</td>
                            <td className="num px-2 py-2 text-right text-muted">{fmtMoney(x.median, { round: true })} / {fmtMoney(x.mean, { round: true })}</td>
                            <td className="px-4 py-2 text-right">{x.existing ? <Badge>{t("suggested.set", { amount: fmtMoney(x.existing, { round: true }) })}</Badge> : <Button size="sm" onClick={() => setDraft({ target: x.category, monthly: String(parseMoney(x.suggested)), rollover: false, owner: "", account: "", note: "" })}>{t("suggested.accept")}</Button>}</td>
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
                <Card title={t("goals.title")} action={<Button size="sm" onClick={() => setGoalOpen(true)}><Plus className="size-3.5" aria-hidden /> {t("goals.add")}</Button>}>
                  {g.goals.length === 0 ? <p className="text-[13px] text-muted">{t("goals.empty")}</p> : (
                    <ul className="grid gap-4">
                      {g.goals.map((x) => (
                        <li key={x.id}>
                          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm"><span className="font-medium">{x.title ?? x.id}</span><span className="num text-muted"><Money v={x.current} round /> / <Money v={x.target} round /></span></div>
                          <ProgressBar label={t("goals.progress", { goal: x.title ?? x.id })} value={x.percent ?? 0} max={1} tone="pos" />
                          <p className="mt-1 text-xs text-muted">{GOAL_STATUS[x.status] ? t(GOAL_STATUS[x.status]) : x.status.replace(/_/g, " ")}{x.projected_date ? t("goals.projected", { date: fmtDate(x.projected_date) }) : ""}{x.required_monthly ? t("goals.needs", { amount: fmtMoney(x.required_monthly, { round: true }) }) : ""}</p>
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
  const { t } = useTranslation("budgets");
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/categories/${b.category ?? b.group}`} className="block truncate font-semibold hover:underline">{b.category ? catLabel(b.category) : t("card.allOf", { group: groupLabel(b.group ?? "") })}</Link>
          <div className="mt-0.5 flex flex-wrap gap-1.5 text-xs text-muted">{b.rollover && <Badge>{parseMoney(b.carry) ? t("card.carried", { amount: fmtMoney(b.carry, { signed: true, round: true }) }) : t("card.rollover")}</Badge>}{b.owner && <Badge>{b.owner}</Badge>}{b.account && <Badge>{b.account}</Badge>}</div>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button size="sm" variant="ghost" onClick={onEdit} aria-label={t("card.edit", { target: b.target })}><Pencil className="size-3.5" /></Button>
          <Button size="sm" variant="ghost" onClick={onDelete} aria-label={t("card.remove", { target: b.target })}><Trash2 className="size-3.5" /></Button>
        </div>
      </div>
      <div className="mt-3 flex items-baseline justify-between gap-3">
        <div className="num text-xl font-semibold"><Money v={b.spent} round /> <span className="text-sm font-normal text-muted"><Trans t={t} i18nKey="card.of" components={{ amount: <Money v={b.available} round /> }} /></span></div>
        <Badge tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "pos"}>{b.status === "over" ? t("card.over") : b.status === "at_risk" ? t("card.atRisk") : t("card.onTrack")}</Badge>
      </div>
      <div className="mt-2"><ProgressBar label={t("card.used", { target: b.target })} value={spent} max={Math.max(avail, spent, proj)} marker={(avail / Math.max(avail, spent, proj)) * 100} tone={tone} /></div>
      <p className="mt-2 text-[13px] text-muted">
        <Trans t={t} i18nKey="card.projected" values={{ percent: b.projected_percent !== null ? ` (${fmtPct(b.projected_percent)})` : "" }} components={{ b: <b className="num text-text" />, projected: <Money v={b.projected} round />, left: <Money v={b.remaining} round colored /> }} />
        {b.excluded && parseMoney(b.excluded) ? <Trans t={t} i18nKey="card.oneOffs" components={{ amount: <Money v={b.excluded} round /> }} /> : null}
      </p>
      {b.flags.length > 0 && <p className="mt-1 text-xs text-faint">{b.flags.map((f) => (FLAG[f] ? t(FLAG[f]) : f)).join(", ")}</p>}
    </Card>
  );
}

function BudgetDialog({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const [v, setV] = useState<Draft>(draft);
  const filters = useFilters();
  const body = { target: v.target, monthly: v.monthly, rollover: v.rollover || undefined, owner: v.owner || undefined, account: v.account || undefined, note: v.note || undefined, id: v.id, replace: !!v.id };
  const valid = !!v.target && parseMoney(v.monthly) !== null && (parseMoney(v.monthly) ?? 0) > 0;
  const pv = useDryRun<EditResult>("/budgets", body, valid);
  const { t } = useTranslation("budgets");
  const save = useWrite(() => api.post<EditResult>("/budgets", body, { dry_run: false }), { success: t("dialog.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={v.id ? t("dialog.edit") : t("dialog.new")} description={t("dialog.description")} footer={<><Button variant="ghost" onClick={onClose}>{t("dialog.cancel")}</Button><Button variant="primary" disabled={!valid || !pv.data || pv.loading} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("dialog.save")}</Button></>}>
      <div className="grid gap-4">
        <Field label={t("dialog.target")} hint={t("dialog.targetHint")}>{(id) => <CategoryPicker id={id} allowEmpty emptyLabel={t("dialog.choose")} includeGroups value={v.target} onChange={(x) => setV({ ...v, target: x })} />}</Field>
        <Field label={t("dialog.monthly")}>{(id) => <Input id={id} inputMode="decimal" value={v.monthly} onChange={(e) => setV({ ...v, monthly: e.target.value.replace(",", ".") })} />}</Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.rollover} onChange={(e) => setV({ ...v, rollover: e.target.checked })} /> {t("dialog.rollover")}</label>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("dialog.onlyFor")}>{(id) => <Select id={id} value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })}><option value="">{t("dialog.everyone")}</option>{filters.data?.owners.map((o) => <option key={o} value={o}>{o}</option>)}</Select>}</Field>
          <Field label={t("dialog.onlyAccount")}>{(id) => <Select id={id} value={v.account} onChange={(e) => setV({ ...v, account: e.target.value })}><option value="">{t("dialog.allAccounts")}</option>{filters.data?.accounts.map((a) => <option key={a.uid} value={a.uid}>{a.label}</option>)}</Select>}</Field>
        </div>
        <Field label={t("dialog.note")}>{(id) => <Input id={id} value={v.note} onChange={(e) => setV({ ...v, note: e.target.value })} />}</Field>
        {pv.loading && <Spinner label={t("dialog.previewing")} />}
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} empty={t("dialog.already")} />}
      </div>
    </Dialog>
  );
}

function GoalDialog({ onClose }: { onClose: () => void }) {
  const [v, setV] = useState({ id: "", title: "", target: "", source: "tag", ref: "savings", date: "", monthly: "" });
  const body = { id: v.id, title: v.title || undefined, target: v.target, [v.source]: v.ref, date: v.date || undefined, monthly: v.monthly || undefined };
  const valid = /^[a-z0-9][a-z0-9_-]*$/.test(v.id) && !!v.target && !!v.ref;
  const pv = useDryRun<EditResult>("/goals", body, valid);
  const { t } = useTranslation("budgets");
  const save = useWrite(() => api.post<EditResult>("/goals", body, { dry_run: false }), { success: t("goalDialog.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("goalDialog.title")} footer={<><Button variant="ghost" onClick={onClose}>{t("dialog.cancel")}</Button><Button variant="primary" disabled={!valid || !pv.data} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("dialog.save")}</Button></>}>
      <div className="grid gap-4">
        <Field label={t("goalDialog.id")} hint={t("goalDialog.idHint")}>{(id) => <Input id={id} value={v.id} onChange={(e) => setV({ ...v, id: e.target.value.toLowerCase() })} placeholder={t("goalDialog.idPlaceholder")} />}</Field>
        <Field label={t("goalDialog.goalTitle")}>{(id) => <Input id={id} value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} />}</Field>
        <Field label={t("goalDialog.target")}>{(id) => <Input id={id} inputMode="decimal" value={v.target} onChange={(e) => setV({ ...v, target: e.target.value.replace(",", ".") })} />}</Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("goalDialog.follows")}>{(id) => <Select id={id} value={v.source} onChange={(e) => setV({ ...v, source: e.target.value })}><option value="tag">{t("goalDialog.tag")}</option><option value="asset">{t("goalDialog.asset")}</option><option value="account">{t("goalDialog.account")}</option></Select>}</Field>
          <Field label={v.source === "tag" ? t("goalDialog.tagLabel") : v.source === "asset" ? t("goalDialog.assetLabel") : t("goalDialog.accountLabel")}>{(id) => <Input id={id} value={v.ref} onChange={(e) => setV({ ...v, ref: e.target.value })} />}</Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("goalDialog.date")}>{(id) => <Input id={id} type="date" value={v.date} onChange={(e) => setV({ ...v, date: e.target.value })} />}</Field>
          <Field label={t("goalDialog.monthly")}>{(id) => <Input id={id} inputMode="decimal" value={v.monthly} onChange={(e) => setV({ ...v, monthly: e.target.value.replace(",", ".") })} />}</Field>
        </div>
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} />}
      </div>
    </Dialog>
  );
}
