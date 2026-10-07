import { useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import { Async, Badge, Card, EmptyState, Input, Money, Notice, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { ShareBar } from "@/components/charts";
import { useScoped } from "@/api/hooks";
import { catLabel, fmtMoney, fmtMonth, groupLabel, parseMoney } from "@/lib/format";
import type { CategoryOverview } from "@/api/types";

export default function Categories() {
  const q = useScoped<CategoryOverview>("/categories");
  const [view, setView] = useState<"groups" | "flat">("groups");
  const [text, setText] = useState("");
  const { t } = useTranslation("categories");
  return (
    <>
      <PageHeader title={t("overview.title")} subtitle={t("overview.subtitle")} actions={<Segmented label={t("overview.view")} value={view} onChange={setView} options={[{ value: "groups", label: t("overview.grouped") }, { value: "flat", label: t("overview.ranked") }]} />} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          const rows = d.categories.filter((c) => !text || catLabel(c.category).toLowerCase().includes(text.toLowerCase()) || c.category.includes(text.toLowerCase()));
          const max = Math.max(...rows.map((r) => parseMoney(r.monthly_avg) ?? 0), 1);
          const groups = groupRows(rows, d.groups);
          return (
            <div className="grid gap-4">
              <Card>
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <div className="text-xs font-medium text-muted">{t("overview.usualHousehold")}</div>
                    <div className="num text-2xl font-semibold">{d.household_monthly_avg ? fmtMoney(d.household_monthly_avg) : "–"}</div>
                    <div className="text-xs text-faint">{d.household_months.length ? t("overview.averageOf", { count: d.household_months.length, from: fmtMonth(d.household_months[0]), to: fmtMonth(d.household_months.at(-1)) }) : t("overview.noMonth")}</div>
                  </div>
                  <Input aria-label={t("overview.filter")} type="search" placeholder={t("overview.filter")} value={text} onChange={(e) => setText(e.target.value)} className="max-w-60" />
                </div>
                {d.coverage.notes.map((n) => <Notice key={n} tone="warn" className="mt-3">{n}</Notice>)}
              </Card>
              {rows.length === 0 && <Card><EmptyState title={t("overview.empty")} /></Card>}
              {view === "flat" ? (
                <Card pad={false}><CatTable rows={rows} max={max} /></Card>
              ) : (
                groups.map((g) => (
                  <Card key={g.group} title={<Link className="hover:underline" to={`/categories/${g.group}`}>{groupLabel(g.group)}</Link>} action={<span className="num text-sm text-muted"><Trans t={t} i18nKey="overview.usualMonth" components={{ b: <b className="text-text" />, amount: g.monthly_avg ? <Money v={g.monthly_avg} round /> : <>–</> }} />{g.low_confidence && <Badge tone="warn" className="ml-2">{t("overview.lowConfidence")}</Badge>}</span>} pad={false}>
                    <CatTable rows={g.rows} max={max} />
                  </Card>
                ))
              )}
              {d.unavailable.length > 0 && <Notice title={t("overview.unavailableTitle")}>{t("overview.unavailable", { list: d.unavailable.map((u) => catLabel(u.category)).join(", ") })}</Notice>}
            </div>
          );
        }}
      </Async>
    </>
  );
}

/** Rows grouped as the API groups them; every group figure (usual month, this / last month) is the analytics' own. */
function groupRows(rows: CategoryOverview["categories"], groups: CategoryOverview["groups"]) {
  return groups
    .map((g) => ({ ...g, rows: rows.filter((r) => r.group === g.group) }))
    .filter((g) => g.rows.length > 0);
}

function CatTable({ rows, max }: { rows: CategoryOverview["categories"]; max: number }) {
  const { t } = useTranslation("categories");
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">{t("table.category")}</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">{t("table.usualMonth")}</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">{t("table.thisMonth")}</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">{t("table.lastMonth")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.category} className="border-t border-border">
              <td className="px-4 py-2">
                <Link to={`/categories/${r.category}`} className="font-medium hover:underline">{catLabel(r.category)}</Link>
                {r.low_confidence && <Badge tone="warn" className="ml-2" title={t("table.lowConfidenceTitle")}>{t("overview.lowConfidence")}</Badge>}
                {r.lumpy && <Badge className="ml-2" title={t("table.lumpyTitle")}>{t("table.lumpy")}</Badge>}
                <div className="mt-1 max-w-56"><ShareBar value={parseMoney(r.monthly_avg) ?? 0} max={max} /></div>
              </td>
              <td className="num px-2 py-2 text-right font-medium">{r.monthly_avg ? fmtMoney(r.monthly_avg, { round: true }) : "–"}<div className="text-[11px] font-normal text-faint">{r.n_months ? t("table.months", { n: r.n_months }) : t("table.noFairAverage")}</div></td>
              <td className="num px-2 py-2 text-right">{fmtMoney(r.this_month, { round: true })}</td>
              <td className="num px-4 py-2 text-right text-muted">{fmtMoney(r.last_month, { round: true })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
