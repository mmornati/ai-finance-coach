import { useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
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
  const { t } = useTranslation("connections");
  if (!r) return <Card title={t("lastRun.title")}><p className="text-sm text-muted"><Trans t={t} i18nKey="lastRun.none" components={{ code: <code /> }} /></p></Card>;
  const tone = r.outcome === "ok" ? (r.warned.length ? "warn" : "pos") : r.outcome === "failed" ? "neg" : "neutral";
  return (
    <Card title={t("lastRun.title")} subtitle={r.duration_s !== null ? t("lastRun.subtitleDuration", { started: r.started ? fmtDateTime(r.started) : t("lastRun.unknownStart"), duration: r.duration_s, run: r.run }) : t("lastRun.subtitle", { started: r.started ? fmtDateTime(r.started) : t("lastRun.unknownStart"), run: r.run })} action={<Badge tone={tone}>{r.outcome}</Badge>}>
      <ul className="grid gap-1 text-sm sm:grid-cols-2 lg:grid-cols-3" aria-label={t("lastRun.steps")}>
        {r.steps.map((s) => (
          <li key={s.step} className="flex items-center gap-2">
            <Dot level={s.status === "error" ? "red" : s.status === "warn" ? "amber" : "green"} />
            <span className="min-w-0 flex-1 truncate">{s.step}</span>
            <span className="text-xs text-faint">{s.status === "skipped" ? t("lastRun.skipped") : t("lastRun.ms", { ms: s.ms })}</span>
          </li>
        ))}
      </ul>
      {r.failed.length > 0 && <Notice tone="neg" className="mt-3"><Trans t={t} i18nKey="lastRun.failed" values={{ steps: r.failed.join(", "), run: r.run }} components={{ code: <code /> }} /></Notice>}
    </Card>
  );
}

export default function Connections() {
  const { t } = useTranslation("connections");
  const q = useConnections();
  const [connect, setConnect] = useState(false);
  const [edit, setEdit] = useState<ConnAccount | null>(null);
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} actions={<><Button onClick={() => setConnect(true)}><Plug className="size-4" aria-hidden /> {t("connect")}</Button><SyncButton /></>} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            {!d.enable_banking_configured && <Notice tone="warn" title={t("notConfiguredTitle")}>{t("notConfiguredBody")}</Notice>}
            <SyncResult job={d.sync} limit={d.sync.daily_limit} />
            <LastRunCard r={d.health.last_run ?? null} />
            <div className="grid gap-3 lg:grid-cols-2">{d.health.banks.map((b) => <BankCard key={(b.session_id ?? "import") + b.bank} b={b} consent={d.consents.find((c) => c.session_id === b.session_id)} canReconnect={d.enable_banking_configured} />)}</div>
            <Card title={t("accounts.title")} pad={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-medium">{t("accounts.account")}</th><th scope="col" className="px-2 py-2 font-medium">{t("accounts.owner")}</th><th scope="col" className="px-2 py-2 font-medium">{t("accounts.purpose")}</th><th scope="col" className="px-2 py-2 text-right font-medium">{t("accounts.transactions")}</th><th scope="col" className="px-4 py-2 text-right font-medium">{t("accounts.syncToday")}</th><th scope="col" className="px-2 py-2"><span className="sr-only">{t("accounts.edit")}</span></th></tr></thead>
                  <tbody>
                    {d.accounts.map((a) => (
                      <tr key={a.uid} className="border-t border-border">
                        <td className="px-4 py-2"><div className="font-medium">{a.label ?? a.name}</div><div className="text-xs text-muted">{a.bank ?? t("accounts.manual")}{a.iban_last4 ? ` · ${a.iban_last4}` : ""}{a.source !== "api" ? t("accounts.imported") : ""}</div>{a.excluded && <Badge tone="warn">{t("accounts.excluded")}</Badge>} {a.needs_review && <Badge tone="warn">{t("accounts.needsReview")}</Badge>}</td>
                        <td className="px-2 py-2">{a.owner ?? <span className="text-faint">{t("accounts.notSet")}</span>}</td>
                        <td className="px-2 py-2">{a.purpose ? groupLabel(a.purpose) : <span className="text-faint">{t("accounts.notSet")}</span>}</td>
                        <td className="num px-2 py-2 text-right">{a.tx_count}</td>
                        <td className="num px-4 py-2 text-right">{a.syncs_left_today === null ? "–" : t("accounts.syncsLeft", { count: a.syncs_left_today })}</td>
                        <td className="px-2 py-2 text-right"><Button size="sm" variant="ghost" aria-label={t("accounts.editAria", { name: a.label ?? a.name })} onClick={() => setEdit(a)}><Pencil className="size-3.5" /></Button></td>
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
  const { t } = useTranslation("connections");
  if (job.state === "idle") return <p className="text-xs text-faint">{t("sync.idle", { limit })}</p>;
  const tone = job.state === "failed" ? "neg" : job.state === "running" ? "info" : "pos";
  return (
    <Notice tone={tone} title={job.state === "running" ? t("sync.running") : job.state === "failed" ? t("sync.failed") : t("sync.last")}>
      {job.message ?? (job.state === "running" ? t("sync.asking") : "")}
      {job.results.length > 0 && <ul className="mt-1 text-xs">{job.results.map((r) => <li key={r.uid}>{r.new ? t("sync.resultNew", { bank: r.bank ?? r.uid, status: r.status, count: r.new }) : t("sync.result", { bank: r.bank ?? r.uid, status: r.status })}{r.note ? t("sync.note", { note: r.note }) : ""}</li>)}</ul>}
      {job.finished_at && <div className="mt-1 text-xs opacity-80">{fmtDateTime(job.finished_at)}</div>}
    </Notice>
  );
}

function BankCard({ b, consent, canReconnect }: { b: HealthBank; consent?: Consent; canReconnect: boolean }) {
  const { t } = useTranslation("connections");
  const [busy, setBusy] = useState(false);
  const [dlg, setDlg] = useState(false);
  const total = 180;
  const left = b.consent_days_left;
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><h3 className="flex items-center gap-2 text-[15px] font-semibold"><Dot level={b.level} />{b.bank}</h3>
          <p className="text-xs text-muted">{b.consent_status ? (left !== null ? t("bank.consentLeft", { status: b.consent_status, count: left, date: fmtDate(b.valid_until) }) : t("bank.consent", { status: b.consent_status })) : t("bank.importedFiles")}</p></div>
        {consent && canReconnect && <Button size="sm" variant={left !== null && left <= 14 ? "primary" : "secondary"} onClick={() => setDlg(true)}><RefreshCw className="size-3.5" aria-hidden /> {t("bank.reconnect")}</Button>}
      </div>
      {left !== null && <div className="mt-3"><ProgressBar label={t("bank.timeLeft")} value={Math.max(0, left)} max={total} tone={left <= 3 ? "neg" : left <= 14 ? "warn" : "pos"} /></div>}
      <ul className="mt-3 divide-y divide-border text-sm">
        {b.accounts.map((a) => (
          <li key={a.uid} className="py-2">
            <div className="flex items-center justify-between gap-3"><span className="flex min-w-0 items-center gap-2"><Dot level={a.level} /><span className="truncate">{a.label}</span></span><span className="shrink-0 text-xs text-muted">{a.source === "api" ? (a.last_ok_sync ? t("bank.synced", { date: fmtDateTime(a.last_ok_sync) }) : t("bank.neverSynced")) : (a.last_import ? t("bank.importedOn", { date: fmtDateTime(a.last_import) }) : t("bank.noImport"))}</span></div>
            {a.source === "api" && <div className="ml-4 text-xs text-faint">{t("bank.syncsLeft", { left: a.syncs_left_today, limit: a.daily_limit, count: a.tx_count })}</div>}
            {a.problems.map((p) => <div key={p} className="ml-4 text-xs text-warn">{p.replace(/`[^`]*`/g, "").trim()}</div>)}
          </li>
        ))}
      </ul>
      {dlg && consent && <ConnectDialog onClose={() => setDlg(false)} reconnect={consent} />}
    </Card>
  );
}

function ConnectDialog({ onClose, reconnect }: { onClose: () => void; reconnect?: Consent }) {
  const { t } = useTranslation("connections");
  const [country, setCountry] = useState("FR");
  const [q, setQ] = useState("");
  const [bank, setBank] = useState("");
  const [days, setDays] = useState(180);
  const [job, setJob] = useState<JobState | null>(null);
  const banks = useGet<{ banks: { name: string; country: string; max_consent_days: number }[] }>("/banks", { country, q }, { enabled: !reconnect && country.length === 2, staleTime: 300_000 });
  const start = useWrite(() => (reconnect ? api.post<JobState>("/connections/reconnect", { session_id: reconnect.session_id, days }) : api.post<JobState>("/connections/connect", { bank, country, days })), { invalidate: false, onSuccess: setJob });
  useEffect(() => {
    if (!job || job.state !== "running") return;
    const timer = setInterval(async () => setJob(await api.get<JobState>("/connections/auth/status")), 1500);
    return () => clearInterval(timer);
  }, [job?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  const qc = useQueryClient();
  useEffect(() => { if (job?.state === "done") void qc.invalidateQueries(); }, [job?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open onClose={onClose} title={reconnect ? t("connectDialog.reconnectTitle", { bank: reconnect.bank }) : t("connectDialog.title")} description={t("connectDialog.description")} footer={!job && <><Button variant="ghost" onClick={onClose}>{t("connectDialog.cancel")}</Button><Button variant="primary" busy={start.isPending} disabled={!reconnect && !bank} onClick={() => start.mutate(undefined as never)}>{t("connectDialog.continue")}</Button></>}>
      {!job ? (
        <div className="grid gap-4">
          {!reconnect && (
            <>
              <div className="grid grid-cols-[6rem_1fr] gap-3">
                <Field label={t("connectDialog.country")}>{(id) => <Input id={id} maxLength={2} value={country} onChange={(e) => setCountry(e.target.value.toUpperCase())} />}</Field>
                <Field label={t("connectDialog.findBank")}>{(id) => <Input id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("connectDialog.findPlaceholder")} />}</Field>
              </div>
              {banks.isFetching ? <Spinner /> : banks.isError ? <Notice tone="neg">{banks.error.message}</Notice> : (
                <ul className="max-h-56 overflow-auto rounded-lg border border-border">
                  {(banks.data?.banks ?? []).slice(0, 40).map((b) => <li key={b.name}><button type="button" onClick={() => setBank(b.name)} className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-surface-2 ${bank === b.name ? "bg-accent-soft text-accent" : ""}`}>{b.name}<span className="text-xs text-muted">{t("connectDialog.upTo", { count: b.max_consent_days })}</span></button></li>)}
                </ul>
              )}
            </>
          )}
          <Field label={t("connectDialog.length")} hint={t("connectDialog.lengthHint")}>{(id) => <Input id={id} type="number" min={1} max={730} value={days} onChange={(e) => setDays(Number(e.target.value))} />}</Field>
        </div>
      ) : (
        <div className="grid gap-3" aria-live="polite">
          {job.state === "running" && !job.url && <Spinner label={t("connectDialog.askingLogin")} />}
          {job.url && job.state === "running" && (
            <>
              <Notice tone="info" title={t("connectDialog.loginTitle")}>{t("connectDialog.loginBody")}</Notice>
              <a href={job.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg hover:bg-accent-hover"><ExternalLink className="size-4" aria-hidden /> {t("connectDialog.openLogin")}</a>
              <Spinner label={t("connectDialog.waiting")} />
            </>
          )}
          {job.state === "done" && <Notice tone="pos" title={t("connectDialog.connected")}>{t("connectDialog.connectedBody")}</Notice>}
          {job.state === "failed" && <Notice tone="neg" title={t("connectDialog.notConnected")}>{job.message}</Notice>}
          {job.state !== "running" && <div className="flex justify-end"><Button variant="primary" onClick={onClose}>{t("connectDialog.close")}</Button></div>}
        </div>
      )}
      {start.error && <Notice tone="neg" className="mt-3">{start.error.message}</Notice>}
    </Dialog>
  );
}

function AccountDialog({ a, onClose }: { a: ConnAccount; onClose: () => void }) {
  const { t } = useTranslation("connections");
  const filters = useFilters();
  const [v, setV] = useState({ label: a.label ?? "", owner: a.owner ?? "", purpose: a.purpose ?? "", exclude: a.excluded });
  const save = useWrite(() => api.patch(`/accounts/${a.uid}`, { label: v.label || undefined, owner: v.owner || undefined, purpose: v.purpose || undefined, exclude: v.exclude, resolve: a.needs_review || undefined }), { success: t("accountDialog.updated"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("accountDialog.title")} description={`${a.bank ?? t("accounts.manual")}${a.iban_last4 ? ` · ${a.iban_last4}` : ""}`} footer={<><Button variant="ghost" onClick={onClose}>{t("accountDialog.cancel")}</Button><Button variant="primary" busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("accountDialog.save")}</Button></>}>
      <div className="grid gap-4">
        <Field label={t("accountDialog.name")}>{(id) => <Input id={id} value={v.label} onChange={(e) => setV({ ...v, label: e.target.value })} />}</Field>
        <Field label={t("accountDialog.owner")} hint={t("accountDialog.ownerHint")}>{(id) => <><Input id={id} list="owners" value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })} /><datalist id="owners">{filters.data?.owners.map((o) => <option key={o} value={o} />)}</datalist></>}</Field>
        <Field label={t("accountDialog.usedFor")}>{(id) => <Select id={id} value={v.purpose} onChange={(e) => setV({ ...v, purpose: e.target.value })}><option value="">{t("accountDialog.notSet")}</option>{["main", "cards", "rental", "kids", "savings"].map((p) => <option key={p} value={p}>{groupLabel(p)}</option>)}</Select>}</Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.exclude} onChange={(e) => setV({ ...v, exclude: e.target.checked })} /> {t("accountDialog.exclude")}</label>
        {a.needs_review && <Notice tone="warn">{t("accountDialog.needsReview")}</Notice>}
        {save.error && <Notice tone="neg">{save.error.message}</Notice>}
      </div>
    </Dialog>
  );
}

function TransfersCard() {
  const { t } = useTranslation("connections");
  const q = useGet<Transfers>("/transfers");
  const link = useWrite((p: { out_tx: string; in_tx: string }) => api.post("/transfers/link", p), { success: t("transfers.linked") });
  const unlink = useWrite((id: number) => api.post("/transfers/unlink", { ref: String(id) }), { success: t("transfers.unlinked") });
  return (
    <Card title={t("transfers.title")} subtitle={t("transfers.subtitle")} pad={false}>
      <Async q={q}>
        {(d) => (
          <div className="divide-y divide-border">
            {d.proposals.length === 0 && d.links.length === 0 && <EmptyState title={t("transfers.empty")} />}
            {d.proposals.slice(0, 8).map((p) => (
              <div key={p.debit.tx_key} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm sm:px-5">
                <div className="min-w-0 flex-1"><div className="truncate"><Trans t={t} i18nKey="transfers.fromTo" values={{ from: p.debit.account, to: p.credit.account }} components={{ amount: <Money v={p.debit.amount} /> }} /></div><div className="truncate text-xs text-muted">{fmtDate(p.debit.date)} / {fmtDate(p.credit.date)} · {p.debit.description}</div></div>
                <Badge tone={p.confidence >= 0.85 ? "pos" : "warn"}>{t(p.topup ? "transfers.confidenceTopUp" : "transfers.confidence", { pct: Math.round(p.confidence * 100) })}</Badge>
                <Button size="sm" onClick={() => link.mutate({ out_tx: p.debit.tx_key, in_tx: p.credit.tx_key })}><Link2 className="size-3.5" aria-hidden /> {t("transfers.link")}</Button>
              </div>
            ))}
            {d.links.length > 0 && <div className="px-4 py-2 text-xs text-muted sm:px-5">{t("transfers.linkedPairs", { count: d.links.length })}</div>}
            {d.links.slice(-5).reverse().map((l) => (
              <div key={l.id} className="flex items-center gap-3 px-4 py-2.5 text-sm sm:px-5"><span className="min-w-0 flex-1 truncate"><Money v={l.amount} /> {l.out_account} → {l.in_account} <span className="text-xs text-faint">{fmtDate(l.out_date)} · {l.method}</span></span><Button size="sm" variant="ghost" onClick={() => unlink.mutate(l.id)}>{t("transfers.unlink")}</Button></div>
            ))}
          </div>
        )}
      </Async>
    </Card>
  );
}
