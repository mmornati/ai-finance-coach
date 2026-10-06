import { Link } from "react-router";
import { AlertTriangle, ArrowRight, Bell, CalendarClock, CircleHelp, Lightbulb } from "lucide-react";
import { Async, Badge, Card, Dot, EmptyState, Money, Notice, PageHeader, ProgressBar, Skeleton, Stat } from "@/components/ui";
import { CashflowChart, ForecastChart, ShareBar } from "@/components/charts";
import { useAlerts, useBalances, useBudgets, useCashflow, useForecast, useGet, useHealth, useHumanize, useInsights, useMonthCategories, useQuestions, useScoped } from "@/api/hooks";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, fmtRelativeDays, parseMoney } from "@/lib/format";
import type { CalendarResult, SavingsView } from "@/api/types";
import { cn } from "@/lib/utils";

export default function Dashboard() {
  return (
    <>
      <PageHeader title="Dashboard" subtitle="Where the money is, where it goes, and what is coming." />
      <div className="grid gap-4 lg:grid-cols-3">
        <BalanceCard />
        <ThisMonth />
        <SavingsCard />
        <div className="lg:col-span-2">
          <CashflowCard />
        </div>
        <ForecastCard />
        <div className="lg:col-span-2">
          <TopCategories />
        </div>
        <BudgetCard />
        <UpcomingCard />
        <InsightsCard />
        <div className="grid content-start gap-4">
          <AlertsCard />
          <SubsSavingsCard />
          <QuestionsCard />
          <HealthCard />
        </div>
      </div>
    </>
  );
}

function BalanceCard() {
  const q = useBalances();
  return (
    <Card title="Balances" action={<Link to="/wealth" className="text-xs text-accent hover:underline">Net worth</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => (
          <>
            <Stat big label="Across all accounts" value={<Money v={d.household_total} />} hint={d.n_without_balance ? `${d.n_without_balance} account(s) without a balance yet` : undefined} />
            <ul className="mt-3 divide-y divide-border text-sm">
              {d.accounts.slice(0, 6).map((a) => (
                <li key={a.uid} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="min-w-0 truncate">
                    {a.label}
                    {a.stale && <Badge tone="warn" className="ml-2" title={`Last balance ${a.age_days} days ago`}>old</Badge>}
                    {a.balance !== null && !a.booked && <Badge className="ml-2" title="The bank only gives this kind of balance for this account">{a.balance_type_label}</Badge>}
                  </span>
                  <Money v={a.balance} colored className="font-medium" />
                </li>
              ))}
            </ul>
            {d.accounts.length > 6 && <p className="mt-2 text-xs text-faint">+ {d.accounts.length - 6} smaller or older accounts</p>}
            {d.mixed_types && <p className="mt-2 text-xs text-faint">{d.note}</p>}
          </>
        )}
      </Async>
    </Card>
  );
}

function ThisMonth() {
  const q = useMonthCategories();
  return (
    <Card title="This month so far">
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          const spent = parseMoney(d.total_spent) ?? 0;
          const avg = parseMoney(d.household_monthly_avg);
          const now = new Date();
          const frac = now.getDate() / new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
          return (
            <>
              <Stat big label={fmtMonth(d.month, "long")} value={<Money v={d.total_spent} />} />
              {avg !== null && avg > 0 ? (
                <div className="mt-3">
                  <ProgressBar label="Spent compared with a usual month" value={spent} max={Math.max(avg, spent)} marker={(Math.min(frac, 1) * Math.min(avg, Math.max(avg, spent)) / Math.max(avg, spent)) * 100} tone={spent > avg ? "neg" : "info"} />
                  <p className="mt-2 text-[13px] text-muted">
                    A usual month is <Money v={d.household_monthly_avg} className="font-medium text-text" /> (average of {d.household_avg_months} fully covered months, one-offs left out). The tick shows how far into the month we are.
                  </p>
                </div>
              ) : (
                <p className="mt-3 text-[13px] text-muted">No month is fully covered by every account yet, so there is no fair average to compare with.</p>
              )}
            </>
          );
        }}
      </Async>
    </Card>
  );
}

function SavingsCard() {
  const q = useCashflow(6);
  return (
    <Card title="Savings rate" subtitle="Complete months only">
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          const t = d.household.totals_complete;
          if (!t) return <EmptyState title="Not enough complete months">A month counts once every account carrying income or spending has all its days.</EmptyState>;
          return (
            <>
              <Stat big label={`Over ${t.n_months} month${t.n_months > 1 ? "s" : ""}`} value={fmtPct(t.savings_rate, 1)} tone={(t.savings_rate ?? 0) < 0 ? "neg" : undefined} hint={<>Net <Money v={t.net} signed colored /> of <Money v={t.income} round /> income</>} />
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
                <dt className="text-muted">Saved, in total</dt>
                <dd className="text-right"><Money v={t.saved} /></dd>
                <dt className="text-muted">Debt service, in total</dt>
                <dd className="text-right"><Money v={t.debt_service} /></dd>
              </dl>
              <p className="mt-2 text-xs text-faint">
                {t.loan_principal !== null && t.savings_rate_incl_principal !== null
                  ? <>Repaying loan principal (about <Money v={t.loan_principal} />) is also wealth you build: the rate including it is {fmtPct(t.savings_rate_incl_principal, 1)}.</>
                  : t.debt_service && parseMoney(t.debt_service) ? "Part of the debt service is principal, which builds wealth, but it cannot be estimated until the loans' rate and outstanding capital are known." : null}
              </p>
            </>
          );
        }}
      </Async>
    </Card>
  );
}

function CashflowCard() {
  const q = useCashflow(6);
  return (
    <Card title="Cash flow" subtitle="Income and spending per month; lighter bars are months not fully covered by every account">
      <Async q={q} skeleton={<Skeleton className="h-56 w-full" />}>
        {(d) => (
          <>
            <CashflowChart months={d.household.months} />
            {d.coverage.notes.slice(0, 2).map((n) => (
              <p key={n} className="mt-2 text-xs text-faint">{n}</p>
            ))}
          </>
        )}
      </Async>
    </Card>
  );
}

function ForecastCard() {
  const q = useForecast(90);
  return (
    <Card title="Next 90 days" action={<Link to="/calendar" className="text-xs text-accent hover:underline">Calendar</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-52 w-full" />}>
        {(d) => {
          const risky = [d.household, ...d.accounts].filter((a) => a.first_negative || a.first_at_risk);
          return (
            <>
              {d.household.points.length > 0 ? <ForecastChart f={d.household} height={190} /> : <EmptyState title="No forecast yet">A balance snapshot is needed.</EmptyState>}
              <p className="mt-2 text-[13px] text-muted">
                Lowest expected balance <Money v={d.household.min_balance} className="font-medium text-text" />, around {fmtDate(d.household.min_date, "dayMonth")}
              </p>
              {risky.length > 0 ? (
                <ul className="mt-2 grid gap-1.5">
                  {risky.slice(0, 3).map((a) => (
                    <li key={a.label}>
                      <Notice tone={a.first_negative ? "neg" : "warn"}>
                        <b>{a.label}</b>: {a.first_negative ? `projected below zero from ${fmtDate(a.first_negative, "dayMonth")}` : `could run short around ${fmtDate(a.first_at_risk, "dayMonth")}`}
                      </Notice>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 flex items-center gap-1.5 text-[13px] text-pos"><Dot level="green" /> No account is projected to run short.</p>
              )}
            </>
          );
        }}
      </Async>
    </Card>
  );
}

function TopCategories() {
  const q = useMonthCategories();
  return (
    <Card title="Top categories this month" subtitle="The tick marks a usual month for that category" action={<Link to="/categories" className="text-xs text-accent hover:underline">All categories</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-52 w-full" />}>
        {(d) => {
          const rows = d.categories.slice(0, 8);
          if (!rows.length) return <EmptyState title="No spending yet this month" />;
          const max = Math.max(...rows.map((r) => Math.max(parseMoney(r.spent) ?? 0, parseMoney(r.monthly_avg) ?? 0)), 1);
          return (
            <ul className="grid gap-3">
              {rows.map((r) => {
                const spent = parseMoney(r.spent) ?? 0;
                const avg = parseMoney(r.monthly_avg);
                return (
                  <li key={r.category}>
                    <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                      <Link to={`/categories/${r.category}`} className="min-w-0 truncate hover:underline">{catLabel(r.category)}</Link>
                      <span className="num shrink-0">
                        <b className="font-semibold">{fmtMoney(r.spent, { round: true })}</b>
                        {avg !== null && <span className="ml-2 text-xs text-faint">avg {fmtMoney(r.monthly_avg, { round: true })}</span>}
                      </span>
                    </div>
                    <ShareBar value={spent} max={max} marker={avg} color={avg !== null && spent > avg * 1.25 ? "var(--s2)" : "var(--s1)"} />
                  </li>
                );
              })}
            </ul>
          );
        }}
      </Async>
    </Card>
  );
}

function BudgetCard() {
  const q = useBudgets();
  return (
    <Card title="Budgets" action={<Link to="/budgets" className="text-xs text-accent hover:underline">Manage</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          if (!d.budgets.length) return <EmptyState title="No budgets yet">Suggestions are based on your last months of spending.</EmptyState>;
          const order = { over: 0, at_risk: 1, ok: 2 } as const;
          const rows = [...d.budgets].sort((a, b) => order[a.status] - order[b.status] || (b.percent_used ?? 0) - (a.percent_used ?? 0)).slice(0, 5);
          return (
            <ul className="grid gap-3">
              {rows.map((b) => (
                <li key={b.id}>
                  <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                    <span className="min-w-0 truncate">{b.category ? catLabel(b.category) : `All ${b.group}`}</span>
                    <span className="num text-xs text-muted"><Money v={b.spent} round /> / <Money v={b.available} round /></span>
                  </div>
                  <ProgressBar label={`${b.target} budget used`} value={parseMoney(b.spent) ?? 0} max={parseMoney(b.available) ?? 1} tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "info"} />
                </li>
              ))}
            </ul>
          );
        }}
      </Async>
    </Card>
  );
}

function UpcomingCard() {
  const q = useScoped<CalendarResult>("/calendar", { days: 14 });
  return (
    <Card title="Upcoming, next 14 days" action={<Link to="/calendar" className="text-xs text-accent hover:underline">Calendar</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          const items = d.items.filter((i) => i.source !== "asset").slice(0, 7);
          if (!items.length) return <EmptyState icon={<CalendarClock className="size-6" />} title="Nothing expected" />;
          return (
            <ul className="divide-y divide-border">
              {items.map((i) => (
                <li key={`${i.ref}${i.date}${i.kind}`} className="flex items-center gap-3 py-2 text-sm">
                  <div className="w-20 shrink-0 text-xs leading-tight text-muted">
                    <div className="font-medium text-text">{fmtDate(i.date, "dayMonth")}</div>
                    <div>{fmtRelativeDays(i.days_until)}</div>
                  </div>
                  <span className="min-w-0 flex-1 truncate">{i.title}</span>
                  {i.amount !== null && <Money v={i.amount} colored className="shrink-0 text-sm font-medium" />}
                </li>
              ))}
            </ul>
          );
        }}
      </Async>
    </Card>
  );
}

/** E8-6: what the household really saved on subscriptions and contracts (only what the bank data confirms). */
function SubsSavingsCard() {
  const q = useGet<SavingsView>("/subs/savings");
  return (
    <Card title="Subscription savings" action={<Link to="/subscriptions" className="text-xs text-accent hover:underline">Subscriptions</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-24 w-full" />}>
        {(d) => {
          const none = d.verified + d.pending + d.contradicted === 0;
          if (none && !d.proposed.length) return <EmptyState title="No decision recorded yet">When you cancel or renegotiate a subscription, record it on the Subscriptions page: the bank data then confirms the saving.</EmptyState>;
          return (
            <>
              <Stat big label="Saved per month, confirmed" value={<Money v={d.realised_monthly} />} tone={Number(d.realised_monthly) > 0 ? "pos" : undefined} hint={<><Money v={d.realised_since_decisions} /> since the decisions · <Money v={d.realised_yearly_run_rate} round /> a year at this rate</>} />
              <p className="mt-2 text-[13px] text-muted">
                {d.verified} confirmed{d.pending ? `, ${d.pending} waiting for the bank data` : ""}{d.contradicted ? `, ${d.contradicted} still being charged` : ""}.
                {d.pending > 0 && <> Claimed but not confirmed: <Money v={d.claimed_monthly_unverified} /> a month.</>}
              </p>
            </>
          );
        }}
      </Async>
    </Card>
  );
}

export function AlertsCard() {
  const q = useAlerts({ status: undefined });
  return (
    <Card title="Alerts" action={<Link to="/alerts" className="text-xs text-accent hover:underline">Open</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-20 w-full" />}>
        {(d) => {
          const open = d.items.filter((e) => e.status === "new" || e.status === "sent");
          if (!d.ready || !open.length) return <EmptyState icon={<Bell className="size-6" />} title="No open alert">Nothing needs your attention.</EmptyState>;
          const high = open.filter((e) => e.severity === "high").length;
          return (
            <Link to="/alerts" className="group block">
              <div className="flex items-center gap-2">
                <Badge tone={high ? "neg" : "warn"}>{open.length} open</Badge>
                {high > 0 && <Badge tone="neg">{high} high</Badge>}
              </div>
              <ul className="mt-2 grid gap-1 text-sm">
                {open.slice(0, 3).map((e) => <li key={e.id} className="truncate">{e.title}</li>)}
              </ul>
            </Link>
          );
        }}
      </Async>
    </Card>
  );
}

function InsightsCard() {
  const q = useInsights();
  const h = useHumanize();
  return (
    <Card title="Latest insights" action={<Link to="/insights" className="text-xs text-accent hover:underline">All</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          if (!d.cards.length) return <EmptyState icon={<Lightbulb className="size-6" />} title="All quiet">Nothing unusual to report.</EmptyState>;
          return (
            <ul className="grid gap-2.5">
              {d.cards.slice(0, 4).map((c) => (
                <li key={c.id} className="flex gap-2.5 text-sm">
                  <AlertTriangle className={cn("mt-0.5 size-4 shrink-0", c.severity === "high" ? "text-neg" : c.severity === "medium" ? "text-warn" : "text-faint")} aria-label={`${c.severity} severity`} />
                  <div className="min-w-0">
                    <div className="truncate font-medium">{h(c.title)}</div>
                    <div className="line-clamp-2 text-[13px] text-muted">{h(c.body)}</div>
                  </div>
                </li>
              ))}
            </ul>
          );
        }}
      </Async>
    </Card>
  );
}

function QuestionsCard() {
  const q = useQuestions("open");
  return (
    <Card title="Questions for you">
      <Async q={q} skeleton={<Skeleton className="h-16 w-full" />}>
        {(d) => {
          const n = d.counts.open ?? 0;
          return n ? (
            <Link to="/memory" className="group flex items-center gap-3">
              <CircleHelp className="size-8 text-accent" aria-hidden />
              <div className="flex-1">
                <div className="text-xl font-semibold num">{n}</div>
                <div className="text-[13px] text-muted">open question{n > 1 ? "s" : ""}: answers make the figures more accurate</div>
              </div>
              <ArrowRight className="size-4 text-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
            </Link>
          ) : (
            <p className="text-[13px] text-muted">Nothing to answer right now.</p>
          );
        }}
      </Async>
    </Card>
  );
}

function HealthCard() {
  const q = useHealth();
  return (
    <Card title="Connections" action={<Link to="/connections" className="text-xs text-accent hover:underline">Details</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-20 w-full" />}>
        {(d) => (
          <ul className="grid gap-1.5 text-sm">
            {d.banks.map((b) => (
              <li key={`${b.bank}${b.session_id}`} className="flex items-center gap-2">
                <Dot level={b.level} />
                <span className="min-w-0 flex-1 truncate">{b.bank}</span>
                <span className="text-xs text-muted">{b.consent_days_left !== null ? `${b.consent_days_left} d left` : b.consent_status ?? "manual"}</span>
              </li>
            ))}
            {d.memory && (d.memory.errors > 0 || d.memory.warnings > 0) && (
              <li className="flex items-center gap-2 text-xs text-muted"><Dot level={d.memory.errors ? "red" : "amber"} /> Memory: {d.memory.errors} error(s), {d.memory.warnings} warning(s)</li>
            )}
          </ul>
        )}
      </Async>
    </Card>
  );
}
