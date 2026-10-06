import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity } from "lucide-react";
import { Async, Badge, Card, EmptyState, Notice, PageHeader, ProgressBar, Segmented, Skeleton, Stat } from "@/components/ui";
import { ChartFrame } from "@/components/charts";
import { useGet } from "@/api/hooks";
import { fmtDate, fmtNumber } from "@/lib/format";
import type { UsageSummary } from "@/api/types";

const usd = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? "unknown" : `$${v.toFixed(digits)}`);
type Period = "7" | "30" | "90";

export default function Usage() {
  const [days, setDays] = useState<Period>("30");
  const q = useGet<UsageSummary>("/usage", { days: Number(days) });
  return (
    <>
      <PageHeader title="AI usage" subtitle="What the AI calls of this app cost, which job made them and where they went." actions={<Segmented<Period> label="Period" value={days} onChange={setDays} options={[{ value: "7", label: "7 days" }, { value: "30", label: "30 days" }, { value: "90", label: "90 days" }]} />} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => d.totals.calls === 0 ? <Card><EmptyState icon={<Activity className="size-6" />} title="No AI call in this period">Calls made by the classifier, the coach and the evaluations show up here.</EmptyState></Card> : (
          <div className="grid gap-4">
            <Card>
              <div className="grid gap-4 sm:grid-cols-4">
                <Stat label="Calls" value={fmtNumber(d.totals.calls)} hint={`${fmtNumber(d.totals.tokens_in)} tokens in, ${fmtNumber(d.totals.tokens_out)} out`} />
                <Stat label="Cost" value={usd(d.totals.cost_usd)} hint="notional on a subscription" />
                <Stat label="Of which estimated at API prices" value={usd(d.totals.estimated_cost_usd)} hint="what an API key would be billed" />
                <Stat label="Time spent" value={`${Math.round(d.totals.duration_s)} s`} hint={d.totals.unknown_cost_calls ? `${d.totals.unknown_cost_calls} call(s) with no known price` : undefined} />
              </div>
              <Budget m={d.month} />
            </Card>
            <Card title="Per day">
              <ChartFrame label="Cost per day" table={{ head: ["Day", "Calls", "Cost"], rows: d.by_day.filter((x) => x.calls > 0).map((x) => [fmtDate(x.date, "dayMonth"), x.calls, usd(x.cost_usd, 4)]) }} legend={[{ color: "var(--s1)", text: "Cost (USD)" }]}>
                <div style={{ height: 200 }} role="img" aria-label="Cost per day; open the table view for the numbers">
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
            <Card title="Per job and model" pad={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-medium">Job</th><th scope="col" className="px-2 py-2 font-medium">Model</th><th scope="col" className="px-2 py-2 text-right font-medium">Calls</th><th scope="col" className="px-2 py-2 text-right font-medium">Tokens in / out</th><th scope="col" className="px-2 py-2 text-right font-medium">Cache read / written</th><th scope="col" className="px-2 py-2 text-right font-medium">Cost</th><th scope="col" className="px-4 py-2 text-right font-medium">Avg time</th></tr></thead>
                  <tbody>
                    {d.lines.map((l) => (
                      <tr key={`${l.purpose}|${l.backend}|${l.model}`} className="border-t border-border">
                        <td className="px-4 py-2 font-medium">{l.job}</td>
                        <td className="px-2 py-2">{l.model ?? "?"} <span className="text-xs text-faint">{l.backend}</span></td>
                        <td className="num px-2 py-2 text-right">{l.calls}</td>
                        <td className="num px-2 py-2 text-right">{fmtNumber(l.tokens_in)} / {fmtNumber(l.tokens_out)}</td>
                        <td className="num px-2 py-2 text-right">{fmtNumber(l.cache_read_tokens)} / {fmtNumber(l.cache_write_tokens)}</td>
                        <td className="num px-2 py-2 text-right">{usd(l.cost_usd, 4)} {l.notional && <Badge title="A subscription call: nothing is billed for it">notional</Badge>}</td>
                        <td className="num px-4 py-2 text-right">{l.avg_duration_s} s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            <Card title="Where the calls went" subtitle="From the local egress journal: the host and the size, never what was sent.">
              {d.destinations.length === 0 ? <p className="text-sm text-muted">{d.journal_available ? "Nothing recorded in this period." : "The egress journal is not available yet (run `coach db migrate`)."}</p> : (
                <ul className="divide-y divide-border text-sm" aria-label="Destinations">
                  {d.destinations.map((x) => <li key={`${x.kind}|${x.host}|${x.purpose}`} className="flex flex-wrap items-center gap-2 py-2"><span className="min-w-0 flex-1">{x.host || x.kind} <span className="text-xs text-faint">{x.purpose}</span></span><span className="num text-muted">{x.calls} call{x.calls > 1 ? "s" : ""} · {fmtNumber(x.bytes)} bytes</span>{x.denied > 0 && <Badge tone="warn">{x.denied} refused</Badge>}</li>)}
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
  if (!m.threshold_usd) return <p className="mt-3 text-xs text-faint">{m.month}: {usd(m.cost_usd)} so far. Set <code>[usage] monthly_warn_usd</code> in the configuration file to be warned when a month costs more than you want.</p>;
  return (
    <div className="mt-4">
      <div className="mb-1 flex justify-between text-xs text-muted"><span>{m.month}: {usd(m.cost_usd)} of {usd(m.threshold_usd)}</span><span>{Math.round((m.ratio ?? 0) * 100)} %</span></div>
      <ProgressBar label="This month against your threshold" value={m.cost_usd} max={m.threshold_usd} tone={m.level === "high" ? "neg" : m.level === "medium" ? "warn" : "info"} />
      {m.level && <Notice tone="warn" className="mt-3">This month is over the threshold you set. An alert is in the Alerts page (it is never sent anywhere else).</Notice>}
    </div>
  );
}
