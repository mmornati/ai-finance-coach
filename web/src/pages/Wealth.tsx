import { lazy, Suspense, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { AlertTriangle, Camera, Home, Pencil, Plus } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Skeleton, Stat } from "@/components/ui";
import { ItemDialog, Kind } from "@/components/ItemForm";
import { NetWorthChart } from "@/components/charts";
import { useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtNumber } from "@/lib/format";
import { holdingKindLabel, serverLabel, tServerOr, useServerText } from "@/i18n/server";
import type { Asset, Liability, NetWorth, NetWorthPoint, NwCategory } from "@/api/types";

const LoanDialog = lazy(() => import("@/components/LoanDetail"));

/** Loan kinds the page knows (the server sends the code; an unknown code is shown as it is). */
const LIAB: Record<string, ParseKeys<"wealth">> = {
  mortgage: "loans.kind.mortgage", car_loan: "loans.kind.carLoan", loa: "loans.kind.loa", lld: "loans.kind.lld", consumer_loan: "loans.kind.consumerLoan", bnpl: "loans.kind.bnpl",
};
const CAT_LABEL: Record<NwCategory | "liabilities", ParseKeys<"wealth">> = {
  cash: "breakdown.category.cash", savings: "breakdown.category.savings", investments: "breakdown.category.investments", real_estate: "breakdown.category.realEstate",
  vehicles: "breakdown.category.vehicles", other: "breakdown.category.other", liabilities: "breakdown.category.liabilities",
};
const RATE_TYPE: Record<string, ParseKeys<"wealth">> = { fixed: "loans.rateType.fixed", variable: "loans.rateType.variable", mixed: "loans.rateType.mixed" };

export default function Wealth() {
  const { t } = useTranslation("wealth");
  const { tServer } = useServerText();
  const nw = useGet<NetWorth>("/net-worth", { history: true, months: 36 });
  const liab = useGet<{ liabilities: Liability[]; totals: { outstanding_known: string; monthly_payments: string; n_unknown_outstanding: number }; note: string }>("/liabilities");
  const assets = useGet<{ assets: (Asset & Record<string, any>)[] }>("/assets");
  const [edit, setEdit] = useState<{ kind: Kind; id?: string; initial?: Record<string, any> } | null>(null);
  const [open, setOpen] = useState<Liability | null>(null);
  const snapshot = useWrite(() => api.post<{ net_worth: string; backfilled_months: number }>("/net-worth/snapshot", {}), { success: t("history.snapshotRecorded") });
  return (
    <>
      <PageHeader title={t("header.title")} subtitle={t("header.subtitle")} actions={<Button onClick={() => setEdit({ kind: "liabilities" })}><Plus className="size-4" aria-hidden /> {t("header.addLoan")}</Button>} />
      <div className="grid gap-4">
        <Card>
          <Async q={nw} skeleton={<Skeleton className="h-40 w-full" />}>
            {(d) => (
              <div className="grid gap-5 md:grid-cols-[1.1fr_2fr]">
                <div>
                  <Stat big label={d.complete ? t("summary.netWorth") : t("summary.netWorthKnownPart")} value={<Money v={d.net_worth} round />} hint={d.complete ? undefined : t("summary.itemsNotCounted", { count: d.unknown.length })} />
                  {!d.complete && <Notice tone="warn" className="mt-3" title={t("summary.notFullPicture")}>{tServer(d.note_msg, d.note)}</Notice>}
                </div>
                <dl className="grid content-start gap-3 text-sm sm:grid-cols-3">
                  <div className="rounded-lg bg-surface-2 p-3"><dt className="text-xs text-muted">{t("summary.bankBalances")}</dt><dd className="num mt-0.5 text-lg font-semibold"><Money v={d.bank.total} round /></dd><dd className="text-xs text-faint">{t("summary.accounts", { count: d.bank.accounts.length })}</dd></div>
                  <div className="rounded-lg bg-surface-2 p-3"><dt className="text-xs text-muted">{t("summary.manualAssets")}</dt><dd className="num mt-0.5 text-lg font-semibold"><Money v={d.assets.total} round /></dd><dd className="text-xs text-faint">{t("summary.withValue", { n: d.assets.items.filter((a) => a.counted).length })}</dd></div>
                  <div className="rounded-lg bg-surface-2 p-3"><dt className="text-xs text-muted">{t("summary.owedKnown")}</dt><dd className="num mt-0.5 text-lg font-semibold">{Number(d.liabilities.total) > 0 && "−"}<Money v={d.liabilities.total} round /></dd><dd className="text-xs text-faint">{t("summary.loansCounted", { counted: d.liabilities.items.filter((l) => l.counted).length, count: d.liabilities.items.filter((l) => !l.excluded).length })}</dd></div>
                </dl>
                <Breakdown d={d} />
                {d.unknown.length > 0 && (
                  <div className="md:col-span-2">
                    <h3 className="mb-1.5 text-xs font-semibold text-muted">{t("summary.notCountedUnknown")}</h3>
                    <ul className="flex flex-wrap gap-2">{d.unknown.map((u) => <li key={u.kind + u.id}><Badge tone="warn" title={tServer(u.reason_msg, u.reason)}><AlertTriangle className="size-3" aria-hidden />{t("summary.unknownItem", { kind: u.kind, label: u.label })}</Badge></li>)}</ul>
                  </div>
                )}
                {d.stale.length > 0 && <p className="text-xs text-faint md:col-span-2">{t("summary.toRefresh", { ids: d.stale.map((s) => s.id).join(", ") })}</p>}
              </div>
            )}
          </Async>
        </Card>

        <Card title={t("history.title")} subtitle={t("history.subtitle")} action={<Button size="sm" busy={snapshot.isPending} onClick={() => snapshot.mutate(undefined as never)}><Camera className="size-3.5" aria-hidden /> {t("history.recordToday")}</Button>}>
          <Async q={nw} skeleton={<Skeleton className="h-64 w-full" />}>
            {(d) => <History points={d.history ?? []} />}
          </Async>
        </Card>

        <Async q={liab} skeleton={<Skeleton className="h-48 w-full" />}>
          {(d) =>
            d.liabilities.length === 0 ? (
              <Card><EmptyState icon={<Home className="size-6" />} title={t("loans.emptyTitle")} action={<Button variant="primary" onClick={() => setEdit({ kind: "liabilities" })}>{t("loans.addFirst")}</Button>}>{t("loans.empty")}</EmptyState></Card>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {d.liabilities.map((l) => <LoanCard key={l.id} l={l} onOpen={() => setOpen(l)} onEdit={() => setEdit({ kind: "liabilities", id: l.id, initial: l })} />)}
              </div>
            )
          }
        </Async>

        <Async q={assets} skeleton={<Skeleton className="h-40 w-full" />}>
          {(d) => (
            <Card title={t("assets.title")} action={<Button size="sm" onClick={() => setEdit({ kind: "assets" })}><Plus className="size-3.5" aria-hidden /> {t("assets.add")}</Button>} pad={false}>
              {d.assets.length === 0 ? <EmptyState title={t("assets.emptyTitle")}>{t("assets.empty")}</EmptyState> : (
                <ul className="divide-y divide-border">
                  {d.assets.map((a) => (
                    <li key={a.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{a.description ?? a.id}</div>
                        <div className="text-xs text-muted">{[holdingKindLabel(a.kind), a.provider, a.holder, a.connected ? t("assets.syncedFromBank") : null].filter(Boolean).join(" · ")}</div>
                      </div>
                      <div className="text-right">
                        {a.unknown_value ? <Badge tone="warn">{t("assets.valueUnknown")}</Badge> : <div className="num font-semibold"><Money v={a.value} round /></div>}
                        <div className="text-xs text-faint">{a.as_of ? t("assets.asOf", { date: fmtDate(a.as_of) }) : ""}{a.stale && <Badge tone="warn" className="ml-1.5">{t("assets.refresh")}</Badge>}</div>
                      </div>
                      <Button size="sm" variant="ghost" aria-label={t("assets.editAria", { id: a.id })} onClick={() => setEdit({ kind: "assets", id: a.id, initial: a })}><Pencil className="size-3.5" /></Button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}
        </Async>
      </div>
      {edit && <ItemDialog {...edit} onClose={() => setEdit(null)} />}
      {open && (
        <Suspense fallback={null}>
          <LoanDialog loan={open} onClose={() => setOpen(null)} onEdit={() => { const l = open; setOpen(null); setEdit({ kind: "liabilities", id: l.id, initial: l }); }} />
        </Suspense>
      )}
    </>
  );
}

function History({ points }: { points: NetWorthPoint[] }) {
  const { t } = useTranslation("wealth");
  if (points.length === 0) return <EmptyState title={t("history.emptyTitle")}>{t("history.empty")}</EmptyState>;
  const partial = points.filter((p) => !p.complete).length;
  return (
    <div className="grid gap-2">
      <NetWorthChart points={points} />
      {partial > 0 && <p className="text-xs text-faint">{t("history.partial", { partial, total: points.length })}{points.some((p) => p.caveat) && <> {t("history.caveat")}</>}</p>}
    </div>
  );
}

function Breakdown({ d }: { d: NetWorth }) {
  const { t } = useTranslation("wealth");
  const cats = (Object.keys(d.by_category) as (keyof typeof d.by_category)[]).filter((k) => k !== "liabilities" && Number(d.by_category[k]) !== 0);
  const owners = Object.entries(d.by_owner);
  return (
    <div className="grid gap-3 md:col-span-2 sm:grid-cols-2">
      <div>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">{t("breakdown.byCategory")}</h3>
        <ul className="grid gap-1 text-sm">
          {cats.map((k) => <li key={k} className="flex justify-between"><span>{t(CAT_LABEL[k])}</span><span className="num"><Money v={d.by_category[k]} round /></span></li>)}
          <li className="flex justify-between text-muted"><span>{t(CAT_LABEL.liabilities)}</span><span className="num">−<Money v={d.by_category.liabilities} round /></span></li>
        </ul>
      </div>
      <div>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">{t("breakdown.byPerson")}</h3>
        <ul className="grid gap-1 text-sm">
          {owners.map(([o, v]) => <li key={o} className="flex justify-between"><span>{o === "unassigned" ? t("breakdown.unassigned") : o}{v.n_unknown > 0 && <span className="text-xs text-faint"> {t("breakdown.nUnknown", { count: v.n_unknown })}</span>}</span><span className="num"><Money v={v.net_worth} round /></span></li>)}
        </ul>
      </div>
    </div>
  );
}

function LoanCard({ l, onOpen, onEdit }: { l: Liability; onOpen: () => void; onEdit: () => void }) {
  const { t } = useTranslation("wealth");
  const lease = l.kind === "loa" || l.kind === "lld";
  const s = l.schedule;
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold">{l.lender ?? l.id}</h3>
          <p className="text-xs text-muted">{LIAB[l.kind] ? t(LIAB[l.kind]) : l.kind}{l.asset ? ` · ${l.asset}` : ""}</p>
        </div>
        <div className="flex gap-1">
          <Button size="sm" onClick={onOpen} aria-label={t("loans.detailsAria", { id: l.id })}>{t("loans.details")}</Button>
          <Button size="sm" variant="ghost" onClick={onEdit} aria-label={t("loans.editAria", { id: l.id })}><Pencil className="size-3.5" /></Button>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <Cell label={lease ? t("loans.monthlyRent") : t("loans.monthlyPayment")} v={l.monthly_payment ? <Money v={l.monthly_payment} /> : null} />
        {lease ? <Cell label={t("loans.capitalOwed")} v={t("loans.noneLease")} /> : <Cell label={t("loans.capitalDue")} v={l.remaining_capital ? <><Money v={l.remaining_capital} round /><Badge className="ml-1.5" tone={l.remaining_capital_source === "schedule" ? "info" : "neutral"}>{l.remaining_capital_source === "schedule" ? t("loans.source.schedule") : t("loans.source.declared")}</Badge>{l.remaining_capital_source === "declared" && l.outstanding_stale && <Badge tone="warn" className="ml-1.5">{t("loans.old")}</Badge>}</> : null} sub={l.remaining_capital_source === "declared" && l.outstanding_as_of ? t("loans.asOf", { date: fmtDate(l.outstanding_as_of) }) : s.next_due ? t("loans.nextInstalment", { date: fmtDate(s.next_due, "dayMonth") }) : undefined} />}
        <Cell label={t("loans.ends")} v={l.end_date ? fmtDate(l.end_date, "medium") : s.last_due ? fmtDate(s.last_due, "medium") : null} />
        {lease ? <Cell label={t("loans.optionPrice")} v={l.residual_value ? <Money v={l.residual_value} round /> : null} /> : <Cell label={t("loans.rate")} v={l.rate?.nominal != null ? (l.rate.type ? t("loans.rateWithType", { rate: fmtNumber(l.rate.nominal, 2), type: RATE_TYPE[l.rate.type] ? t(RATE_TYPE[l.rate.type]) : l.rate.type }) : t("loans.rateValue", { rate: fmtNumber(l.rate.nominal, 2) })) : null} />}
        <Cell label={t("loans.debitedFrom")} v={l.debited_account_label ?? l.debited_account} />
        <Cell label={t("loans.lastPayment")} v={l.payments ? <Money v={l.payments.amount} /> : null} sub={l.payments ? (l.payments.next_expected ? t("loans.paymentNext", { date: fmtDate(l.payments.last_date), next: fmtDate(l.payments.next_expected, "dayMonth") }) : fmtDate(l.payments.last_date)) : l.payments_seen ? t("loans.paymentsMatched", { count: l.payments_seen }) : t("loans.noMatchingPayment")} />
      </dl>
      {l.alerts.length > 0 && (
        <ul className="mt-3 grid gap-1.5">{l.alerts.map((a) => <li key={a.id} className="flex gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /><span><strong className="font-medium">{tServerOr(a.title_msg, a.title)}.</strong> {tServerOr(a.body_msg, a.body)}</span></li>)}</ul>
      )}
      {l.lease?.end.reminder_active && <Notice tone="warn" className="mt-3" title={t("loans.lease.endsIn", { count: l.lease.end.days_left })}>{t("loans.lease.decide")}{l.lease.mileage.status === "over_limit" && <> {t("loans.lease.overLimit", { km: fmtNumber(l.lease.mileage.excess_km) })}</>}</Notice>}
      {(l.missing.length > 0 || l.open_questions.length > 0) && (
        <div className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
          {l.missing.length > 0 && <>{t("loans.missing", { fields: missingLabels(l).join(", ") })} </>}
          {l.inferred.length > 0 && <>{t("loans.canSuggest", { fields: l.inferred.map((f) => f.field).join(", ") })} </>}
          {l.open_questions.length > 0 && <Link to="/memory" className="font-medium underline">{t("loans.openQuestions", { count: l.open_questions.length })}</Link>}
        </div>
      )}
    </Card>
  );
}

/** The fields a loan card says are missing, in the interface language (labels.loanField by code; the English label when the server sent no code). */
function missingLabels(l: Liability): string[] {
  return l.missing.map((english, i) => serverLabel("loanField", l.missing_codes?.[i], english));
}

function Cell({ label, v, sub }: { label: string; v: React.ReactNode; sub?: string }) {
  const { t } = useTranslation("wealth");
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 truncate font-medium">{v ?? <span className="rounded bg-warn-soft px-1.5 py-0.5 text-[12px] font-medium text-warn">{t("loans.unknown")}</span>}</dd>
      {sub && <dd className="text-xs text-faint">{sub}</dd>}
    </div>
  );
}
