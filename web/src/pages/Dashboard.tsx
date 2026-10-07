import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import { AlertTriangle, ArrowRight, Bell, CalendarClock, CircleHelp, Lightbulb } from "lucide-react";
import { Async, Badge, Card, Dot, EmptyState, Money, Notice, PageHeader, ProgressBar, Skeleton, Stat } from "@/components/ui";
import { CashflowChart, ForecastChart, ShareBar } from "@/components/charts";
import { useAlerts, useBalances, useBudgets, useCashflow, useForecast, useGet, useHealth, useHumanize, useInsights, useMonthCategories, useQuestions, useScoped } from "@/api/hooks";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, fmtRelativeDays, parseMoney } from "@/lib/format";
import { forecastLabel, serverLabel, tServer } from "@/i18n/server";
import type { CalendarResult, SavingsView } from "@/api/types";
import { cn } from "@/lib/utils";

export default function Dashboard() {
  const { t } = useTranslation("dashboard");
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("balances.title")} action={<Link to="/wealth" className="text-xs text-accent hover:underline">{t("balances.netWorth")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => (
          <>
            <Stat big label={t("balances.total")} value={<Money v={d.household_total} />} hint={d.n_without_balance ? t("balances.withoutBalance", { count: d.n_without_balance }) : undefined} />
            <ul className="mt-3 divide-y divide-border text-sm">
              {d.accounts.slice(0, 6).map((a) => (
                <li key={a.uid} className="flex items-center justify-between gap-3 py-1.5">
                  <span className="min-w-0 truncate">
                    {a.label}
                    {a.stale && <Badge tone="warn" className="ml-2" title={t("balances.oldTitle", { count: a.age_days })}>{t("balances.old")}</Badge>}
                    {a.balance !== null && !a.booked && <Badge className="ml-2" title={t("balances.typeTitle")}>{serverLabel("balanceType", a.balance_type, a.balance_type_label)}</Badge>}
                  </span>
                  <Money v={a.balance} colored className="font-medium" />
                </li>
              ))}
            </ul>
            {d.accounts.length > 6 && <p className="mt-2 text-xs text-faint">{t("balances.more", { count: d.accounts.length - 6 })}</p>}
            {d.mixed_types && <p className="mt-2 text-xs text-faint">{tServer(d.note_msg, d.note)}</p>}
          </>
        )}
      </Async>
    </Card>
  );
}

function ThisMonth() {
  const q = useMonthCategories();
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("month.title")}>
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
                  <ProgressBar label={t("month.progress")} value={spent} max={Math.max(avg, spent)} marker={(Math.min(frac, 1) * Math.min(avg, Math.max(avg, spent)) / Math.max(avg, spent)) * 100} tone={spent > avg ? "neg" : "info"} />
                  <p className="mt-2 text-[13px] text-muted">
                    <Trans t={t} i18nKey="month.usual" count={d.household_avg_months} components={{ amount: <Money v={d.household_monthly_avg} className="font-medium text-text" /> }} />
                  </p>
                </div>
              ) : (
                <p className="mt-3 text-[13px] text-muted">{t("month.noAverage")}</p>
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
  const { t: tr } = useTranslation("dashboard");
  return (
    <Card title={tr("savings.title")} subtitle={tr("savings.subtitle")}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          const t = d.household.totals_complete;
          if (!t) return <EmptyState title={tr("savings.emptyTitle")}>{tr("savings.emptyBody")}</EmptyState>;
          return (
            <>
              <Stat big label={tr("savings.over", { count: t.n_months })} value={fmtPct(t.savings_rate, 1)} tone={(t.savings_rate ?? 0) < 0 ? "neg" : undefined} hint={<Trans t={tr} i18nKey="savings.netOfIncome" components={{ net: <Money v={t.net} signed colored />, income: <Money v={t.income} round /> }} />} />
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
                <dt className="text-muted">{tr("savings.saved")}</dt>
                <dd className="text-right"><Money v={t.saved} /></dd>
                <dt className="text-muted">{tr("savings.debtService")}</dt>
                <dd className="text-right"><Money v={t.debt_service} /></dd>
              </dl>
              <p className="mt-2 text-xs text-faint">
                {t.loan_principal !== null && t.savings_rate_incl_principal !== null
                  ? <Trans t={tr} i18nKey="savings.principal" values={{ rate: fmtPct(t.savings_rate_incl_principal, 1) }} components={{ amount: <Money v={t.loan_principal} /> }} />
                  : t.debt_service && parseMoney(t.debt_service) ? tr("savings.principalUnknown") : null}
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("cashflow.title")} subtitle={t("cashflow.subtitle")}>
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("forecast.title")} action={<Link to="/calendar" className="text-xs text-accent hover:underline">{t("forecast.calendar")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-52 w-full" />}>
        {(d) => {
          const risky = [d.household, ...d.accounts].filter((a) => a.first_negative || a.first_at_risk);
          return (
            <>
              {d.household.points.length > 0 ? <ForecastChart f={d.household} height={190} /> : <EmptyState title={t("forecast.emptyTitle")}>{t("forecast.emptyBody")}</EmptyState>}
              <p className="mt-2 text-[13px] text-muted">
                <Trans t={t} i18nKey="forecast.lowest" values={{ date: fmtDate(d.household.min_date, "dayMonth") }} components={{ amount: <Money v={d.household.min_balance} className="font-medium text-text" /> }} />
              </p>
              {risky.length > 0 ? (
                <ul className="mt-2 grid gap-1.5">
                  {risky.slice(0, 3).map((a) => (
                    <li key={a.label}>
                      <Notice tone={a.first_negative ? "neg" : "warn"}>
                        <b>{forecastLabel(a)}</b>: {a.first_negative ? t("forecast.belowZero", { date: fmtDate(a.first_negative, "dayMonth") }) : t("forecast.short", { date: fmtDate(a.first_at_risk, "dayMonth") })}
                      </Notice>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 flex items-center gap-1.5 text-[13px] text-pos"><Dot level="green" /> {t("forecast.allGood")}</p>
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("top.title")} subtitle={t("top.subtitle")} action={<Link to="/categories" className="text-xs text-accent hover:underline">{t("top.all")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-52 w-full" />}>
        {(d) => {
          const rows = d.categories.slice(0, 8);
          if (!rows.length) return <EmptyState title={t("top.empty")} />;
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
                        {avg !== null && <span className="ml-2 text-xs text-faint">{t("top.avg", { amount: fmtMoney(r.monthly_avg, { round: true }) })}</span>}
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("budgets.title")} action={<Link to="/budgets" className="text-xs text-accent hover:underline">{t("budgets.manage")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          if (!d.budgets.length) return <EmptyState title={t("budgets.emptyTitle")}>{t("budgets.emptyBody")}</EmptyState>;
          const order = { over: 0, at_risk: 1, ok: 2 } as const;
          const rows = [...d.budgets].sort((a, b) => order[a.status] - order[b.status] || (b.percent_used ?? 0) - (a.percent_used ?? 0)).slice(0, 5);
          return (
            <ul className="grid gap-3">
              {rows.map((b) => (
                <li key={b.id}>
                  <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                    <span className="min-w-0 truncate">{b.category ? catLabel(b.category) : t("budgets.allOf", { group: b.group })}</span>
                    <span className="num text-xs text-muted"><Money v={b.spent} round /> / <Money v={b.available} round /></span>
                  </div>
                  <ProgressBar label={t("budgets.used", { target: b.target })} value={parseMoney(b.spent) ?? 0} max={parseMoney(b.available) ?? 1} tone={b.status === "over" ? "neg" : b.status === "at_risk" ? "warn" : "info"} />
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("upcoming.title")} action={<Link to="/calendar" className="text-xs text-accent hover:underline">{t("upcoming.calendar")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          const items = d.items.filter((i) => i.source !== "asset").slice(0, 7);
          if (!items.length) return <EmptyState icon={<CalendarClock className="size-6" />} title={t("upcoming.empty")} />;
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("subsSavings.title")} action={<Link to="/subscriptions" className="text-xs text-accent hover:underline">{t("subsSavings.link")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-24 w-full" />}>
        {(d) => {
          const none = d.verified + d.pending + d.contradicted === 0;
          if (none && !d.proposed.length) return <EmptyState title={t("subsSavings.emptyTitle")}>{t("subsSavings.emptyBody")}</EmptyState>;
          return (
            <>
              <Stat big label={t("subsSavings.confirmedMonthly")} value={<Money v={d.realised_monthly} />} tone={Number(d.realised_monthly) > 0 ? "pos" : undefined} hint={<Trans t={t} i18nKey="subsSavings.hint" components={{ since: <Money v={d.realised_since_decisions} />, yearly: <Money v={d.realised_yearly_run_rate} round /> }} />} />
              <p className="mt-2 text-[13px] text-muted">
                {t("subsSavings.confirmed", { count: d.verified })}{d.pending ? t("subsSavings.pending", { count: d.pending }) : ""}{d.contradicted ? t("subsSavings.contradicted", { count: d.contradicted }) : ""}.
                {d.pending > 0 && <Trans t={t} i18nKey="subsSavings.claimed" components={{ amount: <Money v={d.claimed_monthly_unverified} /> }} />}
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("alerts.title")} action={<Link to="/alerts" className="text-xs text-accent hover:underline">{t("alerts.open")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-20 w-full" />}>
        {(d) => {
          const open = d.items.filter((e) => e.status === "new" || e.status === "sent");
          if (!d.ready || !open.length) return <EmptyState icon={<Bell className="size-6" />} title={t("alerts.emptyTitle")}>{t("alerts.emptyBody")}</EmptyState>;
          const high = open.filter((e) => e.severity === "high").length;
          return (
            <Link to="/alerts" className="group block">
              <div className="flex items-center gap-2">
                <Badge tone={high ? "neg" : "warn"}>{t("alerts.openCount", { count: open.length })}</Badge>
                {high > 0 && <Badge tone="neg">{t("alerts.highCount", { count: high })}</Badge>}
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("insights.title")} action={<Link to="/insights" className="text-xs text-accent hover:underline">{t("insights.all")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-36 w-full" />}>
        {(d) => {
          if (!d.cards.length) return <EmptyState icon={<Lightbulb className="size-6" />} title={t("insights.emptyTitle")}>{t("insights.emptyBody")}</EmptyState>;
          return (
            <ul className="grid gap-2.5">
              {d.cards.slice(0, 4).map((c) => (
                <li key={c.id} className="flex gap-2.5 text-sm">
                  <AlertTriangle className={cn("mt-0.5 size-4 shrink-0", c.severity === "high" ? "text-neg" : c.severity === "medium" ? "text-warn" : "text-faint")} aria-label={t(`insights.severity.${c.severity}`)} />
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
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("questions.title")}>
      <Async q={q} skeleton={<Skeleton className="h-16 w-full" />}>
        {(d) => {
          const n = d.counts.open ?? 0;
          return n ? (
            <Link to="/memory" className="group flex items-center gap-3">
              <CircleHelp className="size-8 text-accent" aria-hidden />
              <div className="flex-1">
                <div className="text-xl font-semibold num">{n}</div>
                <div className="text-[13px] text-muted">{t("questions.open", { count: n })}</div>
              </div>
              <ArrowRight className="size-4 text-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
            </Link>
          ) : (
            <p className="text-[13px] text-muted">{t("questions.none")}</p>
          );
        }}
      </Async>
    </Card>
  );
}

function HealthCard() {
  const q = useHealth();
  const { t } = useTranslation("dashboard");
  return (
    <Card title={t("health.title")} action={<Link to="/connections" className="text-xs text-accent hover:underline">{t("health.details")}</Link>}>
      <Async q={q} skeleton={<Skeleton className="h-20 w-full" />}>
        {(d) => (
          <ul className="grid gap-1.5 text-sm">
            {d.banks.map((b) => (
              <li key={`${b.bank}${b.session_id}`} className="flex items-center gap-2">
                <Dot level={b.level} />
                <span className="min-w-0 flex-1 truncate">{b.bank}</span>
                <span className="text-xs text-muted">{b.consent_days_left !== null ? t("health.daysLeft", { days: b.consent_days_left }) : b.consent_status ?? t("health.manual")}</span>
              </li>
            ))}
            {d.memory && (d.memory.errors > 0 || d.memory.warnings > 0) && (
              <li className="flex items-center gap-2 text-xs text-muted"><Dot level={d.memory.errors ? "red" : "amber"} /> {t("health.memory", { errors: d.memory.errors, warnings: d.memory.warnings })}</li>
            )}
          </ul>
        )}
      </Async>
    </Card>
  );
}
