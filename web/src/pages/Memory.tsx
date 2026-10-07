import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Link, useSearchParams } from "react-router";
import { Check, Pencil, Plus, RotateCcw, ShieldAlert, Trash2, X } from "lucide-react";
import { Async, Badge, Button, Card, Chips, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, Select, Skeleton, Spinner, Tabs, Textarea } from "@/components/ui";
import { ItemDialog, Kind } from "@/components/ItemForm";
import { CopyCommand } from "@/components/CopyCommand";
import { useDryRun, useGet, useProposals, useQuestions, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { errorText, holdingKindLabel, serverLabel, tServer, useServerText } from "@/i18n/server";
import { fmtDate, fmtDateTime, fmtMoney } from "@/lib/format";
import type { Annotation, Asset, Change, CheckIssue, EditResult, EventMeta, Liability, MemoryOverview, Member, Proposal, Question } from "@/api/types";
import { cn } from "@/lib/utils";

type Tab = "questions" | "proposals" | "household" | "events" | "items" | "annotations" | "check" | "history";
const EVENT_STATUS: Record<string, ParseKeys<"memory">> = { planned: "events.status.planned", ongoing: "events.status.ongoing", done: "events.status.done" };

export default function Memory() {
  const { t } = useTranslation("memory");
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "questions";
  const ov = useGet<MemoryOverview>("/memory/overview");
  const props = useProposals();
  const nProps = props.data?.proposals.length ?? 0;
  const nQ = ov.data?.counts.open_questions ?? 0;
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <Tabs label={t("tabs.label")} value={tab} onChange={(v) => setSp({ tab: v })} tabs={[
        { value: "questions", label: t("tabs.questions"), badge: nQ ? <Badge tone="info">{nQ}</Badge> : null },
        { value: "proposals", label: t("tabs.proposals"), badge: nProps ? <Badge tone="warn">{nProps}</Badge> : null },
        { value: "household", label: t("tabs.household") },
        { value: "events", label: t("tabs.events") },
        { value: "items", label: t("tabs.items") },
        { value: "annotations", label: t("tabs.annotations") },
        { value: "check", label: t("tabs.check"), badge: ov.data && ov.data.check.errors ? <Badge tone="neg">{ov.data.check.errors}</Badge> : null },
        { value: "history", label: t("tabs.history") },
      ]} />
      {tab === "questions" && <QuestionsTab />}
      {tab === "proposals" && <ProposalsTab />}
      {tab === "household" && <HouseholdTab />}
      {tab === "events" && <EventsTab />}
      {tab === "items" && <ItemsTab />}
      {tab === "annotations" && <AnnotationsTab />}
      {tab === "check" && <CheckTab />}
      {tab === "history" && <HistoryTab />}
    </>
  );
}

/* ------------------------------------------------------------------ questions */
function QuestionsTab() {
  const { t } = useTranslation("memory");
  const [status, setStatus] = useState("open");
  const q = useQuestions(status);
  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <Select aria-label={t("questions.show")} value={status} onChange={(e) => setStatus(e.target.value)} className="!w-auto">
          <option value="open">{t("questions.filter.open")}</option><option value="answered">{t("questions.filter.answered")}</option><option value="dismissed">{t("questions.filter.dismissed")}</option><option value="all">{t("questions.filter.all")}</option>
        </Select>
        <p className="text-xs text-faint">{t("questions.hint")}</p>
      </div>
      <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
        {(d) => d.questions.length === 0 ? <Card><EmptyState title={t("questions.emptyTitle")}>{t("questions.emptyBody")}</EmptyState></Card> : (
          <ul className="grid gap-3">{d.questions.map((x) => <li key={x.id}><QuestionCard x={x} /></li>)}</ul>
        )}
      </Async>
    </div>
  );
}

function QuestionCard({ x }: { x: Question }) {
  const { t } = useTranslation("memory");
  useServerText();
  const [answer, setAnswer] = useState("");
  const [open, setOpen] = useState(false);
  const [why, setWhy] = useState("");
  const ans = useWrite(() => api.post(`/questions/${x.id}/answer`, { text: answer }), { success: t("questions.answerRecorded"), onSuccess: () => setOpen(false) });
  const dis = useWrite(() => api.post(`/questions/${x.id}/dismiss`, { reason: why || undefined }), { success: t("questions.dismissedToast") });
  const reopen = useWrite(() => api.post(`/questions/${x.id}/reopen`, {}), { success: t("questions.reopened") });
  const ev = Object.entries(x.evidence ?? {}).filter(([, v]) => v !== null && v !== "");
  const target = x.suggested_target?.file;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2"><Badge>{serverLabel("questionTopic", x.topic_code, x.topic)}</Badge>{x.stake && <Badge tone="warn">{t("questions.atStake", { amount: fmtMoney(x.stake, { round: true }) })}</Badge>}{x.status !== "open" && <Badge tone={x.status === "answered" ? "pos" : "neutral"}>{t(`questions.status.${x.status}`)}</Badge>}</div>
      <p className="mt-2 text-[15px] font-medium">{tServer(x.question_msg, x.question)}</p>
      {x.context && <p className="mt-1 text-[13px] text-muted">{tServer(x.context_msg, x.context)}</p>}
      {ev.length > 0 && <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">{ev.slice(0, 6).map(([k, v]) => <div key={k}><dt className="inline">{k.replace(/_/g, " ")}: </dt><dd className="inline font-medium text-text">{Array.isArray(v) ? v.join(", ") : String(v)}</dd></div>)}</dl>}
      {x.answer && <p className="mt-2 rounded-lg bg-pos-soft px-3 py-2 text-[13px] text-pos">{x.answer}</p>}
      {x.note && <p className="mt-2 text-xs text-muted">{t("questions.dismissedNote", { note: x.note })}</p>}
      {x.status === "open" && !open && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" onClick={() => setOpen(true)}>{t("questions.answer")}</Button>
          {target?.startsWith("liabilities/") && <Link to="/wealth" className="text-sm text-accent hover:underline">{t("questions.fillLoan")}</Link>}
          {target === "categorization.yaml" && <Link to="/transactions" className="text-sm text-accent hover:underline">{t("questions.openTransactions")}</Link>}
          <span className="ml-auto flex items-center gap-1.5"><Input aria-label={t("questions.reason")} placeholder={t("questions.reason")} value={why} onChange={(e) => setWhy(e.target.value)} className="!min-h-8 w-40 !text-[13px]" /><Button size="sm" variant="ghost" busy={dis.isPending} onClick={() => dis.mutate(undefined as never)}><X className="size-3.5" aria-hidden /> {t("questions.dismiss")}</Button></span>
        </div>
      )}
      {open && (
        <div className="mt-3 grid gap-2">
          <Textarea aria-label={t("questions.yourAnswer")} autoFocus value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={t("questions.answerPlaceholder")} />
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setOpen(false)}>{t("questions.cancel")}</Button><Button variant="primary" disabled={!answer.trim()} busy={ans.isPending} onClick={() => ans.mutate(undefined as never)}>{t("questions.record")}</Button></div>
        </div>
      )}
      {x.status !== "open" && <div className="mt-3"><Button size="sm" busy={reopen.isPending} onClick={() => reopen.mutate(undefined as never)}><RotateCcw className="size-3.5" aria-hidden /> {t("questions.reopen")}</Button></div>}
    </Card>
  );
}

/* ------------------------------------------------------------------ proposals */
function ProposalsTab() {
  const { t } = useTranslation("memory");
  const q = useProposals();
  const [rej, setRej] = useState<Proposal | null>(null);
  return (
    <div className="grid gap-4">
      <Notice title={t("proposals.terminalTitle")}>
        <Trans t={t} i18nKey="proposals.terminalBody" components={{ b: <b /> }} />
      </Notice>
      <Async q={q} skeleton={<Skeleton className="h-48 w-full" />}>
        {(d) => d.proposals.length === 0 ? <Card><EmptyState title={t("proposals.empty")} /></Card> : (
          <ul className="grid gap-3">
            {d.proposals.map((p) => (
              <li key={p.id}>
                <Card>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><Badge tone="info">{p.source}</Badge><span>{fmtDateTime(p.created)}</span><span>→ {p.file}</span>{!p.sealed && <Badge tone="neg"><ShieldAlert className="size-3" aria-hidden /> {t("proposals.modified")}</Badge>}</div>
                  <p className="mt-2 font-medium">{p.reason}</p>
                  <ul className="mt-2 grid gap-1 text-[13px]">
                    {p.changes.map((c, i) => <li key={i} className="rounded bg-surface-2 px-2 py-1"><code className="text-xs">{c.op} {c.path}</code>{c.op === "set" && <> : {JSON.stringify(c.old)} → <b>{JSON.stringify(c.new)}</b></>}{c.snippet && <div className="text-xs text-faint">{t("proposals.source", { snippet: c.snippet })}</div>}{c.suspicious && <Badge tone="neg" className="ml-2">{t("proposals.instructionLike")}</Badge>}</li>)}
                  </ul>
                  <div className="mt-3"><DiffView diff={p.diff} empty={t("proposals.noChange")} /></div>
                  {p.suspicious_paths.length > 0 && <Notice tone="neg" className="mt-2" title={t("proposals.suspiciousTitle")}>{t("proposals.suspiciousBody", { paths: p.suspicious_paths.join(", ") })}</Notice>}
                  {!p.applicable && <Notice tone="warn" className="mt-2">{t("proposals.notApplicable", { error: p.error })}</Notice>}
                  <div className="mt-3 grid gap-1.5">
                    <div className="text-xs font-medium text-muted">{t("proposals.toAccept")}</div>
                    <CopyCommand command={p.accept_command} />
                  </div>
                  <div className="mt-3 flex justify-end"><Button variant="ghost" onClick={() => setRej(p)}>{t("proposals.reject")}</Button></div>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </Async>
      {rej && <RejectDialog p={rej} onClose={() => setRej(null)} />}
    </div>
  );
}

export function RejectDialog({ p, onClose }: { p: Proposal; onClose: () => void }) {
  const { t } = useTranslation("memory");
  const [note, setNote] = useState("");
  const reject = useWrite(() => api.post(`/proposals/${p.id}/reject`, { note: note || undefined }), { success: t("reject.done"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("reject.title")} description={p.reason} size="sm"
      footer={<><Button variant="ghost" onClick={onClose}>{t("reject.keep")}</Button><Button variant="danger" busy={reject.isPending} onClick={() => reject.mutate(undefined as never)}>{t("reject.confirm")}</Button></>}>
      <p className="mb-3 text-sm text-muted">{t("reject.body")}</p>
      <Field label={t("reject.why")}>{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ household / events */
function HouseholdTab() {
  const { t } = useTranslation("memory");
  const q = useGet<{ members: Member[] }>("/household");
  const [edit, setEdit] = useState<{ id?: string; initial?: Record<string, any> } | null>(null);
  return (
    <Card title={t("household.title")} subtitle={t("household.subtitle")} action={<Button size="sm" onClick={() => setEdit({})}><Plus className="size-3.5" aria-hidden /> {t("household.add")}</Button>}>
      <Async q={q}>
        {(d) => d.members.length === 0 ? <EmptyState title={t("household.empty")} /> : (
          <ul className="divide-y divide-border">{d.members.map((m) => <li key={m.id} className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="font-medium">{m.name} <Badge>{t(`household.role.${m.role}`)}</Badge></div><div className="text-xs text-muted">{m.birth_year ? t("household.born", { year: m.birth_year }) : <span className="text-warn">{t("household.birthUnknown")}</span>}{m.aliases.length ? t("household.seenAs", { aliases: m.aliases.join(", ") }) : ""}</div></div><Button size="sm" variant="ghost" aria-label={t("household.edit", { name: m.name })} onClick={() => setEdit({ id: m.id, initial: m })}><Pencil className="size-3.5" /></Button></li>)}</ul>
        )}
      </Async>
      {edit && <ItemDialog kind="members" {...edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

function EventsTab() {
  const { t } = useTranslation("memory");
  const q = useGet<{ events: EventMeta[] }>("/events");
  const [edit, setEdit] = useState<{ id?: string; initial?: Record<string, any> } | null>(null);
  return (
    <Card title={t("events.title")} subtitle={t("events.subtitle")} action={<Button size="sm" onClick={() => setEdit({})}><Plus className="size-3.5" aria-hidden /> {t("events.add")}</Button>}>
      <Async q={q}>
        {(d) => d.events.length === 0 ? <EmptyState title={t("events.empty")} /> : (
          <ul className="divide-y divide-border">{d.events.map((e) => <li key={e.id} className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="font-medium">{e.title ?? e.id} <Badge>{e.status ? (EVENT_STATUS[e.status] ? t(EVENT_STATUS[e.status]) : e.status) : t("events.noStatus")}</Badge></div><div className="text-xs text-muted">{e.start ? `${fmtDate(e.start)}${e.end ? ` → ${fmtDate(e.end)}` : ""}` : t("events.noDates")}{e.budget ? t("events.budget", { amount: fmtMoney(e.budget, { round: true }) }) : ""}</div></div><Link className="text-sm text-accent hover:underline" to={`/transactions?event=${e.id}`}>{t("events.transactions")}</Link>{e.source === "events.yaml" && <Button size="sm" variant="ghost" aria-label={t("events.edit", { name: e.id })} onClick={() => setEdit({ id: e.id, initial: e })}><Pencil className="size-3.5" /></Button>}</li>)}</ul>
        )}
      </Async>
      {edit && <ItemDialog kind="events" {...edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

/* ------------------------------------------------------------------ loans, contracts, assets */
function ItemsTab() {
  const { t } = useTranslation("memory");
  const liab = useGet<{ liabilities: Liability[] }>("/liabilities");
  const con = useGet<{ contracts: Record<string, any>[] }>("/contracts");
  const ast = useGet<{ assets: (Asset & Record<string, any>)[] }>("/assets");
  const [edit, setEdit] = useState<{ kind: Kind; id?: string; initial?: Record<string, any> } | null>(null);
  const Row = ({ title, sub, onEdit, warn }: { title: string; sub: string; onEdit: () => void; warn?: string }) => (
    <li className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="truncate font-medium">{title}</div><div className="truncate text-xs text-muted">{sub}</div>{warn && <div className="text-xs text-warn">{warn}</div>}</div><Button size="sm" variant="ghost" aria-label={t("items.edit", { name: title })} onClick={onEdit}><Pencil className="size-3.5" /></Button></li>
  );
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title={t("items.loans")} action={<Button size="sm" onClick={() => setEdit({ kind: "liabilities" })}><Plus className="size-3.5" aria-hidden /> {t("items.addLoan")}</Button>}>
        <Async q={liab}>{(d) => d.liabilities.length ? <ul className="divide-y divide-border">{d.liabilities.map((l) => <Row key={l.id} title={l.lender ?? l.id} sub={`${holdingKindLabel(l.kind)}${l.monthly_payment ? t("items.perMonth", { amount: fmtMoney(l.monthly_payment) }) : ""}`} warn={l.missing.length ? t("items.missing", { fields: l.missing.join(", ") }) : undefined} onEdit={() => setEdit({ kind: "liabilities", id: l.id, initial: l })} />)}</ul> : <EmptyState title={t("items.noLoan")} />}</Async>
      </Card>
      <Card title={t("items.contracts")} action={<Button size="sm" onClick={() => setEdit({ kind: "contracts" })}><Plus className="size-3.5" aria-hidden /> {t("items.addContract")}</Button>}>
        <Async q={con}>{(d) => d.contracts.length ? <ul className="divide-y divide-border">{d.contracts.map((c) => <Row key={c.id} title={c.provider ?? c.id} sub={`${holdingKindLabel(c.kind ?? "other")}${c.billing?.amount ? ` · ${fmtMoney(c.billing.amount)}` : ""}${c.renewal ? t("items.renews", { date: fmtDate(c.renewal) }) : ""}`} onEdit={() => setEdit({ kind: "contracts", id: c.id, initial: c })} />)}</ul> : <EmptyState title={t("items.noContract")}>{t("items.noContractBody")}</EmptyState>}</Async>
      </Card>
      <Card title={t("items.assets")} className="lg:col-span-2" action={<Button size="sm" onClick={() => setEdit({ kind: "assets" })}><Plus className="size-3.5" aria-hidden /> {t("items.addAsset")}</Button>}>
        <Async q={ast}>{(d) => d.assets.length ? <ul className="divide-y divide-border">{d.assets.map((a) => <Row key={a.id} title={a.description ?? a.id} sub={`${holdingKindLabel(a.kind)}${a.value ? ` · ${fmtMoney(a.value, { round: true })}` : ""}${a.as_of ? t("items.asOf", { date: fmtDate(a.as_of) }) : ""}`} warn={a.unknown_value ? t("items.valueUnknown") : a.stale ? t("items.valueOld") : undefined} onEdit={() => setEdit({ kind: "assets", id: a.id, initial: a })} />)}</ul> : <EmptyState title={t("items.noAsset")} />}</Async>
      </Card>
      {edit && <ItemDialog {...edit} onClose={() => setEdit(null)} />}
    </div>
  );
}

/* ------------------------------------------------------------------ annotations */
function AnnotationsTab() {
  const { t } = useTranslation("memory");
  const q = useGet<{ annotations: Annotation[] }>("/annotations");
  const [del, setDel] = useState<Annotation | null>(null);
  return (
    <Card title={t("annotations.title")} subtitle={t("annotations.subtitle")} pad={false}>
      <Async q={q}>{(d) => d.annotations.length === 0 ? <EmptyState title={t("annotations.emptyTitle")}>{t("annotations.emptyBody")}</EmptyState> : (
        <ul className="divide-y divide-border">{d.annotations.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5">
            <div className="min-w-0 flex-1">
              <div className="font-medium"><code className="text-[13px]">{a.id}</code></div>
              <div className="mt-1 flex flex-wrap gap-1.5">{a.category && <Badge tone="info">{a.category}</Badge>}<Chips items={a.tags ?? []} tone="warn" />{a.event && <Badge>{a.event}</Badge>}</div>
              <div className="mt-1 text-xs text-muted">{Object.entries(a.match).map(([k, v]) => `${k}: ${Array.isArray(v) ? t("annotations.transactions", { count: v.length }) : v}`).join(" · ")}</div>
              {a.note && <div className="text-xs text-faint">{a.note}</div>}
            </div>
            <div className="text-right text-xs text-muted"><div className="num">{t("annotations.decided", { count: a.applies_to })}</div><div className="num">{t("annotations.matched", { count: a.matched })} <Money v={a.total} round /></div></div>
            <Button size="sm" variant="ghost" aria-label={t("annotations.remove", { id: a.id })} onClick={() => setDel(a)}><Trash2 className="size-3.5" /></Button>
          </li>))}</ul>)}
      </Async>
      {del && <DeleteAnnotation a={del} onClose={() => setDel(null)} />}
    </Card>
  );
}

function DeleteAnnotation({ a, onClose }: { a: Annotation; onClose: () => void }) {
  const { t } = useTranslation("memory");
  const pv = useDryRun<EditResult>(`/annotations/${a.id}/delete`, {}, true);
  const del = useWrite(() => api.post<EditResult>(`/annotations/${a.id}/delete`, {}, { dry_run: false }), { success: t("annotations.removed"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("annotations.removeTitle", { id: a.id })} description={t("annotations.follow", { count: a.applies_to })} footer={<><Button variant="ghost" onClick={onClose}>{t("annotations.keep")}</Button><Button variant="danger" busy={del.isPending} disabled={!pv.data} onClick={() => del.mutate(undefined as never)}>{t("annotations.confirm")}</Button></>}>
      {pv.loading ? <Spinner /> : pv.data && <DiffView diff={pv.data.diff} />}
    </Dialog>
  );
}

/* ------------------------------------------------------------------ check / history */
function CheckTab() {
  const { t } = useTranslation("memory");
  const q = useGet<{ summary: { errors: number; warnings: number; info: number }; issues: CheckIssue[] }>("/memory/check");
  const { tServer } = useServerText();
  return (
    <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
      {(d) => (
        <div className="grid gap-4">
          <div className="flex gap-2"><Badge tone={d.summary.errors ? "neg" : "pos"}>{t("check.errors", { count: d.summary.errors })}</Badge><Badge tone={d.summary.warnings ? "warn" : "neutral"}>{t("check.warnings", { count: d.summary.warnings })}</Badge><Badge>{t("check.notes", { count: d.summary.info })}</Badge></div>
          {d.issues.length === 0 ? <Card><EmptyState icon={<Check className="size-6" />} title={t("check.consistent")} /></Card> : (
            <Card pad={false}><ul className="divide-y divide-border">{d.issues.map((i, k) => <li key={k} className="flex gap-3 px-4 py-2.5 text-sm sm:px-5"><Badge tone={i.level === "error" ? "neg" : i.level === "warning" ? "warn" : "neutral"}>{t(`check.level.${i.level}`)}</Badge><div className="min-w-0"><div>{tServer(i.message_msg, i.message)}</div><div className="truncate text-xs text-faint">{i.file}{i.path ? ` · ${i.path}` : ""}{i.line ? t("check.line", { line: i.line }) : ""}</div></div></li>)}</ul></Card>
          )}
        </div>
      )}
    </Async>
  );
}

function HistoryTab() {
  const { t } = useTranslation("memory");
  const q = useGet<{ enabled: boolean; changes: Change[] }>("/memory/history", { limit: 60 });
  const [view, setView] = useState<Change | null>(null);
  return (
    <Card title={t("history.title")} subtitle={t("history.subtitle")} pad={false}>
      <Async q={q}>{(d) => !d.enabled ? <EmptyState title={t("history.offTitle")}>{t("history.offBody")}</EmptyState> : d.changes.length === 0 ? <EmptyState title={t("history.empty")} /> : (
        <ul className="divide-y divide-border">{d.changes.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5">
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{c.subject.replace(/^coach: /, "")}</div><div className="text-xs text-muted">{fmtDateTime(c.date)} · <code>{c.id}</code>{c.source ? ` · ${c.source}` : ""} · {c.files.join(", ")}</div>{c.reason && <div className="truncate text-xs text-faint">{c.reason}</div>}</div>
            <Button size="sm" variant="ghost" onClick={() => setView(c)}>{t("history.view")}</Button>
          </li>))}</ul>)}
      </Async>
      {view && <ViewChange c={view} onClose={() => setView(null)} />}
    </Card>
  );
}

function ViewChange({ c, onClose }: { c: Change; onClose: () => void }) {
  const { t } = useTranslation("memory");
  const q = useGet<{ diff: string; revert_command: string }>(`/memory/history/${c.id}/diff`);
  return (
    <Dialog open onClose={onClose} size="lg" title={c.subject} description={`${fmtDateTime(c.date)} · ${c.id}`}>
      {q.isPending ? <Spinner /> : q.isError ? <Notice tone="neg">{errorText(q.error)}</Notice> : (
        <div className="grid gap-4">
          <DiffView diff={q.data.diff} />
          <div className="grid gap-1.5">
            <div className="text-xs font-medium text-muted">{t("history.toUndo")}</div>
            <CopyCommand command={q.data.revert_command} />
          </div>
        </div>
      )}
    </Dialog>
  );
}
