import { useState } from "react";
import { Link } from "react-router";
import { Async, Badge, Card, EmptyState, Input, Money, Notice, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { ShareBar } from "@/components/charts";
import { useScoped } from "@/api/hooks";
import { catLabel, fmtMoney, fmtMonth, groupLabel, parseMoney } from "@/lib/format";
import type { CategoryOverview } from "@/api/types";

export default function Categories() {
  const q = useScoped<CategoryOverview>("/categories");
  const [view, setView] = useState<"groups" | "flat">("groups");
  const [text, setText] = useState("");
  return (
    <>
      <PageHeader title="Categories" subtitle="What a usual month costs per category (coverage-aware, one-offs left out), next to this month and last month." actions={<Segmented label="View" value={view} onChange={setView} options={[{ value: "groups", label: "Grouped" }, { value: "flat", label: "Ranked" }]} />} />
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
                    <div className="text-xs font-medium text-muted">A usual month, whole household</div>
                    <div className="num text-2xl font-semibold">{d.household_monthly_avg ? fmtMoney(d.household_monthly_avg) : "–"}</div>
                    <div className="text-xs text-faint">{d.household_months.length ? `average of ${d.household_months.length} months fully covered by every account (${fmtMonth(d.household_months[0])} to ${fmtMonth(d.household_months.at(-1))})` : "no month is fully covered by every account"}</div>
                  </div>
                  <Input aria-label="Filter categories" type="search" placeholder="Filter categories" value={text} onChange={(e) => setText(e.target.value)} className="max-w-60" />
                </div>
                {d.coverage.notes.map((n) => <Notice key={n} tone="warn" className="mt-3">{n}</Notice>)}
              </Card>
              {rows.length === 0 && <Card><EmptyState title="No category" /></Card>}
              {view === "flat" ? (
                <Card pad={false}><CatTable rows={rows} max={max} /></Card>
              ) : (
                groups.map((g) => (
                  <Card key={g.group} title={<Link className="hover:underline" to={`/categories/${g.group}`}>{groupLabel(g.group)}</Link>} action={<span className="num text-sm text-muted">usual month <b className="text-text">{g.monthly_avg ? <Money v={g.monthly_avg} round /> : "–"}</b>{g.low_confidence && <Badge tone="warn" className="ml-2">low confidence</Badge>}</span>} pad={false}>
                    <CatTable rows={g.rows} max={max} />
                  </Card>
                ))
              )}
              {d.unavailable.length > 0 && <Notice title="No fair average yet for">{d.unavailable.map((u) => catLabel(u.category)).join(", ")}: no month is fully covered by all the accounts carrying them.</Notice>}
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
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">Category</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">Usual month</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">This month</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">Last month</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.category} className="border-t border-border">
              <td className="px-4 py-2">
                <Link to={`/categories/${r.category}`} className="font-medium hover:underline">{catLabel(r.category)}</Link>
                {r.low_confidence && <Badge tone="warn" className="ml-2" title="Few fully covered months back this figure">low confidence</Badge>}
                {r.lumpy && <Badge className="ml-2" title="Seasonal: needs 12 covered months">lumpy</Badge>}
                <div className="mt-1 max-w-56"><ShareBar value={parseMoney(r.monthly_avg) ?? 0} max={max} /></div>
              </td>
              <td className="num px-2 py-2 text-right font-medium">{r.monthly_avg ? fmtMoney(r.monthly_avg, { round: true }) : "–"}<div className="text-[11px] font-normal text-faint">{r.n_months ? `${r.n_months} mo.` : "no fair average"}</div></td>
              <td className="num px-2 py-2 text-right">{fmtMoney(r.this_month, { round: true })}</td>
              <td className="num px-4 py-2 text-right text-muted">{fmtMoney(r.last_month, { round: true })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
