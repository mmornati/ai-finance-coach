import { lazy, Suspense, useState } from "react";
import { Link } from "react-router";
import { AlertTriangle, Camera, Home, Pencil, Plus } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Skeleton, Stat } from "@/components/ui";
import { ItemDialog, Kind } from "@/components/ItemForm";
import { NetWorthChart } from "@/components/charts";
import { useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtNumber, groupLabel } from "@/lib/format";
import type { Asset, Liability, NetWorth, NetWorthPoint, NwCategory } from "@/api/types";

const LoanDialog = lazy(() => import("@/components/LoanDetail"));

const LIAB: Record<string, string> = { mortgage: "Mortgage", car_loan: "Car loan", loa: "Lease with option (LOA)", lld: "Long-term lease", consumer_loan: "Consumer loan", bnpl: "Buy now, pay later" };
const CAT_LABEL: Record<NwCategory | "liabilities", string> = { cash: "Cash", savings: "Savings", investments: "Investments", real_estate: "Real estate", vehicles: "Vehicles", other: "Other", liabilities: "Owed" };

export default function Wealth() {
  const nw = useGet<NetWorth>("/net-worth", { history: true, months: 36 });
  const liab = useGet<{ liabilities: Liability[]; totals: { outstanding_known: string; monthly_payments: string; n_unknown_outstanding: number }; note: string }>("/liabilities");
  const assets = useGet<{ assets: (Asset & Record<string, any>)[] }>("/assets");
  const [edit, setEdit] = useState<{ kind: Kind; id?: string; initial?: Record<string, any> } | null>(null);
  const [open, setOpen] = useState<Liability | null>(null);
  const snapshot = useWrite(() => api.post<{ net_worth: string; backfilled_months: number }>("/net-worth/snapshot", {}), { success: "Snapshot recorded" });
  return (
    <>
      <PageHeader title="Loans & net worth" subtitle="What you own, what you owe, and what is still unknown. Nothing is estimated: a missing figure stays missing." actions={<Button onClick={() => setEdit({ kind: "liabilities" })}><Plus className="size-4" aria-hidden /> Add a loan</Button>} />
      <div className="grid gap-4">
        <Card>
          <Async q={nw} skeleton={<Skeleton className="h-40 w-full" />}>
            {(d) => (
              <div className="grid gap-5 md:grid-cols-[1.1fr_2fr]">
                <div>
                  <Stat big label={d.complete ? "Net worth" : "Net worth, known part only"} value={<Money v={d.net_worth} round />} hint={d.complete ? undefined : `${d.unknown.length} item${d.unknown.length > 1 ? "s" : ""} not counted`} />
                  {!d.complete && <Notice tone="warn" className="mt-3" title="This is not the full picture">{d.note}</Notice>}
                </div>
                <dl className="grid content-start gap-3 text-sm sm:grid-cols-3">
                  <div className="rounded-lg bg-surface-2 p-3"><dt className="text-xs text-muted">Bank balances</dt><dd className="num mt-0.5 text-lg font-semibold"><Money v={d.bank.total} round /></dd><dd className="text-xs text-faint">{d.bank.accounts.length} accounts</dd></div>
                  <div className="rounded-lg bg-surface-2 p-3"><dt className="text-xs text-muted">Manual assets (counted)</dt><dd className="num mt-0.5 text-lg font-semibold"><Money v={d.assets.total} round /></dd><dd className="text-xs text-faint">{d.assets.items.filter((a) => a.counted).length} with a value</dd></div>
                  <div className="rounded-lg bg-surface-2 p-3"><dt className="text-xs text-muted">Owed (known)</dt><dd className="num mt-0.5 text-lg font-semibold">{Number(d.liabilities.total) > 0 && "−"}<Money v={d.liabilities.total} round /></dd><dd className="text-xs text-faint">{d.liabilities.items.filter((l) => l.counted).length} of {d.liabilities.items.filter((l) => !l.excluded).length} loans</dd></div>
                </dl>
                <Breakdown d={d} />
                {d.unknown.length > 0 && (
                  <div className="md:col-span-2">
                    <h3 className="mb-1.5 text-xs font-semibold text-muted">Not counted because unknown</h3>
                    <ul className="flex flex-wrap gap-2">{d.unknown.map((u) => <li key={u.kind + u.id}><Badge tone="warn" title={u.reason}><AlertTriangle className="size-3" aria-hidden />{u.kind}: {u.label}</Badge></li>)}</ul>
                  </div>
                )}
                {d.stale.length > 0 && <p className="text-xs text-faint md:col-span-2">Values to refresh: {d.stale.map((s) => s.id).join(", ")}.</p>}
              </div>
            )}
          </Async>
        </Card>

        <Card title="Net worth over time" subtitle="Recorded after each sync, and rebuilt for past months from the bank balances (bank accounts only), the loan schedules and each manual asset from its own date." action={<Button size="sm" busy={snapshot.isPending} onClick={() => snapshot.mutate(undefined as never)}><Camera className="size-3.5" aria-hidden /> Record today</Button>}>
          <Async q={nw} skeleton={<Skeleton className="h-64 w-full" />}>
            {(d) => <History points={d.history ?? []} />}
          </Async>
        </Card>

        <Async q={liab} skeleton={<Skeleton className="h-48 w-full" />}>
          {(d) =>
            d.liabilities.length === 0 ? (
              <Card><EmptyState icon={<Home className="size-6" />} title="No loan on file" action={<Button variant="primary" onClick={() => setEdit({ kind: "liabilities" })}>Add your first loan</Button>}>Mortgage, car loan or lease: the forecast and the savings rate get more accurate with them.</EmptyState></Card>
            ) : (
              <div className="grid gap-3 lg:grid-cols-2">
                {d.liabilities.map((l) => <LoanCard key={l.id} l={l} onOpen={() => setOpen(l)} onEdit={() => setEdit({ kind: "liabilities", id: l.id, initial: l })} />)}
              </div>
            )
          }
        </Async>

        <Async q={assets} skeleton={<Skeleton className="h-40 w-full" />}>
          {(d) => (
            <Card title="Assets not synced from a bank" action={<Button size="sm" onClick={() => setEdit({ kind: "assets" })}><Plus className="size-3.5" aria-hidden /> Asset</Button>} pad={false}>
              {d.assets.length === 0 ? <EmptyState title="No manual asset">Savings books, employee savings, property or vehicles.</EmptyState> : (
                <ul className="divide-y divide-border">
                  {d.assets.map((a) => (
                    <li key={a.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{a.description ?? a.id}</div>
                        <div className="text-xs text-muted">{groupLabel(a.kind)}{a.provider ? ` · ${a.provider}` : ""}{a.holder ? ` · ${a.holder}` : ""}{a.connected ? " · synced from a bank" : ""}</div>
                      </div>
                      <div className="text-right">
                        {a.unknown_value ? <Badge tone="warn">value unknown</Badge> : <div className="num font-semibold"><Money v={a.value} round /></div>}
                        <div className="text-xs text-faint">{a.as_of ? `as of ${fmtDate(a.as_of)}` : ""}{a.stale && <Badge tone="warn" className="ml-1.5">refresh</Badge>}</div>
                      </div>
                      <Button size="sm" variant="ghost" aria-label={`Edit ${a.id}`} onClick={() => setEdit({ kind: "assets", id: a.id, initial: a })}><Pencil className="size-3.5" /></Button>
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
  if (points.length === 0) return <EmptyState title="No history yet">Press "Record today" to store a snapshot; the past months are rebuilt where the data allow it.</EmptyState>;
  const partial = points.filter((p) => !p.complete).length;
  return (
    <div className="grid gap-2">
      <NetWorthChart points={points} />
      {partial > 0 && <p className="text-xs text-faint">{partial} of {points.length} months are the known part only: lighter bars and hollow dots mark the months where some item had no value that month (hover for which). A manual asset counts only from the date of its recorded value: a dashed line marks the month a value is first counted, where the total jumps without any change in wealth.{points.some((p) => p.caveat) && " Rebuilt months use booked transactions while some balances are available / expected ones, so they can differ slightly from the bank's figure."}</p>}
    </div>
  );
}

function Breakdown({ d }: { d: NetWorth }) {
  const cats = (Object.keys(d.by_category) as (keyof typeof d.by_category)[]).filter((k) => k !== "liabilities" && Number(d.by_category[k]) !== 0);
  const owners = Object.entries(d.by_owner);
  return (
    <div className="grid gap-3 md:col-span-2 sm:grid-cols-2">
      <div>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">By category</h3>
        <ul className="grid gap-1 text-sm">
          {cats.map((k) => <li key={k} className="flex justify-between"><span>{CAT_LABEL[k]}</span><span className="num"><Money v={d.by_category[k]} round /></span></li>)}
          <li className="flex justify-between text-muted"><span>{CAT_LABEL.liabilities}</span><span className="num">−<Money v={d.by_category.liabilities} round /></span></li>
        </ul>
      </div>
      <div>
        <h3 className="mb-1.5 text-xs font-semibold text-muted">By person</h3>
        <ul className="grid gap-1 text-sm">
          {owners.map(([o, v]) => <li key={o} className="flex justify-between"><span>{o === "unassigned" ? "Not assigned to a person" : o}{v.n_unknown > 0 && <span className="text-xs text-faint"> ({v.n_unknown} unknown)</span>}</span><span className="num"><Money v={v.net_worth} round /></span></li>)}
        </ul>
      </div>
    </div>
  );
}

function LoanCard({ l, onOpen, onEdit }: { l: Liability; onOpen: () => void; onEdit: () => void }) {
  const lease = l.kind === "loa" || l.kind === "lld";
  const s = l.schedule;
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold">{l.lender ?? l.id}</h3>
          <p className="text-xs text-muted">{LIAB[l.kind] ?? l.kind}{l.asset ? ` · ${l.asset}` : ""}</p>
        </div>
        <div className="flex gap-1">
          <Button size="sm" onClick={onOpen} aria-label={`Details of loan ${l.id}`}>Details</Button>
          <Button size="sm" variant="ghost" onClick={onEdit} aria-label={`Edit loan ${l.id}`}><Pencil className="size-3.5" /></Button>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <Cell label={lease ? "Monthly rent" : "Monthly payment"} v={l.monthly_payment ? <Money v={l.monthly_payment} /> : null} />
        {lease ? <Cell label="Capital owed" v="none (a lease)" /> : <Cell label="Capital still due" v={l.remaining_capital ? <><Money v={l.remaining_capital} round /><Badge className="ml-1.5" tone={l.remaining_capital_source === "schedule" ? "info" : "neutral"}>{l.remaining_capital_source === "schedule" ? "schedule" : "declared"}</Badge>{l.remaining_capital_source === "declared" && l.outstanding_stale && <Badge tone="warn" className="ml-1.5">old</Badge>}</> : null} sub={l.remaining_capital_source === "declared" && l.outstanding_as_of ? `as of ${fmtDate(l.outstanding_as_of)}` : s.next_due ? `next instalment ${fmtDate(s.next_due, "dayMonth")}` : undefined} />}
        <Cell label="Ends" v={l.end_date ? fmtDate(l.end_date, "medium") : s.last_due ? fmtDate(s.last_due, "medium") : null} />
        {lease ? <Cell label="Option price" v={l.residual_value ? <Money v={l.residual_value} round /> : null} /> : <Cell label="Rate" v={l.rate?.nominal != null ? `${fmtNumber(l.rate.nominal, 2)} %${l.rate.type ? ` ${l.rate.type}` : ""}` : null} />}
        <Cell label="Debited from" v={l.debited_account_label ?? l.debited_account} />
        <Cell label="Last bank payment" v={l.payments ? <Money v={l.payments.amount} /> : null} sub={l.payments ? `${fmtDate(l.payments.last_date)}${l.payments.next_expected ? `, next ${fmtDate(l.payments.next_expected, "dayMonth")}` : ""}` : l.payments_seen ? `${l.payments_seen} payments matched` : "no matching payment found"} />
      </dl>
      {l.alerts.length > 0 && (
        <ul className="mt-3 grid gap-1.5">{l.alerts.map((a) => <li key={a.id} className="flex gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /><span><strong className="font-medium">{a.title}.</strong> {a.body}</span></li>)}</ul>
      )}
      {l.lease?.end.reminder_active && <Notice tone="warn" className="mt-3" title={`Ends in ${l.lease.end.days_left} days`}>Decide between buying the car and returning it.{l.lease.mileage.status === "over_limit" && ` About ${fmtNumber(l.lease.mileage.excess_km)} km over the limit at the current pace.`}</Notice>}
      {(l.missing.length > 0 || l.open_questions.length > 0) && (
        <div className="mt-3 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-warn">
          {l.missing.length > 0 && <>Missing: {l.missing.join(", ")}. </>}
          {l.inferred.length > 0 && <>The coach can suggest {l.inferred.map((f) => f.field).join(", ")} from the payments (Details &gt; Suggestions). </>}
          {l.open_questions.length > 0 && <Link to="/memory" className="font-medium underline">{l.open_questions.length} open question{l.open_questions.length > 1 ? "s" : ""}</Link>}
        </div>
      )}
    </Card>
  );
}

function Cell({ label, v, sub }: { label: string; v: React.ReactNode; sub?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 truncate font-medium">{v ?? <span className="rounded bg-warn-soft px-1.5 py-0.5 text-[12px] font-medium text-warn">unknown</span>}</dd>
      {sub && <dd className="text-xs text-faint">{sub}</dd>}
    </div>
  );
}
