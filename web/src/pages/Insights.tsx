import { useState } from "react";
import { Link } from "react-router";
import { AlertTriangle, BellOff, Bot, Building2, Check, Eye, Info, Landmark, ShieldAlert, Sparkles, TrendingUp, Wallet, X } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { useHumanize, useInsights, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CoachText, RefChip, useResolved } from "@/components/CoachText";
import type { CoachInsight, InsightCard } from "@/api/types";

const KIND = { anomaly: "Unusual", price_change: "Price change", forecast: "Forecast", budget: "Budget", subscription: "Subscription", loan: "Loan", rental: "Rental property" } as const;
const ICON = { anomaly: AlertTriangle, price_change: TrendingUp, forecast: Wallet, budget: Info, subscription: Info, loan: Landmark, rental: Building2 } as const;

function CoachCard({ i, act }: { i: CoachInsight; act: (kind: "read" | "done" | "dismiss" | "snooze", id: string) => void }) {
  const q = useResolved(i.evidence);
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-4 text-accent" aria-hidden />
        <h3 className="text-[15px] font-semibold">{i.title}</h3>
        <Badge tone="info">{i.kind === "answer" ? "Answer" : i.kind === "digest" ? "Digest" : "From the coach"}</Badge>
        {i.ai_generated !== false && <Badge tone="neutral" title={i.ai_label ?? "AI-generated content: it can contain mistakes. Check the figures against your accounts."}><Bot className="size-3" aria-hidden /> AI-generated</Badge>}
        {i.status === "new" && <Badge tone="pos">new</Badge>}
        {i.status === "snoozed" && <Badge>snoozed until {fmtDate(i.snoozed_until, "dayMonth")}</Badge>}
        {i.suspicious && <Badge tone="warn" title="Text that looked like an instruction was found in the data while this was written; it was treated as data."><ShieldAlert className="size-3" aria-hidden /> suspicious text seen</Badge>}
        {i.unverified_numbers.length > 0 && <Badge tone="warn" title={`Not found in any computed figure: ${i.unverified_numbers.join(", ")}`}>{i.unverified_numbers.length} number{i.unverified_numbers.length > 1 ? "s" : ""} unverified</Badge>}
      </div>
      {i.compliance_banner && <p className="mt-2 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[13px] text-warn" role="note" data-testid="compliance-banner">{i.compliance_banner}</p>}
      <div className="mt-2 text-sm text-muted"><CoachText text={i.body} /></div>
      {i.evidence.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5" data-testid="evidence"><Eye className="size-3.5 text-faint" aria-hidden /><span className="text-xs text-faint">Evidence</span>{i.evidence.map((r) => <RefChip key={r} id={r} info={q.data?.[r]} />)}</div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-faint">{fmtDate(i.created.slice(0, 10), "dayMonth")} · {i.backend ?? "?"} {i.model ?? ""}{i.skill ? ` · ${i.skill}` : ""}</span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          {i.status === "new" && <Button size="sm" onClick={() => act("read", i.id)}><Eye className="size-3.5" aria-hidden /> Mark read</Button>}
          <Button size="sm" onClick={() => act("snooze", i.id)}><BellOff className="size-3.5" aria-hidden /> Snooze 7 days</Button>
          <Button size="sm" onClick={() => act("done", i.id)}><Check className="size-3.5" aria-hidden /> Done</Button>
          <Button size="sm" onClick={() => act("dismiss", i.id)}><X className="size-3.5" aria-hidden /> Dismiss</Button>
        </span>
      </div>
    </Card>
  );
}

export default function Insights() {
  const q = useInsights();
  const h = useHumanize();
  const [filter, setFilter] = useState<"all" | InsightCard["kind"]>("all");
  const dismiss = useWrite((id: string) => api.post(`/insights/${id}/dismiss`, {}), { success: "Dismissed" });
  const snooze = useWrite((v: { id: string; days: number }) => api.post(`/insights/${v.id}/snooze`, { days: v.days }), { success: "Snoozed" });
  const coachAct = useWrite((v: { kind: "read" | "done" | "dismiss" | "snooze"; id: string }) => api.post(`/insights/${v.id}/${v.kind}`, v.kind === "snooze" ? { days: 7 } : {}), { success: "Updated" });
  return (
    <>
      <PageHeader title="Insights" subtitle="What deserves a look: unusual spending, price rises, accounts that may run short, budgets slipping. Dismissals are remembered." />
      <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
        {(d) => {
          const cards = d.cards.filter((c) => filter === "all" || c.kind === filter);
          return (
            <div className="grid gap-4">
              <Segmented label="Type" value={filter} onChange={setFilter} options={[{ value: "all", label: `All ${d.cards.length}` }, ...(Object.keys(KIND) as (keyof typeof KIND)[]).map((k) => ({ value: k, label: `${KIND[k]} ${d.counts[k] ?? 0}` }))]} />
              {d.coach.items.length === 0 ? (
                <Card className="border-dashed" title="From the coach" subtitle="Analysis written by the coach, each with its evidence">
                  <div className="flex items-start gap-3 text-[13px] text-muted"><Sparkles className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />{d.coach.message}</div>
                </Card>
              ) : (
                <section aria-label="From the coach" className="grid gap-3">
                  <h2 className="text-sm font-semibold text-muted">From the coach</h2>
                  {d.coach.items.map((i) => <CoachCard key={i.id} i={i} act={(kind, id) => coachAct.mutate({ kind, id })} />)}
                </section>
              )}
              {cards.length === 0 ? <Card><EmptyState icon={<Check className="size-6" />} title="Nothing needs your attention">{d.hidden ? `${d.hidden} snoozed or dismissed.` : "New findings appear after each sync."}</EmptyState></Card> : (
                <ul className="grid gap-3">
                  {cards.map((c) => {
                    const Icon = ICON[c.kind];
                    return (
                      <li key={c.id}>
                        <Card>
                          <div className="flex gap-3">
                            <div className={cn("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full", c.severity === "high" ? "bg-neg-soft text-neg" : c.severity === "medium" ? "bg-warn-soft text-warn" : "bg-surface-2 text-muted")}><Icon className="size-4" aria-hidden /></div>
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <h3 className="text-[15px] font-semibold">{h(c.title)}</h3>
                                <Badge tone={c.severity === "high" ? "neg" : c.severity === "medium" ? "warn" : "neutral"}>{c.severity}</Badge>
                                <Badge>{KIND[c.kind]}</Badge>
                                {c.snoozed_until && <Badge>snoozed until {fmtDate(c.snoozed_until, "dayMonth")}</Badge>}
                              </div>
                              <p className="mt-1 text-sm text-muted">{h(c.body)}</p>
                              <div className="mt-3 flex flex-wrap items-center gap-2">
                                {c.evidence.length > 0 && !(c.kind === "loan" && c.subtype.startsWith("loa")) && c.kind !== "rental" && <Link className="text-sm text-accent hover:underline" to={`/transactions?tx=${encodeURIComponent(c.evidence[0])}`}>See the transaction{c.evidence.length > 1 ? "s" : ""}</Link>}
                                {c.kind === "budget" && <Link className="text-sm text-accent hover:underline" to="/budgets">Open budgets</Link>}
                                {c.kind === "loan" && <Link className="text-sm text-accent hover:underline" to="/wealth">Open the loan</Link>}
                                {c.kind === "rental" && <Link className="text-sm text-accent hover:underline" to="/rental">Open the property</Link>}
                                {c.kind === "forecast" && <Link className="text-sm text-accent hover:underline" to="/calendar">Open calendar</Link>}
                                {(c.kind === "price_change" || c.kind === "subscription") && <Link className="text-sm text-accent hover:underline" to="/subscriptions">Open subscriptions</Link>}
                                <span className="ml-auto flex gap-1.5">
                                  <Button size="sm" onClick={() => snooze.mutate({ id: c.id, days: 7 })}><BellOff className="size-3.5" aria-hidden /> Snooze 7 days</Button>
                                  <Button size="sm" onClick={() => dismiss.mutate(c.id)}><Check className="size-3.5" aria-hidden /> Done</Button>
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
              {d.hidden > 0 && <p className="text-center text-xs text-faint">{d.hidden} hidden (snoozed or dismissed)</p>}
            </div>
          );
        }}
      </Async>
    </>
  );
}
