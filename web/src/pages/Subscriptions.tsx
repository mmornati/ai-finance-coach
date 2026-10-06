import { useState } from "react";
import { Link } from "react-router";
import { ArrowUpRight, ArrowDownRight, FileWarning, FileCheck2 } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, PageHeader, Segmented, Select, Skeleton, Stat, Tabs } from "@/components/ui";
import InventoryView from "@/pages/Inventory";
import { Sparkline } from "@/components/charts";
import { useGet, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtPct, fmtRelativeDays, groupLabel, parseMoney } from "@/lib/format";
import type { Series, Subscriptions as Subs } from "@/api/types";

const CADENCE: Record<string, string> = { weekly: "weekly", biweekly: "every 2 weeks", monthly: "monthly", bimonthly: "every 2 months", quarterly: "quarterly", semiannual: "every 6 months", yearly: "yearly" };

export default function Subscriptions() {
  const [tab, setTab] = useState<"inventory" | "detected">("inventory");
  return (
    <>
      <PageHeader title="Subscriptions & contracts" subtitle="Every recurring cost with its yearly price, contract, usage, cancellation rules, cheaper offers and the savings you achieved. Nothing here cancels or sends anything." />
      <div className="mb-4">
        <Tabs label="View" value={tab} onChange={setTab} tabs={[{ value: "inventory", label: "Inventory" }, { value: "detected", label: "Detected payments" }]} />
      </div>
      {tab === "inventory" ? <InventoryView /> : <Detected />}
    </>
  );
}

function Detected() {
  const [status, setStatus] = useState<"active" | "ended" | "all">("active");
  const [cadence, setCadence] = useState("");
  const [onlyContracts, setOnly] = useState(true);
  const q = useScoped<Subs>("/subscriptions", { status, cadence, contracts_only: onlyContracts });
  return (
    <>
      <p className="mb-3 text-sm text-muted">Detected from your bank history: what each costs, when it is next due, what changed, and whether a contract is on file.</p>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented label="Status" value={status} onChange={setStatus} options={[{ value: "active", label: "Active" }, { value: "ended", label: "Ended" }, { value: "all", label: "All" }]} />
        <Select aria-label="Cadence" value={cadence} onChange={(e) => setCadence(e.target.value)} className="!w-auto">
          <option value="">Any cadence</option>
          {Object.entries(CADENCE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={onlyContracts} onChange={(e) => setOnly(e.target.checked)} /> Subscriptions, insurance and utilities only</label>
      </div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <Card>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Per month" value={fmtMoney(d.totals.active_monthly)} hint={`${d.totals.n_active} active`} />
                <Stat label="Per year" value={fmtMoney(d.totals.active_yearly, { round: true })} />
                <Stat label="Without a contract on file" value={String(d.totals.n_missing_contract)} tone={d.totals.n_missing_contract ? "warn" : undefined} hint="add one under Memory" />
                <Stat label="Price changes" value={String(d.series.reduce((n, s) => n + s.price_changes.filter((c) => !c.dismissed).length, 0))} />
              </div>
            </Card>
            {d.series.length === 0 ? <Card><EmptyState title="Nothing matches" /></Card> : (
              <ul className="grid gap-3">{d.series.map((s) => <li key={s.id}><SeriesRow s={s} /></li>)}</ul>
            )}
            {d.coverage.notes.map((n) => <p key={n} className="text-xs text-faint">{n}</p>)}
          </div>
        )}
      </Async>
    </>
  );
}

function SeriesRow({ s }: { s: Series }) {
  const dismiss = useWrite((id: string) => api.post(`/price-changes/${id}/dismiss`, {}), { success: "Price change dismissed" });
  const changes = s.price_changes.filter((c) => !c.dismissed);
  const amounts = s.occurrences.map((o) => Math.abs(parseMoney(o.amount) ?? 0));
  const income = s.direction === "in";
  return (
    <Card>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/transactions?entity=${encodeURIComponent(s.entity)}`} className="truncate text-[15px] font-semibold hover:underline">{s.entity}</Link>
            <Badge>{CADENCE[s.cadence] ?? s.cadence}</Badge>
            <Link to={`/categories/${s.category}`}><Badge tone="info">{catLabel(s.category)}</Badge></Link>
            {s.status === "ended" && <Badge tone="warn">ended {fmtDate(s.last_date, "dayMonth")}</Badge>}
            {s.confidence_label === "low" && <Badge title="Few occurrences">low confidence</Badge>}
            {s.amount_mode === "variable" && <Badge title="The amount varies">variable</Badge>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
            <span>{s.account_label}</span>
            {s.next_expected && s.status === "active" && <span>next {fmtDate(s.next_expected, "dayMonth")}{s.overdue_days > 0 ? ` (${s.overdue_days} d late)` : ""}</span>}
            <span>{s.n_occurrences} payments since {fmtDate(s.first_date, "medium")}</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {s.linked.length > 0 ? s.linked.map((l) => <Badge key={l.id} tone="pos"><FileCheck2 className="size-3" aria-hidden /> {l.kind === "contract" ? "Contract" : "Loan"}: {l.name}{l.renewal ? ` · renews ${fmtDate(l.renewal, "medium")}` : ""}{l.keep !== null && l.keep !== undefined ? ` · decision: ${l.keep === true ? "keep" : l.keep === false ? "cancel" : String(l.keep)}` : ""}</Badge>)
              : s.missing_contract ? <Badge tone="warn"><FileWarning className="size-3" aria-hidden /> No contract on file</Badge> : null}
            {changes.map((c) => (
              <span key={c.id} className="inline-flex items-center gap-1">
                <Badge tone={c.direction === "increase" ? (income ? "pos" : "neg") : income ? "neg" : "pos"}>
                  {c.direction === "increase" ? <ArrowUpRight className="size-3" aria-hidden /> : <ArrowDownRight className="size-3" aria-hidden />}
                  {fmtPct(c.pct, 1, { signed: true })} on {fmtDate(c.date, "dayMonth")}: {fmtMoney(c.old)} → {fmtMoney(c.new)}{!c.confirmed ? " (unconfirmed)" : ""}
                </Badge>
                <button className="text-xs text-muted underline hover:text-text" onClick={() => dismiss.mutate(c.id)}>dismiss</button>
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between gap-5 sm:justify-end">
          {amounts.length > 2 && <Sparkline values={amounts} label={`Last ${amounts.length} payments of ${s.entity}`} />}
          <div className="text-right">
            <div className="num text-lg font-semibold"><Money v={s.expected_amount} colored={income} /></div>
            <div className="num text-xs text-muted"><Money v={s.monthly_cost} /> /month · <Money v={s.yearly_cost} round /> /year</div>
          </div>
        </div>
      </div>
    </Card>
  );
}
