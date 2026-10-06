import { useMemo, useState } from "react";
import { Baby, Pencil, Plus, Trash2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Segmented, Select, Skeleton, Stat, Tabs } from "@/components/ui";
import { Sparkline, ShareBar } from "@/components/charts";
import { useDryRun, useKidBudgets, useKids, usePeople, useTaxonomy, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney } from "@/lib/format";
import type { EditResult, KidBudgetStatus, KidReport } from "@/api/types";

export default function Kids() {
  const [months, setMonths] = useState<"3" | "6" | "12">("6");
  const q = useKids(Number(months));
  const budgets = useKidBudgets();
  const people = usePeople();
  const [who, setWho] = useState("");
  return (
    <>
      <PageHeader
        title="Kids' money"
        subtitle="Pocket money, extra top-ups and what each child spends. A parent's top-up is an internal transfer for the household (not spending, not income) but it is the child's income here."
        actions={
          <Segmented
            label="Period"
            value={months}
            onChange={setMonths}
            options={[{ value: "3", label: "3 months" }, { value: "6", label: "6 months" }, { value: "12", label: "12 months" }]}
          />
        }
      />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          if (d.children.length === 0)
            return (
              <Card>
                <EmptyState icon={<Baby className="size-6" />} title="No child declared">
                  Declare a member with the role child (<code>coach memory member add --role child</code>), then give their account an owner on the Household page.
                </EmptyState>
              </Card>
            );
          const current = d.children.find((c) => c.member === who) ?? d.children[0];
          return (
            <div className="grid gap-4">
              {d.children.length > 1 && (
                <Tabs label="Child" value={current.member} onChange={setWho} tabs={d.children.map((c) => ({ value: c.member, label: people.first(c.member) }))} />
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
  const people = usePeople();
  const [pocket, setPocket] = useState(false);
  const [budget, setBudget] = useState(false);
  const trend = r.balance.trend.map((p) => parseMoney(p.end_balance) ?? 0);
  const maxCat = Math.max(1, ...r.spending.by_category.map((c) => parseMoney(c.total) ?? 0));
  const del = useWrite((id: string) => api.post<EditResult>(`/household/kid-budgets/${id}/delete`, {}), { success: "Kid budget removed" });
  const share = r.ratio.pocket_share;
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Balance" value={r.balance.current ? fmtMoney(r.balance.current) : "unknown"} hint={r.balance.as_of ? `as of ${fmtDate(r.balance.as_of, "dayMonth")}` : r.balance.unknown.length ? "no balance synced" : "the child owns no account"} />
        <Stat label="Pocket money" value={fmtMoney(r.pocket_money.monthly_equivalent)} hint="per month, in the period" />
        <Stat label="Extra top-ups" value={fmtMoney(r.extra_topups.total)} hint={`${r.extra_topups.count} credit(s)`} />
        <Stat label="Pocket money vs extra" value={share == null ? "-" : `${fmtPct(share)} / ${fmtPct(r.ratio.extra_share)}`} hint={share == null ? "no credit in the period" : "pocket money / extra"} />
      </div>
      {share != null && <ProgressBar label="Share of the credits that is regular pocket money" value={share * 100} max={100} tone="info" />}
      {r.notes.map((n) => (
        <Notice key={n}>{n}</Notice>
      ))}

      <Card
        title="Pocket money"
        subtitle="A regular credit: three or more of about the same amount, from the same source, at a weekly, fortnightly or monthly rhythm."
        action={<Button size="sm" onClick={() => setPocket(true)}><Pencil className="size-3.5" aria-hidden /> Declare</Button>}
      >
        {r.pocket_money.series.length === 0 ? (
          <p className="text-[13px] text-muted">No regular top-up detected yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {r.pocket_money.series.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span><b><Money v={s.amount} /></b> {s.cadence} from {people.name(s.source)} <span className="text-muted">· {s.count} payments, last {fmtDate(s.last, "dayMonth")}</span></span>
                <Badge>next about {fmtDate(s.next_expected, "dayMonth")}</Badge>
              </li>
            ))}
          </ul>
        )}
        {r.pocket_money.declared && (
          <p className="mt-2 text-xs text-muted">Declared: {fmtMoney(r.pocket_money.declared.amount)} {r.pocket_money.declared.period}; {r.pocket_money.declared.matches} credit(s) match it.</p>
        )}
      </Card>

      <Card title="Extra top-ups" subtitle="Every other credit, with where it came from." pad={false}>
        {r.extra_topups.items.length === 0 ? (
          <p className="px-4 pb-4 text-[13px] text-muted">None in the period.</p>
        ) : (
          <ul className="divide-y divide-border">
            {r.extra_topups.items.map((i, k) => (
              <li key={`${i.date}-${k}`} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                <span>{fmtDate(i.date, "medium")} · {people.name(i.source)}{i.linked && <Badge tone="pos" className="ml-2">internal transfer</Badge>}</span>
                <Money v={i.amount} colored signed />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Spending by category" subtitle={`${fmtMoney(r.spending.total)} in the period · ${fmtMoney(r.spending.monthly_avg)} per month`}>
          {r.spending.by_category.length === 0 ? (
            <p className="text-[13px] text-muted">No spending attributed to this child in the period.</p>
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
        <Card title="Balance trend" subtitle="Month ends, rebuilt backwards from the newest balance (an estimate).">
          {trend.length < 2 ? <p className="text-[13px] text-muted">Not enough history to draw a trend.</p> : <Sparkline values={trend} height={64} label="Balance at each month end" />}
          <ul className="mt-2 grid gap-0.5 text-xs text-muted">
            {r.balance.trend.map((p) => (
              <li key={p.month} className="flex justify-between"><span>{fmtMonth(p.month, "long")}</span><Money v={p.end_balance} /></li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Budgets" subtitle="Weekly or monthly limits. Gentle alerts appear in the local alerts feed only: nothing about a child is ever sent outside this machine."
        action={<Button size="sm" onClick={() => setBudget(true)}><Plus className="size-3.5" aria-hidden /> Budget</Button>}>
        {budgets.length === 0 ? (
          <p className="text-[13px] text-muted">No budget for this child.</p>
        ) : (
          <ul className="grid gap-3">
            {budgets.map((b) => (
              <li key={b.id}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="font-medium">{b.period === "weekly" ? "This week" : "This month"}{b.category ? ` · ${catLabel(b.category)}` : b.group ? ` · ${groupLabel(b.group)}` : ""}</span>
                  <span className="flex items-center gap-2">
                    <span className="num text-muted"><Money v={b.spent} /> / <Money v={b.limit} /></span>
                    <Badge tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "pos"}>{b.status === "over" ? "over" : b.status === "at_risk" ? "close" : "ok"}</Badge>
                    <Button size="sm" variant="ghost" aria-label={`Remove budget ${b.id}`} onClick={() => confirm(`Remove the kid budget "${b.id}"?`) && del.mutate(b.id)}><Trash2 className="size-3.5" /></Button>
                  </span>
                </div>
                <ProgressBar label={`${b.id} used`} value={Math.min(b.ratio, 1.2) * 100} max={120} marker={(100 / 120) * 100} tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "info"} />
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
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState<"weekly" | "monthly">("monthly");
  const body = useMemo(() => ({ amount: parseMoney(amount), period }), [amount, period]);
  const ready = !!amount && (parseMoney(amount) ?? 0) > 0;
  const pv = useDryRun<EditResult>(`/household/members/${member}/pocket-money`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/members/${member}/pocket-money`, body), { success: "Pocket money declared", onSuccess: onClose });
  const clear = useWrite(() => api.put<EditResult>(`/household/members/${member}/pocket-money`, {}), { success: "Declaration removed", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Declare the pocket money" description="The amount a child is meant to get. A credit of about this amount counts as pocket money even before a rhythm is detected."
      footer={<><Button variant="ghost" onClick={() => clear.mutate(undefined as never)}>Remove declaration</Button><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Amount (EUR)">{(id) => <Input id={id} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="10" />}</Field>
        <Field label="Every">{(id) => <Select id={id} value={period} onChange={(e) => setPeriod(e.target.value as "weekly" | "monthly")}><option value="weekly">week</option><option value="monthly">month</option></Select>}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

function BudgetDialog({ member, onClose }: { member: string; onClose: () => void }) {
  const tax = useTaxonomy();
  const [id, setId] = useState("");
  const [period, setPeriod] = useState<"weekly" | "monthly">("weekly");
  const [limit, setLimit] = useState("");
  const [group, setGroup] = useState("");
  const body = useMemo(() => ({ member, period, limit: parseMoney(limit), group: group || undefined }), [member, period, limit, group]);
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id) && (parseMoney(limit) ?? 0) > 0;
  const pv = useDryRun<EditResult>(`/household/kid-budgets/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/kid-budgets/${id}`, body), { success: "Kid budget saved", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="New kid budget" description="A limit on what this child spends; leave the category empty for all their spending."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Id" hint="lowercase letters, digits, - or _">{(fid) => <Input id={fid} value={id} onChange={(e) => setId(e.target.value)} placeholder="weekly-snacks" />}</Field>
        <Field label="Limit (EUR)">{(fid) => <Input id={fid} value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" placeholder="15" />}</Field>
        <Field label="Period">{(fid) => <Select id={fid} value={period} onChange={(e) => setPeriod(e.target.value as "weekly" | "monthly")}><option value="weekly">Weekly (Monday to Sunday)</option><option value="monthly">Monthly</option></Select>}</Field>
        <Field label="Only for">{(fid) => <Select id={fid} value={group} onChange={(e) => setGroup(e.target.value)}><option value="">All spending</option>{(tax.data?.groups ?? []).map((g) => <option key={g.id} value={g.id}>{groupLabel(g.id)}</option>)}</Select>}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}
