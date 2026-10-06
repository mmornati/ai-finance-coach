import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Link2, Pencil, Plug, RefreshCw } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, Dot, EmptyState, Field, Input, Money, Notice, PageHeader, ProgressBar, Select, Skeleton, Spinner } from "@/components/ui";
import { SyncButton } from "@/components/Layout";
import { useConnections, useFilters, useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtMoney, groupLabel } from "@/lib/format";
import type { ConnAccount, Consent, HealthBank, JobState, LastRun, Transfers } from "@/api/types";

/** The last scheduled run, from its structured log (step names, durations and counts only: no description, name or amount is ever logged). */
export function LastRunCard({ r }: { r: LastRun | null }) {
  if (!r) return <Card title="Last scheduled run"><p className="text-sm text-muted">No scheduled run has been logged yet. <code>coach schedule run</code> writes it.</p></Card>;
  const tone = r.outcome === "ok" ? (r.warned.length ? "warn" : "pos") : r.outcome === "failed" ? "neg" : "neutral";
  return (
    <Card title="Last scheduled run" subtitle={`${r.started ? fmtDateTime(r.started) : "?"}${r.duration_s !== null ? ` · ${r.duration_s} s` : ""} · run ${r.run}`} action={<Badge tone={tone}>{r.outcome}</Badge>}>
      <ul className="grid gap-1 text-sm sm:grid-cols-2 lg:grid-cols-3" aria-label="Steps of the last run">
        {r.steps.map((s) => (
          <li key={s.step} className="flex items-center gap-2">
            <Dot level={s.status === "error" ? "red" : s.status === "warn" ? "amber" : "green"} />
            <span className="min-w-0 flex-1 truncate">{s.step}</span>
            <span className="text-xs text-faint">{s.status === "skipped" ? "skipped" : `${s.ms} ms`}</span>
          </li>
        ))}
      </ul>
      {r.failed.length > 0 && <Notice tone="neg" className="mt-3">Failed: {r.failed.join(", ")}. See <code>coach logs --run {r.run}</code>.</Notice>}
    </Card>
  );
}

export default function Connections() {
  const q = useConnections();
  const [connect, setConnect] = useState(false);
  const [edit, setEdit] = useState<ConnAccount | null>(null);
  return (
    <>
      <PageHeader title="Connections" subtitle="Your banks, how long each consent lasts, and when each account was last synced. Syncs respect a daily limit per account." actions={<><Button onClick={() => setConnect(true)}><Plug className="size-4" aria-hidden /> Connect a bank</Button><SyncButton /></>} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            {!d.enable_banking_configured && <Notice tone="warn" title="Enable Banking is not configured">Syncing and connecting need [enable_banking] in config.toml. Imported files still work.</Notice>}
            <SyncResult job={d.sync} limit={d.sync.daily_limit} />
            <LastRunCard r={d.health.last_run ?? null} />
            <div className="grid gap-3 lg:grid-cols-2">{d.health.banks.map((b) => <BankCard key={(b.session_id ?? "import") + b.bank} b={b} consent={d.consents.find((c) => c.session_id === b.session_id)} canReconnect={d.enable_banking_configured} />)}</div>
            <Card title="Accounts" pad={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-medium">Account</th><th scope="col" className="px-2 py-2 font-medium">Owner</th><th scope="col" className="px-2 py-2 font-medium">Purpose</th><th scope="col" className="px-2 py-2 text-right font-medium">Transactions</th><th scope="col" className="px-4 py-2 text-right font-medium">Sync today</th><th scope="col" className="px-2 py-2"><span className="sr-only">Edit</span></th></tr></thead>
                  <tbody>
                    {d.accounts.map((a) => (
                      <tr key={a.uid} className="border-t border-border">
                        <td className="px-4 py-2"><div className="font-medium">{a.label ?? a.name}</div><div className="text-xs text-muted">{a.bank ?? "Manual"}{a.iban_last4 ? ` · ${a.iban_last4}` : ""}{a.source !== "api" ? " · imported" : ""}</div>{a.excluded && <Badge tone="warn">left out of analytics</Badge>} {a.needs_review && <Badge tone="warn">needs review</Badge>}</td>
                        <td className="px-2 py-2">{a.owner ?? <span className="text-faint">not set</span>}</td>
                        <td className="px-2 py-2">{a.purpose ? groupLabel(a.purpose) : <span className="text-faint">not set</span>}</td>
                        <td className="num px-2 py-2 text-right">{a.tx_count}</td>
                        <td className="num px-4 py-2 text-right">{a.syncs_left_today === null ? "–" : `${a.syncs_left_today} left`}</td>
                        <td className="px-2 py-2 text-right"><Button size="sm" variant="ghost" aria-label={`Edit ${a.label ?? a.name}`} onClick={() => setEdit(a)}><Pencil className="size-3.5" /></Button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            <TransfersCard />
          </div>
        )}
      </Async>
      {connect && <ConnectDialog onClose={() => setConnect(false)} />}
      {edit && <AccountDialog a={edit} onClose={() => setEdit(null)} />}
    </>
  );
}

function SyncResult({ job, limit }: { job: JobState; limit: number }) {
  if (job.state === "idle") return <p className="text-xs text-faint">Up to {limit} syncs per account per day. Nothing synced from this window yet.</p>;
  const tone = job.state === "failed" ? "neg" : job.state === "running" ? "info" : "pos";
  return (
    <Notice tone={tone} title={job.state === "running" ? "Syncing…" : job.state === "failed" ? "Sync failed" : "Last sync"}>
      {job.message ?? (job.state === "running" ? "Asking your banks for new transactions." : "")}
      {job.results.length > 0 && <ul className="mt-1 text-xs">{job.results.map((r) => <li key={r.uid}>{r.bank ?? r.uid}: {r.status}{r.new ? `, ${r.new} new` : ""}{r.note ? ` (${r.note})` : ""}</li>)}</ul>}
      {job.finished_at && <div className="mt-1 text-xs opacity-80">{fmtDateTime(job.finished_at)}</div>}
    </Notice>
  );
}

function BankCard({ b, consent, canReconnect }: { b: HealthBank; consent?: Consent; canReconnect: boolean }) {
  const [busy, setBusy] = useState(false);
  const [dlg, setDlg] = useState(false);
  const total = 180;
  const left = b.consent_days_left;
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="flex items-center gap-2 text-[15px] font-semibold"><Dot level={b.level} />{b.bank}</h3>
          <p className="text-xs text-muted">{b.consent_status ? `Consent ${b.consent_status}${left !== null ? `, ${left} day${left === 1 ? "" : "s"} left (until ${fmtDate(b.valid_until)})` : ""}` : "Imported files"}</p></div>
        {consent && canReconnect && <Button size="sm" variant={left !== null && left <= 14 ? "primary" : "secondary"} onClick={() => setDlg(true)}><RefreshCw className="size-3.5" aria-hidden /> Reconnect</Button>}
      </div>
      {left !== null && <div className="mt-3"><ProgressBar label="Consent time left" value={Math.max(0, left)} max={total} tone={left <= 3 ? "neg" : left <= 14 ? "warn" : "pos"} /></div>}
      <ul className="mt-3 divide-y divide-border text-sm">
        {b.accounts.map((a) => (
          <li key={a.uid} className="py-2">
            <div className="flex items-center justify-between gap-3"><span className="flex min-w-0 items-center gap-2"><Dot level={a.level} /><span className="truncate">{a.label}</span></span><span className="shrink-0 text-xs text-muted">{a.source === "api" ? (a.last_ok_sync ? `synced ${fmtDateTime(a.last_ok_sync)}` : "never synced") : (a.last_import ? `imported ${fmtDateTime(a.last_import)}` : "no import")}</span></div>
            {a.source === "api" && <div className="ml-4 text-xs text-faint">{a.syncs_left_today} of {a.daily_limit} syncs left today · {a.tx_count} transactions</div>}
            {a.problems.map((p) => <div key={p} className="ml-4 text-xs text-warn">{p.replace(/`[^`]*`/g, "").trim()}</div>)}
          </li>
        ))}
      </ul>
      {dlg && consent && <ConnectDialog onClose={() => setDlg(false)} reconnect={consent} />}
    </Card>
  );
}

function ConnectDialog({ onClose, reconnect }: { onClose: () => void; reconnect?: Consent }) {
  const [country, setCountry] = useState("FR");
  const [q, setQ] = useState("");
  const [bank, setBank] = useState("");
  const [days, setDays] = useState(180);
  const [job, setJob] = useState<JobState | null>(null);
  const banks = useGet<{ banks: { name: string; country: string; max_consent_days: number }[] }>("/banks", { country, q }, { enabled: !reconnect && country.length === 2, staleTime: 300_000 });
  const start = useWrite(() => (reconnect ? api.post<JobState>("/connections/reconnect", { session_id: reconnect.session_id, days }) : api.post<JobState>("/connections/connect", { bank, country, days })), { invalidate: false, onSuccess: setJob });
  useEffect(() => {
    if (!job || job.state !== "running") return;
    const t = setInterval(async () => setJob(await api.get<JobState>("/connections/auth/status")), 1500);
    return () => clearInterval(t);
  }, [job?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  const qc = useQueryClient();
  useEffect(() => { if (job?.state === "done") void qc.invalidateQueries(); }, [job?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open onClose={onClose} title={reconnect ? `Reconnect ${reconnect.bank}` : "Connect a bank"} description="You will log in on your bank's own page; this app never sees your bank password." footer={!job && <><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" busy={start.isPending} disabled={!reconnect && !bank} onClick={() => start.mutate(undefined as never)}>Continue to the bank</Button></>}>
      {!job ? (
        <div className="grid gap-4">
          {!reconnect && (
            <>
              <div className="grid grid-cols-[6rem_1fr] gap-3">
                <Field label="Country">{(id) => <Input id={id} maxLength={2} value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} />}</Field>
                <Field label="Find your bank">{(id) => <Input id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Fortuneo, CIC, Revolut…" />}</Field>
              </div>
              {banks.isFetching ? <Spinner /> : banks.isError ? <Notice tone="neg">{banks.error.message}</Notice> : (
                <ul className="max-h-56 overflow-auto rounded-lg border border-border">
                  {(banks.data?.banks ?? []).slice(0, 40).map((b) => <li key={b.name}><button type="button" onClick={() => setBank(b.name)} className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-surface-2 ${bank === b.name ? "bg-accent-soft text-accent" : ""}`}>{b.name}<span className="text-xs text-muted">up to {b.max_consent_days} days</span></button></li>)}
                </ul>
              )}
            </>
          )}
          <Field label="Consent length (days)" hint="The bank may grant less.">{(id) => <Input id={id} type="number" min={1} max={730} value={days} onChange={(e) => setDays(Number(e.target.value))} />}</Field>
        </div>
      ) : (
        <div className="grid gap-3" aria-live="polite">
          {job.state === "running" && !job.url && <Spinner label="Asking the bank for a login page" />}
          {job.url && job.state === "running" && (
            <>
              <Notice tone="info" title="Log in on your bank's page">Open it, authenticate, and come back: this window finishes by itself. Your browser warns once about the local certificate, accept it.</Notice>
              <a href={job.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg hover:bg-accent-hover"><ExternalLink className="size-4" aria-hidden /> Open the bank login</a>
              <Spinner label="Waiting for the bank to redirect back" />
            </>
          )}
          {job.state === "done" && <Notice tone="pos" title="Connected">Run a sync to pull the history.</Notice>}
          {job.state === "failed" && <Notice tone="neg" title="Not connected">{job.message}</Notice>}
          {job.state !== "running" && <div className="flex justify-end"><Button variant="primary" onClick={onClose}>Close</Button></div>}
        </div>
      )}
      {start.error && <Notice tone="neg" className="mt-3">{start.error.message}</Notice>}
    </Dialog>
  );
}

function AccountDialog({ a, onClose }: { a: ConnAccount; onClose: () => void }) {
  const filters = useFilters();
  const [v, setV] = useState({ label: a.label ?? "", owner: a.owner ?? "", purpose: a.purpose ?? "", exclude: a.excluded });
  const save = useWrite(() => api.patch(`/accounts/${a.uid}`, { label: v.label || undefined, owner: v.owner || undefined, purpose: v.purpose || undefined, exclude: v.exclude, resolve: a.needs_review || undefined }), { success: "Account updated", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Edit account" description={`${a.bank ?? "Manual"}${a.iban_last4 ? ` · ${a.iban_last4}` : ""}`} footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save</Button></>}>
      <div className="grid gap-4">
        <Field label="Name">{(id) => <Input id={id} value={v.label} onChange={(e) => setV({ ...v, label: e.target.value })} />}</Field>
        <Field label="Owner" hint="‘joint’ or a household member id">{(id) => <><Input id={id} list="owners" value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })} /><datalist id="owners">{filters.data?.owners.map((o) => <option key={o} value={o} />)}</datalist></>}</Field>
        <Field label="Used for">{(id) => <Select id={id} value={v.purpose} onChange={(e) => setV({ ...v, purpose: e.target.value })}><option value="">Not set</option>{["main", "cards", "rental", "kids", "savings"].map((p) => <option key={p} value={p}>{groupLabel(p)}</option>)}</Select>}</Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.exclude} onChange={(e) => setV({ ...v, exclude: e.target.checked })} /> Leave this account out of analytics</label>
        {a.needs_review && <Notice tone="warn">Saving keeps this as its own account and clears “needs review”.</Notice>}
        {save.error && <Notice tone="neg">{save.error.message}</Notice>}
      </div>
    </Dialog>
  );
}

function TransfersCard() {
  const q = useGet<Transfers>("/transfers");
  const link = useWrite((p: { out_tx: string; in_tx: string }) => api.post("/transfers/link", p), { success: "Linked as an internal transfer" });
  const unlink = useWrite((id: number) => api.post("/transfers/unlink", { ref: String(id) }), { success: "Unlinked" });
  return (
    <Card title="Internal transfers" subtitle="Money moved between your own accounts is not spending." pad={false}>
      <Async q={q}>
        {(d) => (
          <div className="divide-y divide-border">
            {d.proposals.length === 0 && d.links.length === 0 && <EmptyState title="No transfer to review" />}
            {d.proposals.slice(0, 8).map((p) => (
              <div key={p.debit.tx_key} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm sm:px-5">
                <div className="min-w-0 flex-1"><div className="truncate"><Money v={p.debit.amount} /> from {p.debit.account} to {p.credit.account}</div><div className="truncate text-xs text-muted">{fmtDate(p.debit.date)} / {fmtDate(p.credit.date)} · {p.debit.description}</div></div>
                <Badge tone={p.confidence >= 0.85 ? "pos" : "warn"}>{Math.round(p.confidence * 100)}%{p.topup ? " top-up" : ""}</Badge>
                <Button size="sm" onClick={() => link.mutate({ out_tx: p.debit.tx_key, in_tx: p.credit.tx_key })}><Link2 className="size-3.5" aria-hidden /> Link</Button>
              </div>
            ))}
            {d.links.length > 0 && <div className="px-4 py-2 text-xs text-muted sm:px-5">{d.links.length} linked pair(s)</div>}
            {d.links.slice(-5).reverse().map((l) => (
              <div key={l.id} className="flex items-center gap-3 px-4 py-2.5 text-sm sm:px-5"><span className="min-w-0 flex-1 truncate"><Money v={l.amount} /> {l.out_account} → {l.in_account} <span className="text-xs text-faint">{fmtDate(l.out_date)} · {l.method}</span></span><Button size="sm" variant="ghost" onClick={() => unlink.mutate(l.id)}>Unlink</Button></div>
            ))}
          </div>
        )}
      </Async>
    </Card>
  );
}
