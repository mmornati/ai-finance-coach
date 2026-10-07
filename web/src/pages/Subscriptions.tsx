import { useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import { ArrowUpRight, ArrowDownRight, FileWarning, FileCheck2 } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, PageHeader, Segmented, Select, Skeleton, Stat, Tabs } from "@/components/ui";
import InventoryView from "@/pages/Inventory";
import { Sparkline } from "@/components/charts";
import { useGet, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtPct, fmtRelativeDays, groupLabel, parseMoney } from "@/lib/format";
import type { Series, Subscriptions as Subs } from "@/api/types";

const CADENCES = ["weekly", "biweekly", "monthly", "bimonthly", "quarterly", "semiannual", "yearly"] as const;
const isCadence = (c: string): c is (typeof CADENCES)[number] => (CADENCES as readonly string[]).includes(c);

export default function Subscriptions() {
  const [tab, setTab] = useState<"inventory" | "detected">("inventory");
  const { t } = useTranslation("subscriptions");
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="mb-4">
        <Tabs label={t("view")} value={tab} onChange={setTab} tabs={[{ value: "inventory", label: t("tab.inventory") }, { value: "detected", label: t("tab.detected") }]} />
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
  const { t } = useTranslation("subscriptions");
  return (
    <>
      <p className="mb-3 text-sm text-muted">{t("detected.intro")}</p>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented label={t("detected.status")} value={status} onChange={setStatus} options={[{ value: "active", label: t("detected.active") }, { value: "ended", label: t("detected.ended") }, { value: "all", label: t("detected.all") }]} />
        <Select aria-label={t("detected.cadence")} value={cadence} onChange={(e) => setCadence(e.target.value)} className="!w-auto">
          <option value="">{t("detected.anyCadence")}</option>
          {CADENCES.map((k) => <option key={k} value={k}>{t(`cadence.${k}`)}</option>)}
        </Select>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={onlyContracts} onChange={(e) => setOnly(e.target.checked)} /> {t("detected.onlyContracts")}</label>
      </div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <Card>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label={t("perMonth")} value={fmtMoney(d.totals.active_monthly)} hint={t("detected.activeCount", { n: d.totals.n_active })} />
                <Stat label={t("perYear")} value={fmtMoney(d.totals.active_yearly, { round: true })} />
                <Stat label={t("detected.withoutContract")} value={String(d.totals.n_missing_contract)} tone={d.totals.n_missing_contract ? "warn" : undefined} hint={t("detected.addUnderMemory")} />
                <Stat label={t("detected.priceChanges")} value={String(d.series.reduce((n, s) => n + s.price_changes.filter((c) => !c.dismissed).length, 0))} />
              </div>
            </Card>
            {d.series.length === 0 ? <Card><EmptyState title={t("detected.nothing")} /></Card> : (
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
  const { t } = useTranslation("subscriptions");
  const dismiss = useWrite((id: string) => api.post(`/price-changes/${id}/dismiss`, {}), { success: t("detected.dismissed") });
  const changes = s.price_changes.filter((c) => !c.dismissed);
  const amounts = s.occurrences.map((o) => Math.abs(parseMoney(o.amount) ?? 0));
  const income = s.direction === "in";
  return (
    <Card>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/transactions?entity=${encodeURIComponent(s.entity)}`} className="truncate text-[15px] font-semibold hover:underline">{s.entity}</Link>
            <Badge>{isCadence(s.cadence) ? t(`cadence.${s.cadence}`) : s.cadence}</Badge>
            <Link to={`/categories/${s.category}`}><Badge tone="info">{catLabel(s.category)}</Badge></Link>
            {s.status === "ended" && <Badge tone="warn">{t("detected.endedOn", { date: fmtDate(s.last_date, "dayMonth") })}</Badge>}
            {s.confidence_label === "low" && <Badge title={t("detected.fewOccurrences")}>{t("lowConfidence")}</Badge>}
            {s.amount_mode === "variable" && <Badge title={t("detected.variableTitle")}>{t("detected.variable")}</Badge>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted">
            <span>{s.account_label}</span>
            {s.next_expected && s.status === "active" && <span>{t("detected.next", { date: fmtDate(s.next_expected, "dayMonth") })}{s.overdue_days > 0 ? t("detected.late", { days: s.overdue_days }) : ""}</span>}
            <span>{t("detected.since", { count: s.n_occurrences, date: fmtDate(s.first_date, "medium") })}</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {s.linked.length > 0 ? s.linked.map((l) => <Badge key={l.id} tone="pos"><FileCheck2 className="size-3" aria-hidden /> {l.kind === "contract" ? t("detected.contract") : t("detected.loan")}: {l.name}{l.renewal ? t("detected.renews", { date: fmtDate(l.renewal, "medium") }) : ""}{l.keep !== null && l.keep !== undefined ? t("detected.decision", { decision: l.keep === true ? t("detected.keep") : l.keep === false ? t("detected.cancel") : String(l.keep) }) : ""}</Badge>)
              : s.missing_contract ? <Badge tone="warn"><FileWarning className="size-3" aria-hidden /> {t("detected.noContract")}</Badge> : null}
            {changes.map((c) => (
              <span key={c.id} className="inline-flex items-center gap-1">
                <Badge tone={c.direction === "increase" ? (income ? "pos" : "neg") : income ? "neg" : "pos"}>
                  {c.direction === "increase" ? <ArrowUpRight className="size-3" aria-hidden /> : <ArrowDownRight className="size-3" aria-hidden />}
                  {t("detected.change", { pct: fmtPct(c.pct, 1, { signed: true }), date: fmtDate(c.date, "dayMonth"), old: fmtMoney(c.old), new: fmtMoney(c.new) })}{!c.confirmed ? t("detected.unconfirmed") : ""}
                </Badge>
                <button className="text-xs text-muted underline hover:text-text" onClick={() => dismiss.mutate(c.id)}>{t("detected.dismiss")}</button>
              </span>
            ))}
          </div>
        </div>
        <div className="flex items-center justify-between gap-5 sm:justify-end">
          {amounts.length > 2 && <Sparkline values={amounts} label={t("detected.sparkline", { n: amounts.length, name: s.entity })} />}
          <div className="text-right">
            <div className="num text-lg font-semibold"><Money v={s.expected_amount} colored={income} /></div>
            <div className="num text-xs text-muted"><Trans t={t} i18nKey="monthYear" components={{ monthly: <Money v={s.monthly_cost} />, yearly: <Money v={s.yearly_cost} round /> }} /></div>
          </div>
        </div>
      </div>
    </Card>
  );
}
