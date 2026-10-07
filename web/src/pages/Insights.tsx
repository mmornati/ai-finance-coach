import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { AlertTriangle, BellOff, Bot, Building2, Check, Eye, Info, Landmark, ShieldAlert, Sparkles, TrendingUp, Wallet, X } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { useDisclaimers, useHumanize, useInsights, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { cardText } from "@/i18n/server";
import { CoachText, RefChip, useResolved } from "@/components/CoachText";
import type { CoachInsight, InsightCard } from "@/api/types";

const KINDS = ["anomaly", "price_change", "forecast", "budget", "subscription", "loan", "rental"] as const;
const ICON = { anomaly: AlertTriangle, price_change: TrendingUp, forecast: Wallet, budget: Info, subscription: Info, loan: Landmark, rental: Building2 } as const;

function CoachCard({ i, act }: { i: CoachInsight; act: (kind: "read" | "done" | "dismiss" | "snooze", id: string) => void }) {
  const q = useResolved(i.evidence);
  const { t } = useTranslation("insights");
  // the EU AI Act label (E11-5): the server sends it in the answer's language; without it, the interface language's (GET /meta/disclaimers).
  // Its wording lives only in src/coach/disclaimers.py.
  const disclaimers = useDisclaimers();
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-4 text-accent" aria-hidden />
        <h3 className="text-[15px] font-semibold">{i.title}</h3>
        <Badge tone="info">{i.kind === "answer" ? t("coach.answer") : i.kind === "digest" ? t("coach.digest") : t("coach.title")}</Badge>
        {i.ai_generated !== false && <Badge tone="neutral" title={i.ai_label ?? disclaimers?.ai_label}><Bot className="size-3" aria-hidden /> {i.ai_label_short ?? disclaimers?.ai_label_short}</Badge>}
        {i.status === "new" && <Badge tone="pos">{t("coach.new")}</Badge>}
        {i.status === "snoozed" && <Badge>{t("coach.snoozedUntil", { date: fmtDate(i.snoozed_until, "dayMonth") })}</Badge>}
        {i.suspicious && <Badge tone="warn" title={t("coach.suspiciousTitle")}><ShieldAlert className="size-3" aria-hidden /> {t("coach.suspicious")}</Badge>}
        {i.unverified_numbers.length > 0 && <Badge tone="warn" title={t("coach.unverifiedTitle", { list: i.unverified_numbers.join(", ") })}>{t("coach.unverified", { count: i.unverified_numbers.length })}</Badge>}
      </div>
      {i.compliance_banner && <p className="mt-2 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[13px] text-warn" role="note" data-testid="compliance-banner">{i.compliance_banner}</p>}
      <div className="mt-2 text-sm text-muted"><CoachText text={i.body} /></div>
      {i.evidence.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5" data-testid="evidence"><Eye className="size-3.5 text-faint" aria-hidden /><span className="text-xs text-faint">{t("coach.evidence")}</span>{i.evidence.map((r) => <RefChip key={r} id={r} info={q.data?.[r]} />)}</div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-faint">{fmtDate(i.created.slice(0, 10), "dayMonth")} · {i.backend ?? "?"} {i.model ?? ""}{i.skill ? ` · ${i.skill}` : ""}</span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          {i.status === "new" && <Button size="sm" onClick={() => act("read", i.id)}><Eye className="size-3.5" aria-hidden /> {t("coach.markRead")}</Button>}
          <Button size="sm" onClick={() => act("snooze", i.id)}><BellOff className="size-3.5" aria-hidden /> {t("coach.snooze")}</Button>
          <Button size="sm" onClick={() => act("done", i.id)}><Check className="size-3.5" aria-hidden /> {t("coach.done")}</Button>
          <Button size="sm" onClick={() => act("dismiss", i.id)}><X className="size-3.5" aria-hidden /> {t("coach.dismiss")}</Button>
        </span>
      </div>
    </Card>
  );
}

export default function Insights() {
  const q = useInsights();
  const h = useHumanize();
  const disclaimers = useDisclaimers();
  const [filter, setFilter] = useState<"all" | InsightCard["kind"]>("all");
  const { t } = useTranslation("insights");
  const dismiss = useWrite((id: string) => api.post(`/insights/${id}/dismiss`, {}), { success: t("dismissed") });
  const snooze = useWrite((v: { id: string; days: number }) => api.post(`/insights/${v.id}/snooze`, { days: v.days }), { success: t("snoozed") });
  const coachAct = useWrite((v: { kind: "read" | "done" | "dismiss" | "snooze"; id: string }) => api.post(`/insights/${v.id}/${v.kind}`, v.kind === "snooze" ? { days: 7 } : {}), { success: t("updated") });
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
        {(d) => {
          const cards = d.cards.filter((c) => filter === "all" || c.kind === filter);
          return (
            <div className="grid gap-4">
              <Segmented label={t("type")} value={filter} onChange={setFilter} options={[{ value: "all", label: t("filterAll", { n: d.cards.length }) }, ...KINDS.map((k) => ({ value: k, label: t("filterKind", { kind: t(`kind.${k}`), n: d.counts[k] ?? 0 }) }))]} />
              {d.coach.items.length === 0 ? (
                <Card className="border-dashed" title={t("coach.title")} subtitle={t("coach.subtitle")}>
                  <div className="flex items-start gap-3 text-[13px] text-muted"><Sparkles className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />{d.coach.message}</div>
                </Card>
              ) : (
                <section aria-label={t("coach.title")} className="grid gap-3">
                  <h2 className="text-sm font-semibold text-muted">{t("coach.title")}</h2>
                  {d.coach.items.map((i) => <CoachCard key={i.id} i={i} act={(kind, id) => coachAct.mutate({ kind, id })} />)}
                </section>
              )}
              {cards.length === 0 ? <Card><EmptyState icon={<Check className="size-6" />} title={t("emptyTitle")}>{d.hidden ? t("hiddenCount", { n: d.hidden }) : t("afterSync")}</EmptyState></Card> : (
                <ul className="grid gap-3">
                  {cards.map((c) => {
                    const Icon = ICON[c.kind];
                    // the card's messages in the interface language; humanize() only for an older, English-only text
                    const txt = cardText(c, { legacy: h, disclaimer: c.disclaimer, disclaimers });
                    return (
                      <li key={c.id}>
                        <Card>
                          <div className="flex gap-3">
                            <div className={cn("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full", c.severity === "high" ? "bg-neg-soft text-neg" : c.severity === "medium" ? "bg-warn-soft text-warn" : "bg-surface-2 text-muted")}><Icon className="size-4" aria-hidden /></div>
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <h3 className="text-[15px] font-semibold">{txt.title}</h3>
                                <Badge tone={c.severity === "high" ? "neg" : c.severity === "medium" ? "warn" : "neutral"}>{t(`severity.${c.severity}`)}</Badge>
                                <Badge>{t(`kind.${c.kind}`)}</Badge>
                                {c.snoozed_until && <Badge>{t("coach.snoozedUntil", { date: fmtDate(c.snoozed_until, "dayMonth") })}</Badge>}
                              </div>
                              <p className="mt-1 text-sm text-muted">{txt.body}</p>
                              <div className="mt-3 flex flex-wrap items-center gap-2">
                                {c.evidence.length > 0 && !(c.kind === "loan" && c.subtype.startsWith("loa")) && c.kind !== "rental" && <Link className="text-sm text-accent hover:underline" to={`/transactions?tx=${encodeURIComponent(c.evidence[0])}`}>{t("seeTransactions", { count: c.evidence.length })}</Link>}
                                {c.kind === "budget" && <Link className="text-sm text-accent hover:underline" to="/budgets">{t("openBudgets")}</Link>}
                                {c.kind === "loan" && <Link className="text-sm text-accent hover:underline" to="/wealth">{t("openLoan")}</Link>}
                                {c.kind === "rental" && <Link className="text-sm text-accent hover:underline" to="/rental">{t("openProperty")}</Link>}
                                {c.kind === "forecast" && <Link className="text-sm text-accent hover:underline" to="/calendar">{t("openCalendar")}</Link>}
                                {(c.kind === "price_change" || c.kind === "subscription") && <Link className="text-sm text-accent hover:underline" to="/subscriptions">{t("openSubscriptions")}</Link>}
                                <span className="ml-auto flex gap-1.5">
                                  <Button size="sm" onClick={() => snooze.mutate({ id: c.id, days: 7 })}><BellOff className="size-3.5" aria-hidden /> {t("snooze")}</Button>
                                  <Button size="sm" onClick={() => dismiss.mutate(c.id)}><Check className="size-3.5" aria-hidden /> {t("done")}</Button>
                                </span>
                              </div>
                            </div>
                          </div>
                        </Card>
                      </li>
                    );
                  })}
                </ul>
              )}
              {d.hidden > 0 && <p className="text-center text-xs text-faint">{t("hidden", { n: d.hidden })}</p>}
            </div>
          );
        }}
      </Async>
    </>
  );
}
