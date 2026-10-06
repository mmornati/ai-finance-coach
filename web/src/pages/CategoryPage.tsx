import { Link, useNavigate, useParams } from "react-router";
import { ArrowDownRight, ArrowLeft, ArrowUpRight } from "lucide-react";
import { Async, Badge, Card, EmptyState, Money, Notice, PageHeader, Skeleton, Stat } from "@/components/ui";
import { MonthlyBars, ShareBar } from "@/components/charts";
import { useScoped } from "@/api/hooks";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney } from "@/lib/format";
import type { CategoryDetail } from "@/api/types";

export default function CategoryPage() {
  const { id = "" } = useParams();
  const q = useScoped<CategoryDetail>(`/categories/${id}`);
  const nav = useNavigate();
  return (
    <>
      <Link to="/categories" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-text"><ArrowLeft className="size-4" aria-hidden /> Categories</Link>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          const avg = d.average ? parseMoney(d.average.monthly) : null;
          const maxE = Math.max(...d.entities.map((e) => parseMoney(e.total) ?? 0), 1);
          return (
            <>
              <PageHeader title={d.is_group ? groupLabel(d.id) : catLabel(d.id)} subtitle={d.description ?? (d.is_group ? `All ${groupLabel(d.id).toLowerCase()} categories` : undefined)} actions={d.is_group ? undefined : <Link className="text-sm text-accent hover:underline" to={`/transactions?category=${d.id}`}>See transactions</Link>} />
              {d.is_group && <div className="mb-4 flex flex-wrap gap-2">{d.leaves.map((l) => <Link key={l.id} to={`/categories/${l.id}`}><Badge tone="info">{catLabel(l.id)}</Badge></Link>)}</div>}
              <div className="grid gap-4 lg:grid-cols-3">
                <Card className="lg:col-span-2" title="Month by month" subtitle="Lighter bars: months not fully covered by the accounts that carry this category. Dashed bars: month in progress.">
                  {d.series.length ? <MonthlyBars series={d.series} average={avg} /> : <EmptyState title="No transactions in this category" />}
                </Card>
                <div className="grid content-start gap-4">
                  <Card title="A usual month">
                    {d.average ? (
                      <>
                        <Stat big label="Without one-offs" value={fmtMoney(d.average.monthly)} hint={`${d.average.n_months} fully covered months`} />
                        <p className="mt-2 text-[13px] text-muted">With one-offs: <Money v={d.average.monthly_with_one_offs} /></p>
                        {d.average.accounts.length > 0 && <p className="mt-1 text-xs text-faint">Based on {d.average.accounts.join(", ")}.</p>}
                        {d.average.low_confidence && <Notice tone="warn" className="mt-2">Low confidence: only {d.average.n_months} covered month(s).</Notice>}
                      </>
                    ) : (
                      <p className="text-[13px] text-muted">{d.kind === "spending" ? "No month is fully covered yet, so there is no fair average." : "Averages are computed for spending categories."}</p>
                    )}
                  </Card>
                  <Card title="Trend">
                    {d.trend ? (
                      <>
                        <div className="flex items-center gap-2 text-xl font-semibold num">
                          {(parseMoney(d.trend.delta) ?? 0) > 0 ? <ArrowUpRight className="size-5 text-neg" aria-hidden /> : <ArrowDownRight className="size-5 text-pos" aria-hidden />}
                          {d.trend.pct !== null ? fmtPct(d.trend.pct, 0, { signed: true }) : fmtMoney(d.trend.delta, { signed: true })}
                        </div>
                        <p className="mt-1 text-[13px] text-muted">
                          Last 3 covered months average <Money v={d.trend.recent_avg} /> against <Money v={d.trend.prior_avg} /> for the 3 before ({fmtMonth(d.trend.prior_months[0])} to {fmtMonth(d.trend.prior_months.at(-1))}).
                        </p>
                      </>
                    ) : <p className="text-[13px] text-muted">Needs six covered months to compare.</p>}
                  </Card>
                </div>
                <Card className="lg:col-span-2" title="Merchants, last 12 months" subtitle={`${d.totals.n_tx} transactions, ${fmtMoney(d.totals.last_12_months, { round: true })} in total`}>
                  {d.entities.length === 0 ? <EmptyState title="Nothing in the last 12 months" /> : (
                    <ul className="grid gap-3">
                      {d.entities.slice(0, 12).map((e) => (
                        <li key={e.entity}>
                          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                            <Link to={`/transactions?entity=${encodeURIComponent(e.entity)}`} className="min-w-0 truncate font-medium hover:underline">{e.entity}</Link>
                            <span className="num shrink-0"><b>{fmtMoney(e.total, { round: true })}</b> <span className="text-xs text-faint">{e.n_tx} tx · {fmtPct(e.share, 0)} · last {fmtDate(e.last_date, "dayMonth")}</span></span>
                          </div>
                          <ShareBar value={parseMoney(e.total) ?? 0} max={maxE} />
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
                <Card title="One-offs" subtitle="Left out of the usual month">
                  {d.one_offs.length === 0 ? <p className="text-[13px] text-muted">None tagged.</p> : (
                    <ul className="divide-y divide-border text-sm">
                      {d.one_offs.slice(0, 8).map((o) => (
                        <li key={o.tx_key} className="flex items-center justify-between gap-3 py-1.5">
                          <button className="min-w-0 truncate text-left hover:underline" onClick={() => nav(`/transactions?tx=${encodeURIComponent(o.tx_key)}`)}>{o.entity}<span className="block text-xs text-faint">{fmtDate(o.date)}{o.event ? ` · ${o.event}` : ""}</span></button>
                          <Money v={o.amount} />
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              </div>
              {d.notes.length > 0 && <div className="mt-4 grid gap-2">{d.notes.map((n) => <Notice key={n}>{n}</Notice>)}</div>}
            </>
          );
        }}
      </Async>
    </>
  );
}
