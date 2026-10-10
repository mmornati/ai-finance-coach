import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { PiggyBank } from "lucide-react";
import { Async, Badge, Card, EmptyState, Money, Notice, ProgressBar, Skeleton, Stat } from "@/components/ui";
import { ShareBar, Sparkline } from "@/components/charts";
import { SignOutButton } from "@/components/Layout";
import { PasskeysCard } from "@/components/Passkeys";
import { useMeSummary, useMeTransactions } from "@/api/hooks";
import { catLabel, fmtDate, fmtMoney, fmtMonth, parseMoney } from "@/lib/format";
import type { KidBudgetStatus } from "@/api/types";

// the rhythm codes the server sends for a pocket-money series (an unknown code is shown as sent)
const CADENCE: Record<string, ParseKeys<"kids">> = { weekly: "kids.cadence.weekly", fortnightly: "kids.cadence.fortnightly", monthly: "kids.cadence.monthly" };
const STATUS: Record<KidBudgetStatus["status"], ParseKeys<"kids">> = { over: "kids.status.over", at_risk: "kids.status.close", ok: "kids.status.ok" };

/** What a child login sees: their own money only (read-only). Nothing about the household, the other members, the accounts or the memory. */
export default function KidHome() {
  const { t } = useTranslation("kids");
  const q = useMeSummary();
  const tx = useMeTransactions(30);
  return (
    <div className="mx-auto min-h-dvh w-full max-w-3xl px-4 py-5 sm:px-6">
      <header className="mb-5 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="size-7 rounded-md" />
          <h1 className="text-lg font-semibold tracking-tight">{t("kidHome.title")}</h1>
        </div>
        <SignOutButton label={t("kidHome.signOut")} />
      </header>
      <Async q={q} skeleton={<Skeleton className="h-72 w-full" />}>
        {(d) => {
          const r = d.report;
          const trend = r.balance.trend.map((p) => parseMoney(p.end_balance) ?? 0);
          const maxCat = Math.max(1, ...r.spending.by_category.map((c) => parseMoney(c.total) ?? 0));
          return (
            <div className="grid gap-4">
              <p className="text-sm text-muted">{t("kidHome.greeting", { name: d.name.split(" ")[0] })}</p>
              <div className="grid grid-cols-2 gap-3">
                <Stat
                  label={t("kidHome.balance")}
                  value={r.balance.current ? fmtMoney(r.balance.current) : t("kidHome.unknown")}
                  hint={r.balance.as_of ? t("kidHome.asOf", { date: fmtDate(r.balance.as_of, "dayMonth") }) : undefined}
                  big
                />
                <Stat label={t("kidHome.spent")} value={fmtMoney(r.spending.this_month_to_date)} hint={t("kidHome.spentHint", { amount: fmtMoney(r.spending.monthly_avg) })} />
              </div>
              {d.budgets.map((b) => (
                <Budget key={b.id} b={b} />
              ))}
              <Card title={t("kidHome.pocket.title")}>
                {r.pocket_money.series.length === 0 ? (
                  <p className="text-[13px] text-muted">{t("kidHome.pocket.empty")}</p>
                ) : (
                  <ul className="grid gap-1 text-sm">
                    {r.pocket_money.series.map((s) => (
                      <li key={s.cadence + s.amount}>
                        <Trans
                          t={t}
                          i18nKey="kidHome.pocket.series"
                          values={{ cadence: CADENCE[s.cadence] ? t(CADENCE[s.cadence]) : s.cadence, source: s.source, date: fmtDate(s.next_expected, "dayMonth") }}
                          components={{ b: <b />, amount: <Money v={s.amount} /> }}
                        />
                      </li>
                    ))}
                  </ul>
                )}
                {r.extra_topups.count > 0 && (
                  <p className="mt-2 text-[13px] text-muted">
                    {t("kidHome.pocket.extra", {
                      total: fmtMoney(r.extra_topups.total),
                      sources: Object.entries(r.extra_topups.by_source)
                        .map(([k, v]) => t("kidHome.pocket.fromSource", { amount: fmtMoney(v), source: k }))
                        .join(", "),
                    })}
                  </p>
                )}
              </Card>
              <Card title={t("kidHome.spending.title")}>
                {r.spending.by_category.length === 0 ? (
                  <p className="text-[13px] text-muted">{t("kidHome.spending.empty")}</p>
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
                <Card title={t("kidHome.trend.title")} subtitle={t("kidHome.trend.subtitle")}>
                  <Sparkline values={trend} height={64} label={t("kidHome.trend.label")} />
                </Card>
              )}
            </div>
          );
        }}
      </Async>
      <div className="mt-4">
        <Async q={tx} skeleton={<Skeleton className="h-40 w-full" />}>
          {(p) => (
            <Card title={t("kidHome.payments.title")} pad={false}>
              {p.items.length === 0 ? (
                <EmptyState icon={<PiggyBank className="size-6" />} title={t("kidHome.payments.empty")}>{t("kidHome.payments.emptyBody")}</EmptyState>
              ) : (
                <ul className="divide-y divide-border">
                  {p.items.map((i, k) => (
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
      <div className="mt-4">
        <PasskeysCard />
      </div>
    </div>
  );
}

function Budget({ b }: { b: KidBudgetStatus }) {
  const { t } = useTranslation("kids");
  const left = fmtMoney(b.remaining);
  const msg =
    b.status === "over"
      ? t("kidHome.budget.over")
      : b.status === "at_risk"
        ? t("kidHome.budget.close", { left, count: b.days_left })
        : t("kidHome.budget.ok", { left, count: b.days_left });
  return (
    <Card
      title={b.period === "weekly" ? t("kidHome.budget.weekly") : t("kidHome.budget.monthly")}
      action={<Badge tone={b.status === "over" ? "warn" : b.status === "at_risk" ? "warn" : "pos"}>{t(STATUS[b.status] ?? "kids.status.ok")}</Badge>}
    >
      <div className="mb-2 flex items-baseline justify-between text-sm">
        <span>
          <Trans t={t} i18nKey="kidHome.budget.spentOf" components={{ spent: <Money v={b.spent} />, limit: <Money v={b.limit} /> }} />
        </span>
      </div>
      <ProgressBar label={t("kidHome.budget.used")} value={Math.min(b.ratio, 1.2) * 100} max={120} marker={(100 / 120) * 100} tone={b.status === "ok" ? "info" : "warn"} />
      <Notice tone={b.status === "ok" ? "info" : "warn"} className="mt-3">{msg}</Notice>
    </Card>
  );
}
