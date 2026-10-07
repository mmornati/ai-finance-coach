import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity } from "lucide-react";
import { Async, Badge, Card, EmptyState, Notice, PageHeader, ProgressBar, Segmented, Skeleton, Stat } from "@/components/ui";
import { ChartFrame } from "@/components/charts";
import { useGet } from "@/api/hooks";
import { fmtDate, fmtNumber } from "@/lib/format";
import type { UsageSummary } from "@/api/types";

function useUsd() {
  const { t } = useTranslation("quality");
  return (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? t("usage.unknown") : `$${v.toFixed(digits)}`);
}
type Period = "7" | "30" | "90";

export default function Usage() {
  const { t } = useTranslation("quality");
  const usd = useUsd();
  const [days, setDays] = useState<Period>("30");
  const q = useGet<UsageSummary>("/usage", { days: Number(days) });
  return (
    <>
      <PageHeader title={t("usage.title")} subtitle={t("usage.subtitle")} actions={<Segmented<Period> label={t("usage.period")} value={days} onChange={setDays} options={(["7", "30", "90"] as const).map((v) => ({ value: v, label: t("usage.days", { count: Number(v) }) }))} />} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => d.totals.calls === 0 ? <Card><EmptyState icon={<Activity className="size-6" />} title={t("usage.emptyTitle")}>{t("usage.emptyBody")}</EmptyState></Card> : (
          <div className="grid gap-4">
            <Card>
              <div className="grid gap-4 sm:grid-cols-4">
                <Stat label={t("usage.calls")} value={fmtNumber(d.totals.calls)} hint={t("usage.tokensHint", { in: fmtNumber(d.totals.tokens_in), out: fmtNumber(d.totals.tokens_out) })} />
                <Stat label={t("usage.cost")} value={usd(d.totals.cost_usd)} hint={t("usage.costHint")} />
                <Stat label={t("usage.estimated")} value={usd(d.totals.estimated_cost_usd)} hint={t("usage.estimatedHint")} />
                <Stat label={t("usage.time")} value={t("usage.seconds", { value: Math.round(d.totals.duration_s) })} hint={d.totals.unknown_cost_calls ? t("usage.unknownCost", { count: d.totals.unknown_cost_calls }) : undefined} />
              </div>
              <Budget m={d.month} />
            </Card>
            <Card title={t("usage.perDay")}>
              <ChartFrame label={t("usage.chart.label")} table={{ head: [t("usage.chart.day"), t("usage.chart.calls"), t("usage.chart.cost")], rows: d.by_day.filter((x) => x.calls > 0).map((x) => [fmtDate(x.date, "dayMonth"), x.calls, usd(x.cost_usd, 4)]) }} legend={[{ color: "var(--s1)", text: t("usage.chart.legend") }]}>
                <div style={{ height: 200 }} role="img" aria-label={t("usage.chart.aria")}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={d.by_day} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
                      <CartesianGrid vertical={false} stroke="var(--grid)" />
                      <XAxis dataKey="date" tickFormatter={(k: string) => k.slice(5)} minTickGap={18} stroke="var(--grid)" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} />
                      <YAxis tickFormatter={(v: number) => `$${v}`} width={44} stroke="var(--grid)" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} />
                      <Tooltip formatter={(v) => usd(Number(v), 4)} labelFormatter={(k) => fmtDate(String(k), "dayMonth")} />
                      <Bar dataKey="cost_usd" fill="var(--s1)" isAnimationActive={false} maxBarSize={24} radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartFrame>
            </Card>
            <Card title={t("usage.perJob")} pad={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-medium">{t("usage.table.job")}</th><th scope="col" className="px-2 py-2 font-medium">{t("usage.table.model")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("usage.table.calls")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("usage.table.tokens")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("usage.table.cache")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("usage.table.cost")}</th><th scope="col" className="px-4 py-2 text-right font-medium">{t("usage.table.avgTime")}</th></tr></thead>
                  <tbody>
                    {d.lines.map((l) => (
                      <tr key={`${l.purpose}|${l.backend}|${l.model}`} className="border-t border-border">
                        <td className="px-4 py-2 font-medium">{l.job}</td>
                        <td className="px-2 py-2">{l.model ?? "?"} <span className="text-xs text-faint">{l.backend}</span></td>
                        <td className="num px-2 py-2 text-right">{l.calls}</td>
                        <td className="num px-2 py-2 text-right">{fmtNumber(l.tokens_in)} / {fmtNumber(l.tokens_out)}</td>
                        <td className="num px-2 py-2 text-right">{fmtNumber(l.cache_read_tokens)} / {fmtNumber(l.cache_write_tokens)}</td>
                        <td className="num px-2 py-2 text-right">{usd(l.cost_usd, 4)} {l.notional && <Badge title={t("usage.notionalTitle")}>{t("usage.notional")}</Badge>}</td>
                        <td className="num px-4 py-2 text-right">{t("usage.seconds", { value: l.avg_duration_s })}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            <Card title={t("usage.where")} subtitle={t("usage.whereHint")}>
              {d.destinations.length === 0 ? <p className="text-sm text-muted">{d.journal_available ? t("usage.nothing") : <Trans t={t} i18nKey="usage.noJournal" components={{ code: <code /> }} />}</p> : (
                <ul className="divide-y divide-border text-sm" aria-label={t("usage.destinations")}>
                  {d.destinations.map((x) => <li key={`${x.kind}|${x.host}|${x.purpose}`} className="flex flex-wrap items-center gap-2 py-2"><span className="min-w-0 flex-1">{x.host || x.kind} <span className="text-xs text-faint">{x.purpose}</span></span><span className="num text-muted">{t("usage.destinationLine", { count: x.calls, bytes: fmtNumber(x.bytes) })}</span>{x.denied > 0 && <Badge tone="warn">{t("usage.refused", { count: x.denied })}</Badge>}</li>)}
                </ul>
              )}
            </Card>
            <p className="text-xs text-faint">{d.note}</p>
          </div>
        )}
      </Async>
    </>
  );
}

function Budget({ m }: { m: UsageSummary["month"] }) {
  const { t } = useTranslation("quality");
  const usd = useUsd();
  if (!m.threshold_usd) return <p className="mt-3 text-xs text-faint"><Trans t={t} i18nKey="usage.budget.noThreshold" values={{ month: m.month, cost: usd(m.cost_usd) }} components={{ code: <code /> }} /></p>;
  return (
    <div className="mt-4">
      <div className="mb-1 flex justify-between text-xs text-muted"><span>{t("usage.budget.ofThreshold", { month: m.month, cost: usd(m.cost_usd), threshold: usd(m.threshold_usd) })}</span><span>{Math.round((m.ratio ?? 0) * 100)} %</span></div>
      <ProgressBar label={t("usage.budget.progress")} value={m.cost_usd} max={m.threshold_usd} tone={m.level === "high" ? "neg" : m.level === "medium" ? "warn" : "info"} />
      {m.level && <Notice tone="warn" className="mt-3">{t("usage.budget.over")}</Notice>}
    </div>
  );
}
