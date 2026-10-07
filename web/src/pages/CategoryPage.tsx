import { Link, useNavigate, useParams } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import { ArrowDownRight, ArrowLeft, ArrowUpRight } from "lucide-react";
import { Async, Badge, Card, EmptyState, Money, Notice, PageHeader, Skeleton, Stat } from "@/components/ui";
import { MonthlyBars, ShareBar } from "@/components/charts";
import { useScoped } from "@/api/hooks";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtPct, groupLabel, parseMoney } from "@/lib/format";
import type { CategoryDetail } from "@/api/types";
import { tServerList } from "@/i18n/server";

export default function CategoryPage() {
  const { id = "" } = useParams();
  const q = useScoped<CategoryDetail>(`/categories/${id}`);
  const nav = useNavigate();
  const { t } = useTranslation("categories");
  return (
    <>
      <Link to="/categories" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-text"><ArrowLeft className="size-4" aria-hidden /> {t("detail.back")}</Link>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          const avg = d.average ? parseMoney(d.average.monthly) : null;
          const maxE = Math.max(...d.entities.map((e) => parseMoney(e.total) ?? 0), 1);
          return (
            <>
              <PageHeader title={d.is_group ? groupLabel(d.id) : catLabel(d.id)} subtitle={d.description ?? (d.is_group ? t("detail.allOfGroup", { group: groupLabel(d.id).toLowerCase() }) : undefined)} actions={d.is_group ? undefined : <Link className="text-sm text-accent hover:underline" to={`/transactions?category=${d.id}`}>{t("detail.seeTransactions")}</Link>} />
              {d.is_group && <div className="mb-4 flex flex-wrap gap-2">{d.leaves.map((l) => <Link key={l.id} to={`/categories/${l.id}`}><Badge tone="info">{catLabel(l.id)}</Badge></Link>)}</div>}
              <div className="grid gap-4 lg:grid-cols-3">
                <Card className="lg:col-span-2" title={t("detail.byMonth")} subtitle={t("detail.byMonthHint")}>
                  {d.series.length ? <MonthlyBars series={d.series} average={avg} /> : <EmptyState title={t("detail.noTransactions")} />}
                </Card>
                <div className="grid content-start gap-4">
                  <Card title={t("detail.usual")}>
                    {d.average ? (
                      <>
                        <Stat big label={t("detail.withoutOneOffs")} value={fmtMoney(d.average.monthly)} hint={t("detail.fullyCovered", { count: d.average.n_months })} />
                        <p className="mt-2 text-[13px] text-muted"><Trans t={t} i18nKey="detail.withOneOffs" components={{ amount: <Money v={d.average.monthly_with_one_offs} /> }} /></p>
                        {d.average.accounts.length > 0 && <p className="mt-1 text-xs text-faint">{t("detail.basedOn", { accounts: d.average.accounts.join(", ") })}</p>}
                        {d.average.low_confidence && <Notice tone="warn" className="mt-2">{t("detail.lowConfidence", { count: d.average.n_months })}</Notice>}
                      </>
                    ) : (
                      <p className="text-[13px] text-muted">{d.kind === "spending" ? t("detail.noFairAverage") : t("detail.spendingOnly")}</p>
                    )}
                  </Card>
                  <Card title={t("detail.trend")}>
                    {d.trend ? (
                      <>
                        <div className="flex items-center gap-2 text-xl font-semibold num">
                          {(parseMoney(d.trend.delta) ?? 0) > 0 ? <ArrowUpRight className="size-5 text-neg" aria-hidden /> : <ArrowDownRight className="size-5 text-pos" aria-hidden />}
                          {d.trend.pct !== null ? fmtPct(d.trend.pct, 0, { signed: true }) : fmtMoney(d.trend.delta, { signed: true })}
                        </div>
                        <p className="mt-1 text-[13px] text-muted">
                          <Trans t={t} i18nKey="detail.trendLine" values={{ from: fmtMonth(d.trend.prior_months[0]), to: fmtMonth(d.trend.prior_months.at(-1)) }} components={{ recent: <Money v={d.trend.recent_avg} />, prior: <Money v={d.trend.prior_avg} /> }} />
                        </p>
                      </>
                    ) : <p className="text-[13px] text-muted">{t("detail.trendNeeds")}</p>}
                  </Card>
                </div>
                <Card className="lg:col-span-2" title={t("detail.merchants")} subtitle={t("detail.merchantsTotal", { count: d.totals.n_tx, total: fmtMoney(d.totals.last_12_months, { round: true }) })}>
                  {d.entities.length === 0 ? <EmptyState title={t("detail.nothing12")} /> : (
                    <ul className="grid gap-3">
                      {d.entities.slice(0, 12).map((e) => (
                        <li key={e.entity}>
                          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                            <Link to={`/transactions?entity=${encodeURIComponent(e.entity)}`} className="min-w-0 truncate font-medium hover:underline">{e.entity}</Link>
                            <span className="num shrink-0"><b>{fmtMoney(e.total, { round: true })}</b> <span className="text-xs text-faint">{t("detail.merchantLine", { n: e.n_tx, share: fmtPct(e.share, 0), date: fmtDate(e.last_date, "dayMonth") })}</span></span>
                          </div>
                          <ShareBar value={parseMoney(e.total) ?? 0} max={maxE} />
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
                <Card title={t("detail.oneOffs")} subtitle={t("detail.oneOffsHint")}>
                  {d.one_offs.length === 0 ? <p className="text-[13px] text-muted">{t("detail.noneTagged")}</p> : (
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
              {d.notes.length > 0 && <div className="mt-4 grid gap-2">{tServerList(d.notes, d.notes_msg).map((n) => <Notice key={n}>{n}</Notice>)}</div>}
            </>
          );
        }}
      </Async>
    </>
  );
}
