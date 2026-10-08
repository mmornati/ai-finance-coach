import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Building2, CalendarClock, FileText, Plus, Scale } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Segmented, Select, Skeleton, Stat, Tabs } from "@/components/ui";
import { RentalChart } from "@/components/charts";
import { useDisclaimers, useDryRun, useRental, useRentalIndicators, useRentalList, useRentalTax, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtMoney, fmtMonth, fmtPct } from "@/lib/format";
import { serverLabel, tServer, tServerList } from "@/i18n/server";
import type { DisclaimerKey, Disclaimers, EditResult, ReadingMsg, RentalDetail, RentalMonth, RentalPnl, RentalScheme } from "@/api/types";

type RentalKey = ParseKeys<"rental">;
type TabKey = "cashflow" | "scheme" | "tax" | "sell";
const STATUS_TONE: Record<string, "pos" | "neg" | "warn" | "neutral"> = { received: "pos", missing: "neg", partial: "warn", late_paid: "warn", declared_vacancy: "neutral", unknown: "neutral", not_let_yet: "neutral" };
// the server sends the code; an unknown code is shown as it is
const STATUS_TEXT: Record<string, RentalKey> = {
  received: "status.received", missing: "status.missing", partial: "status.partial", late_paid: "status.latePaid", declared_vacancy: "status.declaredVacancy", unknown: "status.unknown", not_let_yet: "status.notLetYet",
};
const LINK_TEXT: Record<string, RentalKey> = { declared: "link.declared", only_one: "link.onlyOne", none: "link.none", unknown: "link.unknown", asset: "link.asset", account: "link.account" };
const STATE_TEXT: Record<string, RentalKey> = { unknown: "state.unknown", not_started: "state.notStarted", active: "state.active", ended: "state.ended" };
const DECISION_TEXT: Record<string, RentalKey> = { extend: "decision.extend", not_extend: "decision.notExtend", undecided: "decision.undecided" };
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// The server's sentences come with a message (code + params) the web translates, the English being the fallback (docs/i18n.md, "Server text").
// A legal text is never a translation: the server names it by key and its wording comes from GET /meta/disclaimers in the interface language.
function disclaimerText(texts: Disclaimers["texts"] | undefined, key: DisclaimerKey | undefined, english: string): string {
  return (key && texts?.[key]) || english;
}
/** A reading of the indicators: the sentence, then the disclaimer it carries (general advice). Until the disclaimers are known, the English
 *  reading, which already ends with it. */
function readingText(msg: ReadingMsg | undefined, english: string, texts: Disclaimers["texts"] | undefined): string {
  if (!msg) return english;
  if (!msg.disclaimer) return tServer(msg, english);
  const line = texts?.[msg.disclaimer];
  return line ? `${tServer(msg, english)} ${line}` : english;
}

export default function Rental() {
  const { t } = useTranslation("rental");
  const list = useRentalList();
  const [sel, setSel] = useState<string>("");
  const [tab, setTab] = useState<TabKey>("cashflow");
  const [declare, setDeclare] = useState(false);
  return (
    <>
      <PageHeader
        title={t("header.title")}
        subtitle={t("header.subtitle")}
        actions={<Button onClick={() => setDeclare(true)}><Plus className="size-4" aria-hidden /> {t("header.declare")}</Button>}
      />
      <Async q={list} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          if (d.properties.length === 0)
            return (
              <Card>
                <EmptyState icon={<Building2 className="size-6" />} title={t("empty.title")} action={<Button variant="primary" onClick={() => setDeclare(true)}>{t("header.declare")}</Button>}>
                  {t("empty.body")}
                  {d.unlinked_rental_accounts.length > 0 && <> {t("empty.flagged", { n: d.unlinked_rental_accounts.length })}</>}
                </EmptyState>
              </Card>
            );
          const id = d.properties.some((p) => p.id === sel) ? sel : d.properties[0].id;
          return (
            <div className="grid gap-4">
              {d.properties.length > 1 && (
                <Segmented label={t("header.property")} value={id} onChange={setSel} options={d.properties.map((p) => ({ value: p.id, label: p.id }))} />
              )}
              {d.unlinked_rental_accounts.length > 0 && (
                <Notice tone="warn"><Trans t={t} i18nKey="header.unlinked" values={{ n: d.unlinked_rental_accounts.length }} components={{ code: <code /> }} /></Notice>
              )}
              <Tabs<TabKey>
                label={t("tabs.label")}
                value={tab}
                onChange={setTab}
                tabs={[
                  { value: "cashflow", label: t("tabs.cashflow") },
                  { value: "scheme", label: t("tabs.scheme"), badge: d.properties.find((p) => p.id === id)?.scheme.decision_needed ? <Badge tone="warn">{t("tabs.decide")}</Badge> : undefined },
                  { value: "tax", label: t("tabs.tax") },
                  { value: "sell", label: t("tabs.sell") },
                ]}
              />
              {tab === "cashflow" && <CashflowTab id={id} />}
              {tab === "scheme" && <SchemeTab id={id} />}
              {tab === "tax" && <TaxTab id={id} />}
              {tab === "sell" && <SellTab id={id} />}
            </div>
          );
        }}
      </Async>
      {declare && <DeclareDialog onClose={() => setDeclare(false)} onDone={(id) => { setSel(id); setDeclare(false); }} />}
    </>
  );
}

/* ------------------------------------------------------------------ cash flow and P&L (E15-1, E15-2) */
function CashflowTab({ id }: { id: string }) {
  const { t } = useTranslation("rental");
  const [year, setYear] = useState<number | undefined>();
  const q = useRental(id, { months: 12, ...(year ? { year } : {}) });
  const [facts, setFacts] = useState(false);
  const [vac, setVac] = useState(false);
  return (
    <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
      {(d) => (
        <div className="grid gap-4">
          <Links d={d} onEdit={() => setFacts(true)} />
          {d.cashflow.months.length === 0 ? (
            <Card><EmptyState icon={<Scale className="size-6" />} title={t("cashflow.emptyTitle")}><Trans t={t} i18nKey="cashflow.empty" values={{ tag: d.tag }} components={{ code: <code /> }} /></EmptyState></Card>
          ) : (
            <>
              <Card title={t("cashflow.title")} subtitle={t("cashflow.subtitle")}>
                <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label={t("cashflow.rentExpected")} value={<Money v={d.cashflow.rent.expected} />} hint={d.cashflow.rent.source === "declared" ? t("cashflow.rentDeclared") : d.cashflow.rent.source === "observed_median" ? t("cashflow.rentMedian") : t("common.unknown")} />
                  <Stat label={t("cashflow.averageNet")} value={<Money v={d.cashflow.average?.net} colored signed />} hint={d.cashflow.average ? t("cashflow.overMonths", { n: d.cashflow.n_complete }) : t("cashflow.noCompleteMonth")} />
                  <Stat label={t("cashflow.averageEffort")} value={<Money v={d.cashflow.average?.effort} />} hint={t("cashflow.outOfPocket")} tone={d.cashflow.average && parseFloat(d.cashflow.average.effort) > 0 ? "warn" : undefined} />
                  <Stat label={t("cashflow.occupancy")} value={d.cashflow.vacancy.occupancy_rate === null ? "–" : fmtPct(d.cashflow.vacancy.occupancy_rate)} hint={t("cashflow.occupancyHint", { missing: d.cashflow.vacancy.n_missing, declared: d.cashflow.vacancy.n_declared })} tone={d.cashflow.vacancy.n_missing > 0 ? "warn" : undefined} />
                </div>
                <RentalChart months={d.cashflow.months} />
                <MonthsTable months={d.cashflow.months} />
              </Card>
              <Vacancy d={d} onDeclare={() => setVac(true)} />
              <PnlCard p={d.pnl} years={d.years} year={year ?? d.pnl.year} onYear={setYear} />
              {d.flows_to_label > 0 && (
                <Notice tone="info">
                  <Trans t={t} i18nKey="cashflow.flowsToLabel" values={{ n: d.flows_to_label, tag: d.tag }} components={{ code: <code /> }} />
                </Notice>
              )}
            </>
          )}
          {facts && <FactsDialog d={d} onClose={() => setFacts(false)} />}
          {vac && <VacancyDialog id={id} onClose={() => setVac(false)} />}
        </div>
      )}
    </Async>
  );
}

function Links({ d, onEdit }: { d: RentalDetail; onEdit: () => void }) {
  const { t } = useTranslation("rental");
  const ok = d.links.account === "declared" && d.links.loans > 0;
  const linkText = (code: string) => (LINK_TEXT[code] ? t(LINK_TEXT[code]) : code);
  return (
    <Card title={t("links.title", { id: d.id })} action={<Button size="sm" onClick={onEdit}>{t("common.editFacts")}</Button>}>
      <div className="flex flex-wrap gap-2 text-[13px]">
        <Badge tone={d.links.account === "declared" ? "pos" : d.links.account === "only_one" ? "warn" : "neg"}>{t("links.account", { value: linkText(d.links.account) })}</Badge>
        <Badge tone={d.links.loans > 0 ? "pos" : "warn"}>{t("links.loan", { value: d.links.loans > 0 ? linkText(d.links.loan) : t("link.none") })}</Badge>
        {d.scheme.declared && <Badge tone="info">{t("links.scheme", { value: d.scheme.scheme ?? t("links.recorded") })}</Badge>}
        {d.scheme.missing.length > 0 && <Badge tone="warn">{t("links.missing", { n: d.scheme.missing.length })}</Badge>}
      </div>
      {!ok && <p className="mt-2 text-xs text-muted">{t("links.hint")}</p>}
      {tServerList(d.links.notes, d.links.notes_msg).map((n, i) => <p key={i} className="mt-1 text-xs text-warn">{n}</p>)}
    </Card>
  );
}

function MonthsTable({ months }: { months: RentalMonth[] }) {
  const { t } = useTranslation("rental");
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th scope="col" className="py-1.5 pr-2 font-medium">{t("table.month")}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{t("table.rent")}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{t("table.loan")}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{t("table.otherCosts")}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{t("table.net")}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{t("table.effort")}</th>
            <th scope="col" className="py-1.5 pl-2 font-medium">{t("table.rentStatus")}</th>
          </tr>
        </thead>
        <tbody>
          {[...months].reverse().map((m) => (
            <tr key={m.month} className="border-t border-border">
              <td className="py-1.5 pr-2">{fmtMonth(m.month, "long")}{!m.complete && <span title={t("table.partialTitle")}> *</span>}</td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.rent} /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.loan} /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={String(parseFloat(m.costs) - parseFloat(m.loan))} /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.net} colored signed /></td>
              <td className="num px-2 py-1.5 text-right"><Money v={m.effort} /></td>
              <td className="py-1.5 pl-2"><Badge tone={STATUS_TONE[m.rent_status] ?? "neutral"}>{STATUS_TEXT[m.rent_status] ? t(STATUS_TEXT[m.rent_status]) : m.rent_status}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-faint">{t("table.partialNote")}</p>
    </div>
  );
}

function Vacancy({ d, onDeclare }: { d: RentalDetail; onDeclare: () => void }) {
  const { t } = useTranslation("rental");
  const v = d.cashflow.vacancy;
  const cur = d.current_month;
  return (
    <Card title={t("vacancy.title")} subtitle={t("vacancy.subtitle")} action={<Button size="sm" onClick={onDeclare}>{t("vacancy.declare")}</Button>}>
      {v.n_missing === 0 && v.n_declared === 0 && cur.status !== "late" ? (
        <p className="text-sm text-muted">{t("vacancy.none", { n: v.months_since_let })}</p>
      ) : (
        <ul className="grid gap-1 text-sm">
          {v.missing_months.length > 0 && <li><Badge tone="neg">{t("status.missing")}</Badge> {v.missing_months.map((m) => fmtMonth(m, "long")).join(", ")}</li>}
          {v.declared_months.length > 0 && <li><Badge>{t("vacancy.declared")}</Badge> {v.declared_months.map((m) => fmtMonth(m, "long")).join(", ")}</li>}
          {v.late_paid_months.length > 0 && <li><Badge tone="warn">{t("status.latePaid")}</Badge> {v.late_paid_months.map((m) => fmtMonth(m, "long")).join(", ")}</li>}
          {cur.status === "late" && <li><Badge tone="warn">{t("vacancy.thisMonth")}</Badge> {t("vacancy.late", { month: fmtMonth(cur.month, "long") })}</li>}
        </ul>
      )}
    </Card>
  );
}

function PnlCard({ p, years, year, onYear }: { p: RentalPnl; years: number[]; year: number; onYear: (y: number) => void }) {
  const { t } = useTranslation("rental");
  const rows: [RentalKey, string][] = [
    ["pnl.row.rent", p.totals.rent], ["pnl.row.loan", p.totals.loan], ["pnl.row.charges", p.totals.charges], ["pnl.row.fees", p.totals.fees], ["pnl.row.taxes", p.totals.taxes],
    ["pnl.row.insurance", p.totals.insurance], ["pnl.row.works", p.totals.works], ["pnl.row.other", p.totals.other],
  ];
  return (
    <Card
      title={t("pnl.title", { year: p.year })}
      subtitle={p.complete ? t("pnl.months", { n: p.n_months }) : t("pnl.monthsIncomplete", { n: p.n_months })}
      action={years.length > 1 ? <Select aria-label={t("pnl.year")} value={String(year)} onChange={(e) => onYear(Number(e.target.value))}>{years.map((y) => <option key={y} value={y}>{y}</option>)}</Select> : undefined}
    >
      <table className="w-full text-sm">
        <tbody>
          {rows.map(([label, v], i) => (
            <tr key={label} className="border-t border-border first:border-0">
              <td className="py-1.5">{i > 0 && "− "}{t(label)}</td>
              <td className="num py-1.5 text-right"><Money v={v} /></td>
            </tr>
          ))}
          <tr className="border-t border-border-strong font-semibold">
            <td className="py-2">= {t("pnl.net")}</td>
            <td className="num py-2 text-right"><Money v={p.totals.net} colored signed /></td>
          </tr>
        </tbody>
      </table>
      <div className="mt-3 grid gap-1 text-xs text-muted">
        <span>
          {p.monthly_average_effort ? (
            <Trans t={t} i18nKey="pnl.effortWithAverage" components={{ total: <Money v={p.totals.effort} />, average: <Money v={p.monthly_average_effort} />, owner: <Money v={p.totals.owner_in} /> }} />
          ) : (
            <Trans t={t} i18nKey="pnl.effort" components={{ total: <Money v={p.totals.effort} />, owner: <Money v={p.totals.owner_in} /> }} />
          )}
        </span>
        {p.loan_split && (
          <span>
            <Trans t={t} i18nKey="pnl.loanSplit" components={{ interest: <Money v={p.loan_split.interest} />, insurance: <Money v={p.loan_split.insurance} />, principal: <Money v={p.loan_split.principal} /> }} />
            {p.economic && <> <Trans t={t} i18nKey="pnl.economic" components={{ result: <Money v={p.economic.result} colored signed /> }} /></>}
          </span>
        )}
        {p.gross_yield_pct !== undefined && <span>{t("pnl.grossYield", { pct: p.gross_yield_pct })}</span>}
        {p.months_incomplete.length > 0 && <span className="text-warn">{t("pnl.notCovered", { months: p.months_incomplete.join(", ") })}</span>}
        {p.months_missing_data.length > 0 && <span className="text-warn">{t("pnl.noData", { months: p.months_missing_data.join(", ") })}</span>}
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ scheme commitment (E15-3) */
function SchemeTab({ id }: { id: string }) {
  const { t } = useTranslation("rental");
  const q = useRental(id, { months: 3 });
  const [facts, setFacts] = useState(false);
  const [ext, setExt] = useState(false);
  return (
    <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
      {(d) => {
        const s = d.scheme;
        return (
          <div className="grid gap-4">
            {!s.declared ? (
              <Card><EmptyState icon={<CalendarClock className="size-6" />} title={t("scheme.emptyTitle")} action={<Button variant="primary" onClick={() => setFacts(true)}>{t("scheme.record")}</Button>}>
                {t("scheme.empty")}
              </EmptyState></Card>
            ) : (
              <Card title={t("scheme.title", { scheme: s.scheme ?? t("scheme.fallbackName") })} subtitle={t("scheme.subtitle")} action={<Button size="sm" onClick={() => setFacts(true)}>{t("common.editFacts")}</Button>}>
                <SchemeBody s={s} onExtension={() => setExt(true)} />
              </Card>
            )}
            {s.declared && (
              <div className="grid gap-4 sm:grid-cols-2">
                <Card title={t("rentCap.title")} subtitle={t("rentCap.subtitle")}>
                  <p className="text-sm">
                    {s.rent_cap.status === "unknown" ? t("rentCap.notChecked") : (
                      <>
                        <Badge tone={s.rent_cap.status === "above_cap" ? "neg" : "pos"}>{s.rent_cap.status === "above_cap" ? t("rentCap.above") : t("rentCap.within")}</Badge>{" "}
                        <Trans t={t} i18nKey="rentCap.compare" components={{ rent: <Money v={s.rent_cap.rent} />, cap: <Money v={s.rent_cap.cap} /> }} />
                      </>
                    )}
                  </p>
                  {s.rent_cap.rent_basis && <p className="mt-1 text-xs text-faint">{t("rentCap.basis", { rent: tServer(s.rent_cap.rent_basis_msg, s.rent_cap.rent_basis), cap: tServer(s.rent_cap.cap_basis_msg, s.rent_cap.cap_basis ?? "–") })}</p>}
                </Card>
                <Card title={t("tenantIncome.title")} subtitle={t("tenantIncome.subtitle")}>
                  <p className="text-sm">
                    {s.tenant_income.status === "unknown" ? t("tenantIncome.notChecked") : (
                      <>
                        <Badge tone={s.tenant_income.status === "above_limit" ? "neg" : "pos"}>{s.tenant_income.status === "above_limit" ? t("tenantIncome.above") : t("tenantIncome.within")}</Badge>{" "}
                        <Trans t={t} i18nKey="tenantIncome.compare" components={{ income: <Money v={s.tenant_income.tenant_income} />, limit: <Money v={s.tenant_income.limit} /> }} />
                      </>
                    )}
                  </p>
                </Card>
              </div>
            )}
            {s.missing.length > 0 && (
              <Card title={t("missingFacts.title")} subtitle={t("missingFacts.subtitle")}>
                <ul className="grid gap-1 text-sm">
                  {s.missing.map((m) => (
                    <li key={m.field}><code className="text-[13px]">{m.field}</code> <span className="text-muted">{t("missingFacts.neededFor", { what: tServer(m.needed_for_msg, m.needed_for) })}</span></li>
                  ))}
                </ul>
              </Card>
            )}
            {facts && <FactsDialog d={d} onClose={() => setFacts(false)} />}
            {ext && <ExtensionDialog id={id} onClose={() => setExt(false)} />}
          </div>
        );
      }}
    </Async>
  );
}

function SchemeBody({ s, onExtension }: { s: RentalScheme; onExtension: () => void }) {
  const { t } = useTranslation("rental");
  const decision = DECISION_TEXT[s.extension.decision] ? t(DECISION_TEXT[s.extension.decision]) : String(s.extension.decision).replace("_", " ");
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label={t("schemeBody.start")} value={s.start_date ? fmtDate(s.start_date) : "–"} />
        <Stat label={t("schemeBody.length")} value={s.years ? t("schemeBody.years", { count: s.years }) : "–"} />
        <Stat label={t("schemeBody.end")} value={s.end_date ? fmtDate(s.end_date) : "–"} hint={s.end_source ? tServer(s.end_source_msg, s.end_source) : undefined} />
        <Stat
          label={t("schemeBody.left")}
          value={s.state === "unknown" ? "–" : s.state === "ended" ? t("state.ended") : s.state === "not_started" ? t("state.notStarted") : t("common.months", { count: s.months_left ?? 0 })}
          hint={s.days_left !== undefined ? t("common.days", { count: s.days_left }) : undefined}
          tone={s.decision_needed ? "warn" : undefined}
        />
      </div>
      {s.progress_pct !== undefined && <ProgressBar label={t("schemeBody.progress")} value={s.progress_pct} max={100} tone={s.decision_needed ? "warn" : "info"} />}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={s.extension.decision === "undecided" ? "warn" : "pos"}>{t("schemeBody.extension", { decision })}</Badge>
        {s.extension.years ? (
          <span className="text-muted">
            {s.extension.decided_on ? t("schemeBody.extensionYearsDecided", { n: s.extension.years, date: fmtDate(s.extension.decided_on) }) : t("schemeBody.extensionYears", { n: s.extension.years })}
          </span>
        ) : null}
        <Button size="sm" onClick={onExtension}>{t("schemeBody.recordDecision")}</Button>
      </div>
      {s.reminders && s.reminders.length > 0 && (
        <p className="text-xs text-muted">
          {t("schemeBody.reminders", { list: s.reminders.map((r) => t("schemeBody.reminder", { n: r.months_before, date: fmtDate(r.date) })).join(", ") })}
        </p>
      )}
      {tServerList(s.warnings, s.warnings_msg).map((w, i) => <Notice key={i} tone="warn">{w}</Notice>)}
    </div>
  );
}

function ExtensionDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation("rental");
  const [decision, setDecision] = useState<"extend" | "not_extend" | "undecided">("extend");
  const [years, setYears] = useState("3");
  const [rate, setRate] = useState("");
  const [note, setNote] = useState("");
  const body = useMemo(
    () => ({ decision, years: decision === "extend" ? Number(years) || undefined : undefined, additional_rate_pct: decision === "extend" && rate ? Number(rate) : undefined, note: note || undefined }),
    [decision, years, rate, note],
  );
  const ready = decision !== "extend" || Number(years) >= 1;
  const pv = useDryRun<EditResult>(`/rental/${id}/extension`, body, ready);
  const save = useWrite(() => api.post<EditResult>(`/rental/${id}/extension`, body), { success: t("extension.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("extension.title")} description={t("extension.description")}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("common.save")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("extension.decision")}>{(f) => <Select id={f} value={decision} onChange={(e) => setDecision(e.target.value as typeof decision)}><option value="extend">{t("extension.option.extend")}</option><option value="not_extend">{t("extension.option.notExtend")}</option><option value="undecided">{t("extension.option.undecided")}</option></Select>}</Field>
        {decision === "extend" && <Field label={t("extension.years")}>{(f) => <Input id={f} inputMode="numeric" value={years} onChange={(e) => setYears(e.target.value)} />}</Field>}
        {decision === "extend" && <Field label={t("extension.rate")} hint={t("extension.rateHint")}>{(f) => <Input id={f} inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />}</Field>}
        <Field label={t("common.note")} className="sm:col-span-2">{(f) => <Input id={f} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

function VacancyDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation("rental");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [note, setNote] = useState("");
  const body = useMemo(() => ({ start, end: end || undefined, note: note || undefined }), [start, end, note]);
  const ready = ISO_DATE.test(start) && (!end || ISO_DATE.test(end));
  const pv = useDryRun<EditResult>(`/rental/${id}/vacancy`, body, ready);
  const save = useWrite(() => api.post<EditResult>(`/rental/${id}/vacancy`, body), { success: t("vacancyDialog.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("vacancyDialog.title")} description={t("vacancyDialog.description")}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("common.save")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("vacancyDialog.from")} hint={t("common.dateHint")}>{(f) => <Input id={f} value={start} onChange={(e) => setStart(e.target.value)} placeholder="2026-03-01" />}</Field>
        <Field label={t("vacancyDialog.to")} hint={t("common.dateHint")}>{(f) => <Input id={f} value={end} onChange={(e) => setEnd(e.target.value)} placeholder="2026-04-30" />}</Field>
        <Field label={t("common.note")} className="sm:col-span-2">{(f) => <Input id={f} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ tax year (E15-4) */
function TaxTab({ id }: { id: string }) {
  const { t: tr } = useTranslation("rental");
  const [year, setYear] = useState<number | undefined>();
  const q = useRentalTax(id, year);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const disclaimers = useDisclaimers();
  const now = new Date();
  const first = now.getMonth() >= 9 ? now.getFullYear() : now.getFullYear() - 1;
  const years = [first, first - 1, first - 2, first - 3];
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label={tr("tax.incomeYear")} value={String(year ?? first)} onChange={(v) => setYear(Number(v))} options={years.map((y) => ({ value: String(y), label: String(y) }))} />
        <span className="text-xs text-muted">{tr("tax.declaredNextSpring")}</span>
      </div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(t) =>
          t.status !== "computed" ? (
            <Card><EmptyState icon={<FileText className="size-6" />} title={tr("tax.notModelled")}>{tServer(t.note_msg, t.note ?? "")}</EmptyState></Card>
          ) : (
            <div className="grid gap-4">
              <Notice tone="warn">{tr("tax.candidates")} {disclaimerText(disclaimers, t.disclaimer_key, t.disclaimer)}</Notice>
              {(t.months_incomplete?.length || t.months_missing_data?.length) ? (
                <Notice tone="info">
                  {tr("tax.notCovered", {
                    details: [
                      t.months_incomplete?.length ? tr("tax.partialMonths", { months: t.months_incomplete.join(", ") }) : "",
                      t.months_missing_data?.length ? tr("tax.noDataMonths", { months: t.months_missing_data.join(", ") }) : "",
                    ].join(""),
                  })}
                </Notice>
              ) : null}
              <div className="grid gap-4 lg:grid-cols-2">
                <Card title={tr("tax.micro.title")} subtitle={tr("tax.micro.subtitle", { pct: t.micro_foncier?.abatement_pct })}>
                  <Row label={tr("tax.grossRents")} v={t.micro_foncier!.gross_rents} />
                  <Row label={tr("tax.micro.abatement")} v={t.micro_foncier!.abatement} />
                  <Row label={tr("tax.micro.taxable")} v={t.micro_foncier!.taxable} bold />
                  {!t.micro_foncier!.within_ceiling && (
                    <p className="mt-2 text-xs text-warn">
                      <Trans t={tr} i18nKey="tax.micro.overCeiling" components={{ rents: <Money v={t.micro_foncier!.household_gross_rents} />, ceiling: <Money v={t.micro_foncier!.ceiling} /> }} />
                    </p>
                  )}
                </Card>
                <Card title={tr("tax.reel.title")} subtitle={tr("tax.reel.subtitle")}>
                  <Row label={tr("tax.grossRents")} v={t.reel!.gross_rents} />
                  {t.reel!.deductible.map((i) => (
                    <div key={i.item} className="flex items-baseline justify-between gap-3 border-t border-border py-1.5 text-sm" title={`${tServer(i.source_msg, i.source)}: ${tServer(i.bound_msg, i.bound)}`}>
                      <span>− {tServer(i.item_msg, i.item)}<span className="block text-[11px] text-faint">{tServer(i.bound_msg, i.bound)}</span></span>
                      <Money v={i.amount} />
                    </div>
                  ))}
                  <Row label={tr("tax.reel.net")} v={t.reel!.net} bold />
                  {tServerList(t.reel!.unknown, t.reel!.unknown_msg).map((u, i) => <p key={i} className="mt-2 text-xs text-warn">{tr("tax.reel.unknown", { what: u })}</p>)}
                </Card>
              </div>
              <Card title={tr("tax.lower.title")} subtitle={tr("tax.lower.subtitle")}>
                <p className="text-sm">
                  <Trans
                    t={tr}
                    i18nKey="tax.lower.text"
                    values={{ regime: t.lower_taxable_candidate === "reel" ? tr("tax.lower.reel") : tr("tax.lower.micro"), reel: t.reel!.net, micro: t.micro_foncier!.taxable }}
                    components={{ b: <b />, difference: <Money v={t.difference} signed colored /> }}
                  />
                </p>
              </Card>
              <SchemeReductionCard t={t} />
              <Card title={tr("tax.documents.title")} subtitle={tr("tax.documents.subtitle")}>
                <ul className="grid gap-1.5 text-sm">
                  {t.documents?.map((d) => (
                    <li key={d.id}>
                      <label className="flex items-start gap-2">
                        <input type="checkbox" className="mt-1" checked={!!done[d.id]} onChange={(e) => setDone({ ...done, [d.id]: e.target.checked })} />
                        <span>{serverLabel("rentalDocument", d.id, d.item)} <span className="text-faint">({serverLabel("rentalDocumentFrom", d.id, d.from)})</span></span>
                      </label>
                    </li>
                  ))}
                </ul>
                <ul className="mt-3 grid gap-1 text-xs text-muted">{tServerList(t.notes, t.notes_msg).map((n, i) => <li key={i}>{n}</li>)}</ul>
              </Card>
            </div>
          )
        }
      </Async>
    </div>
  );
}

function Row({ label, v, bold }: { label: string; v: string | null | undefined; bold?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between gap-3 border-t border-border py-1.5 text-sm first:border-0 ${bold ? "font-semibold" : ""}`}>
      <span>{label}</span>
      <Money v={v} />
    </div>
  );
}

function SchemeReductionCard({ t: tax }: { t: import("@/api/types").RentalTax }) {
  const { t } = useTranslation("rental");
  const r = tax.scheme_reduction;
  if (!r || r.status === "no_scheme") return null;
  return (
    <Card title={t("reduction.title")} subtitle={t("reduction.subtitle")}>
      {r.status === "needs_fields" ? (
        <p className="text-sm text-muted">{t("common.missing", { fields: tServerList(r.missing, r.missing_msg).join(", ") })}</p>
      ) : (
        <div className="grid gap-2 text-sm">
          <p>
            <Trans t={t} i18nKey={r.in_window ? "reduction.forYear" : "reduction.forYearOutside"} values={{ year: tax.year }} components={{ b: <b />, amount: <Money v={r.candidate} /> }} />
          </p>
          <p className="text-xs text-muted">
            <Trans t={t} i18nKey="reduction.calc" values={{ rate: r.rate_pct, n: r.years, first: r.first_year, last: r.last_year }} components={{ base: <Money v={r.base} />, total: <Money v={r.total} /> }} />
          </p>
          {tServerList(r.notes, r.notes_msg).map((n, i) => <p key={i} className="text-xs text-faint">{n}</p>)}
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ renegotiate or sell (E15-5) */
function SellTab({ id }: { id: string }) {
  const { t } = useTranslation("rental");
  const [rate, setRate] = useState("");
  const [day, setDay] = useState("");
  const [fees, setFees] = useState("");
  const params = useMemo(() => ({ ...(rate ? { market_rate: Number(rate) } : {}), ...(rate && day ? { market_date: day } : {}), ...(fees ? { bank_fees: Number(fees) } : {}) }), [rate, day, fees]);
  const q = useRentalIndicators(id, params);
  const [save, setSave] = useState(false);
  const disclaimers = useDisclaimers();
  return (
    <div className="grid gap-4">
      <Card title={t("market.title")} subtitle={t("market.subtitle")}>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label={t("market.rate")}>{(f) => <Input id={f} inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} placeholder="3.1" />}</Field>
          <Field label={t("market.asOf")} hint={t("common.dateHint")}>{(f) => <Input id={f} value={day} onChange={(e) => setDay(e.target.value)} placeholder="2026-10-01" />}</Field>
          <Field label={t("market.fees")} hint={t("market.feesHint")}>{(f) => <Input id={f} inputMode="decimal" value={fees} onChange={(e) => setFees(e.target.value)} />}</Field>
          <div className="flex items-end"><Button disabled={!rate || !ISO_DATE.test(day)} onClick={() => setSave(true)}>{t("market.remember")}</Button></div>
        </div>
      </Card>
      <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <div className="grid gap-4 lg:grid-cols-3">
              <Card title={t("vsMarket.title")}>
                {d.loan_rate.loan_rate_pct === undefined ? <p className="text-sm text-muted">{t("common.missing", { fields: tServerList(d.loan_rate.missing, d.loan_rate.missing_msg).join(", ") })}</p> : (
                  <div className="grid gap-1 text-sm">
                    <Stat
                      label={t("vsMarket.yourLoan")}
                      value={t("common.pct", { pct: d.loan_rate.loan_rate_pct })}
                      hint={d.loan_rate.market_rate_pct !== undefined ? t("vsMarket.gap", { market: d.loan_rate.market_rate_pct, gap: d.loan_rate.gap_pts?.toFixed(2) }) : t("vsMarket.enterRate")}
                      tone={d.loan_rate.status === "above_market" ? "warn" : undefined}
                    />
                    {d.loan_rate.reading && <p className="mt-1 text-xs text-muted">{readingText(d.loan_rate.reading_msg, d.loan_rate.reading, disclaimers)}</p>}
                  </div>
                )}
                {d.market_rate.status === "missing" && <p className="mt-2 text-xs text-faint">{tServer(d.market_rate.note_msg, d.market_rate.note ?? "")}</p>}
                {d.market_rate.warning && <p className="mt-2 text-xs text-warn">{tServer(d.market_rate.warning_msg, d.market_rate.warning)}</p>}
              </Card>
              <Card title={t("commitmentEnd.title")}>
                <Stat
                  label={t("commitmentEnd.state")}
                  value={STATE_TEXT[d.commitment.state] ? t(STATE_TEXT[d.commitment.state]) : d.commitment.state.replace("_", " ")}
                  hint={d.commitment.end_date ? (d.commitment.months_left !== undefined ? t("commitmentEnd.endsAbout", { date: fmtDate(d.commitment.end_date), count: d.commitment.months_left }) : t("commitmentEnd.ends", { date: fmtDate(d.commitment.end_date) })) : t("commitmentEnd.recordStart")}
                  tone={d.commitment.decision_needed ? "warn" : undefined}
                />
              </Card>
              <Card title={t("equity.title")} subtitle={t("equity.subtitle")}>
                {d.equity.status === "computed" ? (
                  <Stat label={t("equity.title")} value={<Money v={d.equity.net_equity} colored />} hint={t("equity.hint", { value: fmtMoney(d.equity.value), due: fmtMoney(d.equity.outstanding), pct: d.equity.equity_share_pct })} />
                ) : <p className="text-sm text-muted">{t("equity.unknown", { fields: tServerList(d.equity.missing, d.equity.missing_msg).join(", ") })}</p>}
                {d.equity.value_warning && <p className="mt-2 text-xs text-warn">{tServer(d.equity.value_warning_msg, d.equity.value_warning)}</p>}
              </Card>
            </div>
            {d.loan_rate.renegotiation?.status === "computed" && (
              <Card title={t("renegotiation.title")} subtitle={t("renegotiation.subtitle")}>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label={t("renegotiation.instalment")} value={`${d.loan_rate.renegotiation.current_payment} → ${d.loan_rate.renegotiation.new_payment}`} hint={t("renegotiation.perMonth")} />
                  <Stat label={t("renegotiation.costs")} value={<Money v={d.loan_rate.renegotiation.total_costs} />} hint={t("renegotiation.penalty", { amount: fmtMoney(d.loan_rate.renegotiation.penalty) })} />
                  <Stat label={t("renegotiation.netSaving")} value={<Money v={d.loan_rate.renegotiation.net_saving} colored signed />} />
                  <Stat label={t("renegotiation.breakEven")} value={d.loan_rate.renegotiation.break_even_months === null ? t("renegotiation.never") : t("common.months", { count: d.loan_rate.renegotiation.break_even_months })} />
                </div>
                {d.loan_rate.renegotiation_note && <p className="mt-2 text-xs text-faint">{tServer(d.loan_rate.renegotiation_note_msg, d.loan_rate.renegotiation_note)}</p>}
              </Card>
            )}
            <Card title={t("indicators.title")} subtitle={t("indicators.subtitle", { amount: fmtMoney(d.trailing_effort) })}>
              <ul className="grid gap-2 text-sm">{d.signals.length === 0 ? <li className="text-muted">{t("indicators.none")}</li> : d.signals.map((s) => <li key={s.id}>{readingText(s.reading_msg, s.reading, disclaimers)}</li>)}</ul>
              <ul className="mt-3 grid gap-1 text-xs text-muted">{tServerList(d.scenarios, d.scenarios_msg).map((s, i) => <li key={i}>{s}</li>)}</ul>
              <p className="mt-3 text-xs text-faint">{disclaimerText(disclaimers, d.disclaimer_key, d.disclaimer)}</p>
            </Card>
          </div>
        )}
      </Async>
      {save && <MarketRateDialog id={id} rate={Number(rate)} day={day} onClose={() => setSave(false)} />}
    </div>
  );
}

function MarketRateDialog({ id, rate, day, onClose }: { id: string; rate: number; day: string; onClose: () => void }) {
  const { t } = useTranslation("rental");
  const [source, setSource] = useState("");
  const body = useMemo(() => ({ rate_pct: rate, as_of: day, source: source || undefined }), [rate, day, source]);
  const pv = useDryRun<EditResult>(`/rental/${id}/market-rate`, body, true);
  const save = useWrite(() => api.post<EditResult>(`/rental/${id}/market-rate`, body), { success: t("marketDialog.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("marketDialog.title")} description={t("marketDialog.description")}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button variant="primary" disabled={!!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("common.save")}</Button></>}>
      <Field label={t("marketDialog.source")}>{(f) => <Input id={f} value={source} onChange={(e) => setSource(e.target.value)} placeholder={t("marketDialog.sourcePlaceholder")} />}</Field>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ the facts of a property */
interface FactField { path: string; label: RentalKey; hint?: RentalKey; kind: "text" | "number" | "date" | "int" }
const FACT_FIELDS: FactField[] = [
  { path: "account", label: "facts.field.account", hint: "facts.hint.account", kind: "text" },
  { path: "loan", label: "facts.field.loan", hint: "facts.hint.loan", kind: "text" },
  { path: "value", label: "facts.field.value", kind: "number" },
  { path: "as_of", label: "facts.field.asOf", hint: "common.dateHint", kind: "date" },
  { path: "rent_monthly", label: "facts.field.rentMonthly", kind: "number" },
  { path: "purchase_price", label: "facts.field.purchasePrice", kind: "number" },
  { path: "purchase_date", label: "facts.field.purchaseDate", hint: "common.dateHint", kind: "date" },
  { path: "scheme", label: "facts.field.scheme", hint: "facts.hint.scheme", kind: "text" },
  { path: "commitment.start_date", label: "facts.field.commitmentStart", hint: "facts.hint.commitmentStart", kind: "date" },
  { path: "commitment.years", label: "facts.field.commitmentYears", hint: "facts.hint.commitmentYears", kind: "int" },
  { path: "commitment.surface_m2", label: "facts.field.surface", kind: "number" },
  { path: "commitment.rent_cap_m2", label: "facts.field.rentCapM2", hint: "facts.hint.rentCapM2", kind: "number" },
  { path: "commitment.rent_cap_monthly", label: "facts.field.rentCapMonthly", hint: "facts.hint.rentCapMonthly", kind: "number" },
  { path: "commitment.tenant_income_limit", label: "facts.field.tenantIncomeLimit", kind: "number" },
  { path: "commitment.tenant_income", label: "facts.field.tenantIncome", kind: "number" },
  { path: "commitment.reduction_rate_pct", label: "facts.field.reductionRate", hint: "facts.hint.reductionRate", kind: "number" },
  { path: "commitment.reduction_base_cap", label: "facts.field.reductionBaseCap", hint: "facts.hint.reductionBaseCap", kind: "number" },
  { path: "commitment.reduction_first_year", label: "facts.field.reductionFirstYear", kind: "int" },
];

function currentValue(d: RentalDetail, path: string): string {
  const parts = path.split(".");
  let cur: unknown = d.asset;
  for (const p of parts) cur = cur && typeof cur === "object" ? (cur as Record<string, unknown>)[p] : undefined;
  return cur === undefined || cur === null ? "" : String(cur);
}

function FactsDialog({ d, onClose }: { d: RentalDetail; onClose: () => void }) {
  const { t } = useTranslation("rental");
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(FACT_FIELDS.map((f) => [f.path, currentValue(d, f.path)])));
  const { fields, bad } = useMemo(() => {
    const out: Record<string, unknown> = {};
    const bad: RentalKey[] = [];
    for (const f of FACT_FIELDS) {
      const raw = (vals[f.path] ?? "").trim();
      if (raw === currentValue(d, f.path).trim() || raw === "") continue;
      let v: unknown = raw;
      if (f.kind === "number" || f.kind === "int") {
        const n = Number(raw.replace(",", "."));
        if (!Number.isFinite(n) || (f.kind === "int" && !Number.isInteger(n))) { bad.push(f.label); continue; }
        v = n;
      } else if (f.kind === "date" && !ISO_DATE.test(raw)) { bad.push(f.label); continue; }
      const parts = f.path.split(".");
      if (parts.length === 1) out[parts[0]] = v;
      else out[parts[0]] = { ...((out[parts[0]] as object) ?? {}), [parts[1]]: v };
    }
    return { fields: out, bad };
  }, [vals, d]);
  const changed = Object.keys(fields).length > 0;
  // the reason is stored in the memory history as an identifier of where the change came from: not translated
  const body = useMemo(() => ({ fields, reason: "rental property facts (Rental page)" }), [fields]);
  const pv = useDryRun<EditResult>(`/memory/assets/${d.id}`, body, changed && bad.length === 0, "put");
  const save = useWrite(() => api.put<EditResult>(`/memory/assets/${d.id}`, body), { success: t("facts.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("facts.title")} size="lg" description={t("facts.description")}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button variant="primary" disabled={!changed || bad.length > 0 || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("common.save")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {FACT_FIELDS.map((f) => (
          <Field key={f.path} label={t(f.label)} hint={f.hint ? t(f.hint) : undefined}>{(id) => <Input id={id} value={vals[f.path] ?? ""} onChange={(e) => setVals({ ...vals, [f.path]: e.target.value })} inputMode={f.kind === "number" || f.kind === "int" ? "decimal" : undefined} />}</Field>
        ))}
      </div>
      <div className="mt-3">
        {bad.length > 0 && <Notice tone="neg">{t("facts.invalid", { fields: bad.map((k) => t(k)).join(", ") })}</Notice>}
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && <DiffView diff={pv.data.diff} />}
      </div>
    </Dialog>
  );
}

function DeclareDialog({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const { t } = useTranslation("rental");
  const [id, setId] = useState("");
  const [account, setAccount] = useState("");
  const [loan, setLoan] = useState("");
  // the reason is stored in the memory history as an identifier of where the change came from: not translated
  const body = useMemo(() => ({ fields: { kind: "real_estate_rental", ...(account ? { account } : {}), ...(loan ? { loan } : {}) }, reason: "declare a rental property (Rental page)" }), [account, loan]);
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id);
  const pv = useDryRun<EditResult>(`/memory/assets/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/memory/assets/${id}`, body), { success: t("declare.saved"), onSuccess: () => onDone(id) });
  return (
    <Dialog open onClose={onClose} title={t("declare.title")} description={t("declare.description")}
      footer={<><Button onClick={onClose}>{t("common.cancel")}</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("declare.submit")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={t("declare.id")} hint={t("declare.idHint")}>{(f) => <Input id={f} value={id} onChange={(e) => setId(e.target.value)} placeholder="rental-flat-1" />}</Field>
        <Field label={t("declare.account")} hint={t("declare.accountHint")}>{(f) => <Input id={f} value={account} onChange={(e) => setAccount(e.target.value)} />}</Field>
        <Field label={t("declare.loan")}>{(f) => <Input id={f} value={loan} onChange={(e) => setLoan(e.target.value)} />}</Field>
      </div>
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}
