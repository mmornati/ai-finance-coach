import { LogOut, PiggyBank } from "lucide-react";
import { Async, Badge, Card, EmptyState, IconButton, Money, Notice, ProgressBar, Skeleton, Stat } from "@/components/ui";
import { ShareBar, Sparkline } from "@/components/charts";
import { useMeSummary, useMeTransactions } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtMonth, parseMoney } from "@/lib/format";
import type { KidBudgetStatus } from "@/api/types";

/** What a child login sees: their own money only (read-only). Nothing about the household, the other members, the accounts or the memory. */
export default function KidHome() {
  const q = useMeSummary();
  const tx = useMeTransactions(30);
  return (
    <div className="mx-auto min-h-dvh w-full max-w-3xl px-4 py-5 sm:px-6">
      <header className="mb-5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="size-7 rounded-md" />
          <h1 className="text-lg font-semibold tracking-tight">My money</h1>
        </div>
        <IconButton label="Sign out of this browser" onClick={() => void api.logout().finally(() => window.location.assign("/"))}>
          <LogOut className="size-[18px]" />
        </IconButton>
      </header>
      <Async q={q} skeleton={<Skeleton className="h-72 w-full" />}>
        {(d) => {
          const r = d.report;
          const trend = r.balance.trend.map((p) => parseMoney(p.end_balance) ?? 0);
          const maxCat = Math.max(1, ...r.spending.by_category.map((c) => parseMoney(c.total) ?? 0));
          return (
            <div className="grid gap-4">
              <p className="text-sm text-muted">Hello {d.name.split(" ")[0]}. This is only your own money; you can look, nothing here can be changed.</p>
              <div className="grid grid-cols-2 gap-3">
                <Stat label="My balance" value={r.balance.current ? fmtMoney(r.balance.current) : "unknown"} hint={r.balance.as_of ? `as of ${fmtDate(r.balance.as_of, "dayMonth")}` : undefined} big />
                <Stat label="Spent this month" value={fmtMoney(r.spending.this_month_to_date)} hint={`${fmtMoney(r.spending.monthly_avg)} a month on average`} />
              </div>
              {d.budgets.map((b) => (
                <Budget key={b.id} b={b} />
              ))}
              <Card title="Pocket money">
                {r.pocket_money.series.length === 0 ? (
                  <p className="text-[13px] text-muted">No regular pocket money seen yet.</p>
                ) : (
                  <ul className="grid gap-1 text-sm">
                    {r.pocket_money.series.map((s) => (
                      <li key={s.cadence + s.amount}>
                        <b><Money v={s.amount} /></b> {s.cadence}, from {s.source}. Next one about {fmtDate(s.next_expected, "dayMonth")}.
                      </li>
                    ))}
                  </ul>
                )}
                {r.extra_topups.count > 0 && (
                  <p className="mt-2 text-[13px] text-muted">
                    Extra money you received: {fmtMoney(r.extra_topups.total)} ({Object.entries(r.extra_topups.by_source).map(([k, v]) => `${fmtMoney(v)} from ${k}`).join(", ")}).
                  </p>
                )}
              </Card>
              <Card title="Where my money goes">
                {r.spending.by_category.length === 0 ? (
                  <p className="text-[13px] text-muted">Nothing spent in the last months.</p>
                ) : (
                  <ul className="grid gap-2.5">
                    {r.spending.by_category.slice(0, 6).map((c) => (
                      <li key={c.category}>
                        <div className="flex items-baseline justify-between gap-3 text-sm"><span>{catLabel(c.category)}</span><Money v={c.total} /></div>
                        <ShareBar value={parseMoney(c.total) ?? 0} max={maxCat} />
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-3 text-xs text-muted">{r.spending.by_month.map((m) => `${fmtMonth(m.month)} ${fmtMoney(m.total, { round: true })}`).join(" · ")}</p>
              </Card>
              {trend.length > 1 && (
                <Card title="My balance over time" subtitle="An estimate at the end of each month.">
                  <Sparkline values={trend} height={64} label="My balance at each month end" />
                </Card>
              )}
            </div>
          );
        }}
      </Async>
      <div className="mt-4">
        <Async q={tx} skeleton={<Skeleton className="h-40 w-full" />}>
          {(t) => (
            <Card title="My latest payments" pad={false}>
              {t.items.length === 0 ? (
                <EmptyState icon={<PiggyBank className="size-6" />} title="No payment yet">Your payments will appear here.</EmptyState>
              ) : (
                <ul className="divide-y divide-border">
                  {t.items.map((i, k) => (
                    <li key={`${i.date}-${k}`} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{i.merchant}</span>
                        <span className="block text-xs text-muted">{fmtDate(i.date, "medium")} · {catLabel(i.category)}</span>
                      </span>
                      <Money v={i.amount} colored signed />
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}
        </Async>
      </div>
    </div>
  );
}

function Budget({ b }: { b: KidBudgetStatus }) {
  const left = fmtMoney(b.remaining);
  const msg = b.status === "over" ? "You went over this limit. No worries: have a look at it with a parent." : b.status === "at_risk" ? `Nearly there: ${left} left, ${b.days_left} day(s) to go.` : `${left} left, ${b.days_left} day(s) to go.`;
  return (
    <Card title={b.period === "weekly" ? "This week's limit" : "This month's limit"} action={<Badge tone={b.status === "over" ? "warn" : b.status === "at_risk" ? "warn" : "pos"}>{b.status === "over" ? "over" : b.status === "at_risk" ? "close" : "ok"}</Badge>}>
      <div className="mb-2 flex items-baseline justify-between text-sm"><span><Money v={b.spent} /> of <Money v={b.limit} /></span></div>
      <ProgressBar label="Limit used" value={Math.min(b.ratio, 1.2) * 100} max={120} marker={(100 / 120) * 100} tone={b.status === "ok" ? "info" : "warn"} />
      <Notice tone={b.status === "ok" ? "info" : "warn"} className="mt-3">{msg}</Notice>
    </Card>
  );
}
