import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Baby, Pencil, Plus, Trash2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Segmented, Select, Skeleton, Stat, Tabs } from "@/components/ui";
import { Sparkline, ShareBar } from "@/components/charts";
import { useDryRun, useKidBudgets, useKids, usePeople, useTaxonomy, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney } from "@/lib/format";
import { useServerText } from "@/i18n/server";
import type { EditResult, KidBudgetStatus, KidReport } from "@/api/types";

/** The rhythm codes the server sends for a pocket-money series (an unknown code is shown as sent). */
const CADENCE: Record<string, ParseKeys<"kids">> = { weekly: "kids.cadence.weekly", fortnightly: "kids.cadence.fortnightly", monthly: "kids.cadence.monthly" };
const STATUS: Record<KidBudgetStatus["status"], ParseKeys<"kids">> = { over: "kids.status.over", at_risk: "kids.status.close", ok: "kids.status.ok" };

export default function Kids() {
  const { t } = useTranslation("kids");
  const [months, setMonths] = useState<"3" | "6" | "12">("6");
  const q = useKids(Number(months));
  const budgets = useKidBudgets();
  const people = usePeople();
  const [who, setWho] = useState("");
  return (
    <>
      <PageHeader
        title={t("kids.header.title")}
        subtitle={t("kids.header.subtitle")}
        actions={
          <Segmented
            label={t("kids.period")}
            value={months}
            onChange={setMonths}
            options={(["3", "6", "12"] as const).map((v) => ({ value: v, label: t("kids.months", { count: Number(v) }) }))}
          />
        }
      />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          if (d.children.length === 0)
            return (
              <Card>
                <EmptyState icon={<Baby className="size-6" />} title={t("kids.empty.title")}>
                  <Trans t={t} i18nKey="kids.empty.body" components={{ code: <code /> }} />
                </EmptyState>
              </Card>
            );
          const current = d.children.find((c) => c.member === who) ?? d.children[0];
          return (
            <div className="grid gap-4">
              {d.children.length > 1 && (
                <Tabs label={t("kids.child")} value={current.member} onChange={setWho} tabs={d.children.map((c) => ({ value: c.member, label: people.first(c.member) }))} />
              )}
              <Child r={current} budgets={(budgets.data?.budgets ?? []).filter((b) => b.member === current.member)} />
            </div>
          );
        }}
      </Async>
    </>
  );
}

function Child({ r, budgets }: { r: KidReport; budgets: KidBudgetStatus[] }) {
  const { t } = useTranslation("kids");
  const { tServerList } = useServerText();
  const people = usePeople();
  const [pocket, setPocket] = useState(false);
  const [budget, setBudget] = useState(false);
  const trend = r.balance.trend.map((p) => parseMoney(p.end_balance) ?? 0);
  const maxCat = Math.max(1, ...r.spending.by_category.map((c) => parseMoney(c.total) ?? 0));
  const del = useWrite((id: string) => api.post<EditResult>(`/household/kid-budgets/${id}/delete`, {}), { success: t("kids.budgets.removed") });
  const share = r.ratio.pocket_share;
  const cadence = (c: string) => (CADENCE[c] ? t(CADENCE[c]) : c);
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label={t("kids.stat.balance")}
          value={r.balance.current ? fmtMoney(r.balance.current) : t("kids.stat.unknown")}
          hint={r.balance.as_of ? t("kids.stat.asOf", { date: fmtDate(r.balance.as_of, "dayMonth") }) : r.balance.unknown.length ? t("kids.stat.noBalance") : t("kids.stat.noAccount")}
        />
        <Stat label={t("kids.stat.pocket")} value={fmtMoney(r.pocket_money.monthly_equivalent)} hint={t("kids.stat.pocketHint")} />
        <Stat label={t("kids.stat.extra")} value={fmtMoney(r.extra_topups.total)} hint={t("kids.stat.credits", { count: r.extra_topups.count })} />
        <Stat
          label={t("kids.stat.ratio")}
          value={share == null ? "-" : `${fmtPct(share)} / ${fmtPct(r.ratio.extra_share)}`}
          hint={share == null ? t("kids.stat.noCredit") : t("kids.stat.ratioHint")}
        />
      </div>
      {share != null && <ProgressBar label={t("kids.stat.ratioBar")} value={share * 100} max={100} tone="info" />}
      {tServerList(r.notes, r.notes_msg).map((n) => (
        <Notice key={n}>{n}</Notice>
      ))}

      <Card
        title={t("kids.pocket.title")}
        subtitle={t("kids.pocket.subtitle")}
        action={<Button size="sm" onClick={() => setPocket(true)}><Pencil className="size-3.5" aria-hidden /> {t("kids.pocket.declare")}</Button>}
      >
        {r.pocket_money.series.length === 0 ? (
          <p className="text-[13px] text-muted">{t("kids.pocket.empty")}</p>
        ) : (
          <ul className="divide-y divide-border">
            {r.pocket_money.series.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span>
                  <Trans
                    t={t}
                    i18nKey="kids.pocket.series"
                    count={s.count}
                    values={{ cadence: cadence(s.cadence), source: people.name(s.source), last: fmtDate(s.last, "dayMonth") }}
                    components={{ b: <b />, amount: <Money v={s.amount} />, muted: <span className="text-muted" /> }}
                  />
                </span>
                <Badge>{t("kids.pocket.next", { date: fmtDate(s.next_expected, "dayMonth") })}</Badge>
              </li>
            ))}
          </ul>
        )}
        {r.pocket_money.declared && (
          <p className="mt-2 text-xs text-muted">
            {t("kids.pocket.declared", {
              amount: fmtMoney(r.pocket_money.declared.amount),
              period: cadence(r.pocket_money.declared.period),
              count: r.pocket_money.declared.matches,
            })}
          </p>
        )}
      </Card>

      <Card title={t("kids.extra.title")} subtitle={t("kids.extra.subtitle")} pad={false}>
        {r.extra_topups.items.length === 0 ? (
          <p className="px-4 pb-4 text-[13px] text-muted">{t("kids.extra.empty")}</p>
        ) : (
          <ul className="divide-y divide-border">
            {r.extra_topups.items.map((i, k) => (
              <li key={`${i.date}-${k}`} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                <span>{fmtDate(i.date, "medium")} · {people.name(i.source)}{i.linked && <Badge tone="pos" className="ml-2">{t("kids.extra.internal")}</Badge>}</span>
                <Money v={i.amount} colored signed />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t("kids.spending.title")} subtitle={t("kids.spending.subtitle", { total: fmtMoney(r.spending.total), avg: fmtMoney(r.spending.monthly_avg) })}>
          {r.spending.by_category.length === 0 ? (
            <p className="text-[13px] text-muted">{t("kids.spending.empty")}</p>
          ) : (
            <ul className="grid gap-2.5">
              {r.spending.by_category.map((c) => (
                <li key={c.category}>
                  <div className="flex items-baseline justify-between gap-3 text-sm"><span>{catLabel(c.category)}</span><span className="num text-muted"><Money v={c.total} /> · {c.n}</span></div>
                  <ShareBar value={parseMoney(c.total) ?? 0} max={maxCat} />
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted">{r.spending.by_month.map((m) => `${fmtMonth(m.month)} ${fmtMoney(m.total, { round: true })}`).join(" · ")}</p>
        </Card>
        <Card title={t("kids.trend.title")} subtitle={t("kids.trend.subtitle")}>
          {trend.length < 2 ? <p className="text-[13px] text-muted">{t("kids.trend.short")}</p> : <Sparkline values={trend} height={64} label={t("kids.trend.label")} />}
          <ul className="mt-2 grid gap-0.5 text-xs text-muted">
            {r.balance.trend.map((p) => (
              <li key={p.month} className="flex justify-between"><span>{fmtMonth(p.month, "long")}</span><Money v={p.end_balance} /></li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title={t("kids.budgets.title")} subtitle={t("kids.budgets.subtitle")}
        action={<Button size="sm" onClick={() => setBudget(true)}><Plus className="size-3.5" aria-hidden /> {t("kids.budgets.add")}</Button>}>
        {budgets.length === 0 ? (
          <p className="text-[13px] text-muted">{t("kids.budgets.empty")}</p>
        ) : (
          <ul className="grid gap-3">
            {budgets.map((b) => (
              <li key={b.id}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="font-medium">{b.period === "weekly" ? t("kids.budgets.thisWeek") : t("kids.budgets.thisMonth")}{b.category ? ` · ${catLabel(b.category)}` : b.group ? ` · ${groupLabel(b.group)}` : ""}</span>
                  <span className="flex items-center gap-2">
                    <span className="num text-muted"><Money v={b.spent} /> / <Money v={b.limit} /></span>
                    <Badge tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "pos"}>{t(STATUS[b.status] ?? "kids.status.ok")}</Badge>
                    <Button size="sm" variant="ghost" aria-label={t("kids.budgets.remove", { id: b.id })} onClick={() => confirm(t("kids.budgets.confirmRemove", { id: b.id })) && del.mutate(b.id)}><Trash2 className="size-3.5" /></Button>
                  </span>
                </div>
                <ProgressBar label={t("kids.budgets.used", { id: b.id })} value={Math.min(b.ratio, 1.2) * 100} max={120} marker={(100 / 120) * 100} tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "info"} />
              </li>
            ))}
          </ul>
        )}
      </Card>
      {pocket && <PocketDialog member={r.member} onClose={() => setPocket(false)} />}
      {budget && <BudgetDialog member={r.member} onClose={() => setBudget(false)} />}
    </div>
  );
}

function PocketDialog({ member, onClose }: { member: string; onClose: () => void }) {
  const { t } = useTranslation("kids");
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState<"weekly" | "monthly">("monthly");
  const body = useMemo(() => ({ amount: parseMoney(amount), period }), [amount, period]);
  const ready = !!amount && (parseMoney(amount) ?? 0) > 0;
  const pv = useDryRun<EditResult>(`/household/members/${member}/pocket-money`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/members/${member}/pocket-money`, body), { success: t("kids.pocketDialog.saved"), onSuccess: onClose });
  const clear = useWrite(() => api.put<EditResult>(`/household/members/${member}/pocket-money`, {}), { success: t("kids.pocketDialog.removed"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("kids.pocketDialog.title")} description={t("kids.pocketDialog.description")}
      footer={<><Button variant="ghost" onClick={() => clear.mutate(undefined as never)}>{t("kids.pocketDialog.remove")}</Button><Button onClick={onClose}>{t("kids.pocketDialog.cancel")}</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("kids.pocketDialog.save")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("kids.pocketDialog.amount")}>{(id) => <Input id={id} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="10" />}</Field>
        <Field label={t("kids.pocketDialog.every")}>{(id) => <Select id={id} value={period} onChange={(e) => setPeriod(e.target.value as "weekly" | "monthly")}><option value="weekly">{t("kids.pocketDialog.week")}</option><option value="monthly">{t("kids.pocketDialog.month")}</option></Select>}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

function BudgetDialog({ member, onClose }: { member: string; onClose: () => void }) {
  const { t } = useTranslation("kids");
  const tax = useTaxonomy();
  const [id, setId] = useState("");
  const [period, setPeriod] = useState<"weekly" | "monthly">("weekly");
  const [limit, setLimit] = useState("");
  const [group, setGroup] = useState("");
  const body = useMemo(() => ({ member, period, limit: parseMoney(limit), group: group || undefined }), [member, period, limit, group]);
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id) && (parseMoney(limit) ?? 0) > 0;
  const pv = useDryRun<EditResult>(`/household/kid-budgets/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/kid-budgets/${id}`, body), { success: t("kids.budgetDialog.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("kids.budgetDialog.title")} description={t("kids.budgetDialog.description")}
      footer={<><Button onClick={onClose}>{t("kids.budgetDialog.cancel")}</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("kids.budgetDialog.save")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("kids.budgetDialog.id")} hint={t("kids.budgetDialog.idHint")}>{(fid) => <Input id={fid} value={id} onChange={(e) => setId(e.target.value)} placeholder="weekly-snacks" />}</Field>
        <Field label={t("kids.budgetDialog.limit")}>{(fid) => <Input id={fid} value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" placeholder="15" />}</Field>
        <Field label={t("kids.budgetDialog.period")}>{(fid) => <Select id={fid} value={period} onChange={(e) => setPeriod(e.target.value as "weekly" | "monthly")}><option value="weekly">{t("kids.budgetDialog.weekly")}</option><option value="monthly">{t("kids.budgetDialog.monthly")}</option></Select>}</Field>
        <Field label={t("kids.budgetDialog.onlyFor")}>{(fid) => <Select id={fid} value={group} onChange={(e) => setGroup(e.target.value)}><option value="">{t("kids.budgetDialog.all")}</option>{(tax.data?.groups ?? []).map((g) => <option key={g.id} value={g.id}>{groupLabel(g.id)}</option>)}</Select>}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}
