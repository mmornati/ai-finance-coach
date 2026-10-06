import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Check, Pencil, Plus, RotateCcw, ShieldAlert, Trash2, X } from "lucide-react";
import { Async, Badge, Button, Card, Chips, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, Select, Skeleton, Spinner, Tabs, Textarea } from "@/components/ui";
import { ItemDialog, Kind } from "@/components/ItemForm";
import { CopyCommand } from "@/components/CopyCommand";
import { useDryRun, useGet, useProposals, useQuestions, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtMoney, groupLabel } from "@/lib/format";
import type { Annotation, Asset, Change, CheckIssue, EditResult, EventMeta, Liability, MemoryOverview, Member, Proposal, Question } from "@/api/types";
import { cn } from "@/lib/utils";

type Tab = "questions" | "proposals" | "household" | "events" | "items" | "annotations" | "check" | "history";

export default function Memory() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "questions";
  const ov = useGet<MemoryOverview>("/memory/overview");
  const props = useProposals();
  const nProps = props.data?.proposals.length ?? 0;
  const nQ = ov.data?.counts.open_questions ?? 0;
  return (
    <>
      <PageHeader title="Memory" subtitle="What the coach knows about your household, in plain files you own. Every change is validated, previewed and recorded in a history you can revert." />
      <Tabs label="Memory sections" value={tab} onChange={(t) => setSp({ tab: t })} tabs={[
        { value: "questions", label: "Questions", badge: nQ ? <Badge tone="info">{nQ}</Badge> : null },
        { value: "proposals", label: "Proposals", badge: nProps ? <Badge tone="warn">{nProps}</Badge> : null },
        { value: "household", label: "Household" },
        { value: "events", label: "Events" },
        { value: "items", label: "Loans, contracts, assets" },
        { value: "annotations", label: "Annotations" },
        { value: "check", label: "Check", badge: ov.data && ov.data.check.errors ? <Badge tone="neg">{ov.data.check.errors}</Badge> : null },
        { value: "history", label: "History" },
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
  const [status, setStatus] = useState("open");
  const q = useQuestions(status);
  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <Select aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)} className="!w-auto">
          <option value="open">Open</option><option value="answered">Answered</option><option value="dismissed">Dismissed</option><option value="all">All</option>
        </Select>
        <p className="text-xs text-faint">An answer is only recorded. Turning it into a memory change is a separate, previewed step.</p>
      </div>
      <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
        {(d) => d.questions.length === 0 ? <Card><EmptyState title="No question here">New questions appear after a sync when the coach finds something it cannot infer.</EmptyState></Card> : (
          <ul className="grid gap-3">{d.questions.map((x) => <li key={x.id}><QuestionCard x={x} /></li>)}</ul>
        )}
      </Async>
    </div>
  );
}

function QuestionCard({ x }: { x: Question }) {
  const [answer, setAnswer] = useState("");
  const [open, setOpen] = useState(false);
  const [why, setWhy] = useState("");
  const ans = useWrite(() => api.post(`/questions/${x.id}/answer`, { text: answer }), { success: "Answer recorded", onSuccess: () => setOpen(false) });
  const dis = useWrite(() => api.post(`/questions/${x.id}/dismiss`, { reason: why || undefined }), { success: "Dismissed" });
  const reopen = useWrite(() => api.post(`/questions/${x.id}/reopen`, {}), { success: "Reopened" });
  const ev = Object.entries(x.evidence ?? {}).filter(([, v]) => v !== null && v !== "");
  const target = x.suggested_target?.file;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2"><Badge>{x.topic}</Badge>{x.stake && <Badge tone="warn">{fmtMoney(x.stake, { round: true })} at stake</Badge>}{x.status !== "open" && <Badge tone={x.status === "answered" ? "pos" : "neutral"}>{x.status}</Badge>}</div>
      <p className="mt-2 text-[15px] font-medium">{x.question}</p>
      {x.context && <p className="mt-1 text-[13px] text-muted">{x.context}</p>}
      {ev.length > 0 && <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">{ev.slice(0, 6).map(([k, v]) => <div key={k}><dt className="inline">{k.replace(/_/g, " ")}: </dt><dd className="inline font-medium text-text">{Array.isArray(v) ? v.join(", ") : String(v)}</dd></div>)}</dl>}
      {x.answer && <p className="mt-2 rounded-lg bg-pos-soft px-3 py-2 text-[13px] text-pos">{x.answer}</p>}
      {x.note && <p className="mt-2 text-xs text-muted">Dismissed: {x.note}</p>}
      {x.status === "open" && !open && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" onClick={() => setOpen(true)}>Answer</Button>
          {target?.startsWith("liabilities/") && <Link to="/wealth" className="text-sm text-accent hover:underline">Fill in the loan</Link>}
          {target === "categorization.yaml" && <Link to="/transactions" className="text-sm text-accent hover:underline">Open transactions</Link>}
          <span className="ml-auto flex items-center gap-1.5"><Input aria-label="Reason (optional)" placeholder="Reason (optional)" value={why} onChange={(e) => setWhy(e.target.value)} className="!min-h-8 w-40 !text-[13px]" /><Button size="sm" variant="ghost" busy={dis.isPending} onClick={() => dis.mutate(undefined as never)}><X className="size-3.5" aria-hidden /> Dismiss</Button></span>
        </div>
      )}
      {open && (
        <div className="mt-3 grid gap-2">
          <Textarea aria-label="Your answer" autoFocus value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Your answer, in your own words" />
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" disabled={!answer.trim()} busy={ans.isPending} onClick={() => ans.mutate(undefined as never)}>Record answer</Button></div>
        </div>
      )}
      {x.status !== "open" && <div className="mt-3"><Button size="sm" busy={reopen.isPending} onClick={() => reopen.mutate(undefined as never)}><RotateCcw className="size-3.5" aria-hidden /> Reopen</Button></div>}
    </Card>
  );
}

/* ------------------------------------------------------------------ proposals */
function ProposalsTab() {
  const q = useProposals();
  const [rej, setRej] = useState<Proposal | null>(null);
  return (
    <div className="grid gap-4">
      <Notice title="Accepting is done in your terminal, on purpose">
        The coach and document extraction can only propose changes. Nothing is written until <b>you</b> run the accept command shown on each proposal in your own
        terminal (it shows the same diff and asks for a typed confirmation). This page cannot accept for you, so nothing that reaches this page can write to your memory.
      </Notice>
      <Async q={q} skeleton={<Skeleton className="h-48 w-full" />}>
        {(d) => d.proposals.length === 0 ? <Card><EmptyState title="No pending proposal" /></Card> : (
          <ul className="grid gap-3">
            {d.proposals.map((p) => (
              <li key={p.id}>
                <Card>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><Badge tone="info">{p.source}</Badge><span>{fmtDateTime(p.created)}</span><span>→ {p.file}</span>{!p.sealed && <Badge tone="neg"><ShieldAlert className="size-3" aria-hidden /> modified after creation: it cannot be accepted</Badge>}</div>
                  <p className="mt-2 font-medium">{p.reason}</p>
                  <ul className="mt-2 grid gap-1 text-[13px]">
                    {p.changes.map((c, i) => <li key={i} className="rounded bg-surface-2 px-2 py-1"><code className="text-xs">{c.op} {c.path}</code>{c.op === "set" && <> : {JSON.stringify(c.old)} → <b>{JSON.stringify(c.new)}</b></>}{c.snippet && <div className="text-xs text-faint">source: “{c.snippet}”</div>}{c.suspicious && <Badge tone="neg" className="ml-2">instruction-like text</Badge>}</li>)}
                  </ul>
                  <div className="mt-3"><DiffView diff={p.diff} empty="No change." /></div>
                  {p.suspicious_paths.length > 0 && <Notice tone="neg" className="mt-2" title="The source contains instruction-like text">The source (a document, or data the coach read while proposing this) contains text that looks like an instruction. Check these fields yourself before accepting; each needs its own --confirm-field: {p.suspicious_paths.join(", ")}.</Notice>}
                  {!p.applicable && <Notice tone="warn" className="mt-2">Cannot be applied to the file as it is now: {p.error}</Notice>}
                  <div className="mt-3 grid gap-1.5">
                    <div className="text-xs font-medium text-muted">To accept, run this in your own terminal:</div>
                    <CopyCommand command={p.accept_command} />
                  </div>
                  <div className="mt-3 flex justify-end"><Button variant="ghost" onClick={() => setRej(p)}>Reject…</Button></div>
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
  const [note, setNote] = useState("");
  const reject = useWrite(() => api.post(`/proposals/${p.id}/reject`, { note: note || undefined }), { success: "Proposal rejected", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="Reject this proposal?" description={p.reason} size="sm"
      footer={<><Button variant="ghost" onClick={onClose}>Keep it</Button><Button variant="danger" busy={reject.isPending} onClick={() => reject.mutate(undefined as never)}>Reject</Button></>}>
      <p className="mb-3 text-sm text-muted">Nothing is written to your memory. The proposal is closed and cannot be accepted afterwards.</p>
      <Field label="Why? (optional)">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ household / events */
function HouseholdTab() {
  const q = useGet<{ members: Member[] }>("/household");
  const [edit, setEdit] = useState<{ id?: string; initial?: Record<string, any> } | null>(null);
  return (
    <Card title="Household members" subtitle="Names stay on this machine; the coach sees aliases only." action={<Button size="sm" onClick={() => setEdit({})}><Plus className="size-3.5" aria-hidden /> Member</Button>}>
      <Async q={q}>
        {(d) => d.members.length === 0 ? <EmptyState title="No member yet" /> : (
          <ul className="divide-y divide-border">{d.members.map((m) => <li key={m.id} className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="font-medium">{m.name} <Badge>{m.role}</Badge></div><div className="text-xs text-muted">{m.birth_year ? `born ${m.birth_year}` : <span className="text-warn">birth year unknown</span>}{m.aliases.length ? ` · seen as ${m.aliases.join(", ")}` : ""}</div></div><Button size="sm" variant="ghost" aria-label={`Edit ${m.name}`} onClick={() => setEdit({ id: m.id, initial: m })}><Pencil className="size-3.5" /></Button></li>)}</ul>
        )}
      </Async>
      {edit && <ItemDialog kind="members" {...edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

function EventsTab() {
  const q = useGet<{ events: EventMeta[] }>("/events");
  const [edit, setEdit] = useState<{ id?: string; initial?: Record<string, any> } | null>(null);
  return (
    <Card title="Events" subtitle="Trips, works and projects. Tag transactions with them to keep their cost out of the usual month." action={<Button size="sm" onClick={() => setEdit({})}><Plus className="size-3.5" aria-hidden /> Event</Button>}>
      <Async q={q}>
        {(d) => d.events.length === 0 ? <EmptyState title="No event yet" /> : (
          <ul className="divide-y divide-border">{d.events.map((e) => <li key={e.id} className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="font-medium">{e.title ?? e.id} <Badge>{e.status ?? "no status"}</Badge></div><div className="text-xs text-muted">{e.start ? `${fmtDate(e.start)}${e.end ? ` → ${fmtDate(e.end)}` : ""}` : "no dates"}{e.budget ? ` · budget ${fmtMoney(e.budget, { round: true })}` : ""}</div></div><Link className="text-sm text-accent hover:underline" to={`/transactions?event=${e.id}`}>Transactions</Link>{e.source === "events.yaml" && <Button size="sm" variant="ghost" aria-label={`Edit ${e.id}`} onClick={() => setEdit({ id: e.id, initial: e })}><Pencil className="size-3.5" /></Button>}</li>)}</ul>
        )}
      </Async>
      {edit && <ItemDialog kind="events" {...edit} onClose={() => setEdit(null)} />}
    </Card>
  );
}

/* ------------------------------------------------------------------ loans, contracts, assets */
function ItemsTab() {
  const liab = useGet<{ liabilities: Liability[] }>("/liabilities");
  const con = useGet<{ contracts: Record<string, any>[] }>("/contracts");
  const ast = useGet<{ assets: (Asset & Record<string, any>)[] }>("/assets");
  const [edit, setEdit] = useState<{ kind: Kind; id?: string; initial?: Record<string, any> } | null>(null);
  const Row = ({ title, sub, onEdit, warn }: { title: string; sub: string; onEdit: () => void; warn?: string }) => (
    <li className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="truncate font-medium">{title}</div><div className="truncate text-xs text-muted">{sub}</div>{warn && <div className="text-xs text-warn">{warn}</div>}</div><Button size="sm" variant="ghost" aria-label={`Edit ${title}`} onClick={onEdit}><Pencil className="size-3.5" /></Button></li>
  );
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Loans" action={<Button size="sm" onClick={() => setEdit({ kind: "liabilities" })}><Plus className="size-3.5" aria-hidden /> Loan</Button>}>
        <Async q={liab}>{(d) => d.liabilities.length ? <ul className="divide-y divide-border">{d.liabilities.map((l) => <Row key={l.id} title={l.lender ?? l.id} sub={`${groupLabel(l.kind)}${l.monthly_payment ? ` · ${fmtMoney(l.monthly_payment)}/month` : ""}`} warn={l.missing.length ? `missing: ${l.missing.join(", ")}` : undefined} onEdit={() => setEdit({ kind: "liabilities", id: l.id, initial: l })} />)}</ul> : <EmptyState title="No loan" />}</Async>
      </Card>
      <Card title="Contracts" action={<Button size="sm" onClick={() => setEdit({ kind: "contracts" })}><Plus className="size-3.5" aria-hidden /> Contract</Button>}>
        <Async q={con}>{(d) => d.contracts.length ? <ul className="divide-y divide-border">{d.contracts.map((c) => <Row key={c.id} title={c.provider ?? c.id} sub={`${groupLabel(c.kind ?? "other")}${c.billing?.amount ? ` · ${fmtMoney(c.billing.amount)}` : ""}${c.renewal ? ` · renews ${fmtDate(c.renewal)}` : ""}`} onEdit={() => setEdit({ kind: "contracts", id: c.id, initial: c })} />)}</ul> : <EmptyState title="No contract on file">Add the ones behind your subscriptions to see their renewal dates.</EmptyState>}</Async>
      </Card>
      <Card title="Assets" className="lg:col-span-2" action={<Button size="sm" onClick={() => setEdit({ kind: "assets" })}><Plus className="size-3.5" aria-hidden /> Asset</Button>}>
        <Async q={ast}>{(d) => d.assets.length ? <ul className="divide-y divide-border">{d.assets.map((a) => <Row key={a.id} title={a.description ?? a.id} sub={`${groupLabel(a.kind)}${a.value ? ` · ${fmtMoney(a.value, { round: true })}` : ""}${a.as_of ? ` as of ${fmtDate(a.as_of)}` : ""}`} warn={a.unknown_value ? "value unknown" : a.stale ? "value is old: refresh it" : undefined} onEdit={() => setEdit({ kind: "assets", id: a.id, initial: a })} />)}</ul> : <EmptyState title="No asset" />}</Async>
      </Card>
      {edit && <ItemDialog {...edit} onClose={() => setEdit(null)} />}
    </div>
  );
}

/* ------------------------------------------------------------------ annotations */
function AnnotationsTab() {
  const q = useGet<{ annotations: Annotation[] }>("/annotations");
  const [del, setDel] = useState<Annotation | null>(null);
  return (
    <Card title="Annotations" subtitle="Rules you taught: which transactions get which category, tags or event. The first match wins." pad={false}>
      <Async q={q}>{(d) => d.annotations.length === 0 ? <EmptyState title="No annotation">Fix a category in Transactions and choose “Remember with a memory annotation”.</EmptyState> : (
        <ul className="divide-y divide-border">{d.annotations.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5">
            <div className="min-w-0 flex-1">
              <div className="font-medium"><code className="text-[13px]">{a.id}</code></div>
              <div className="mt-1 flex flex-wrap gap-1.5">{a.category && <Badge tone="info">{a.category}</Badge>}<Chips items={a.tags ?? []} tone="warn" />{a.event && <Badge>{a.event}</Badge>}</div>
              <div className="mt-1 text-xs text-muted">{Object.entries(a.match).map(([k, v]) => `${k}: ${Array.isArray(v) ? `${v.length} transaction(s)` : v}`).join(" · ")}</div>
              {a.note && <div className="text-xs text-faint">{a.note}</div>}
            </div>
            <div className="text-right text-xs text-muted"><div className="num">{a.applies_to} decided</div><div className="num">{a.matched} matched <Money v={a.total} round /></div></div>
            <Button size="sm" variant="ghost" aria-label={`Remove ${a.id}`} onClick={() => setDel(a)}><Trash2 className="size-3.5" /></Button>
          </li>))}</ul>)}
      </Async>
      {del && <DeleteAnnotation a={del} onClose={() => setDel(null)} />}
    </Card>
  );
}

function DeleteAnnotation({ a, onClose }: { a: Annotation; onClose: () => void }) {
  const pv = useDryRun<EditResult>(`/annotations/${a.id}/delete`, {}, true);
  const del = useWrite(() => api.post<EditResult>(`/annotations/${a.id}/delete`, {}, { dry_run: false }), { success: "Annotation removed", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={`Remove “${a.id}”?`} description={`${a.applies_to} transaction(s) currently follow it.`} footer={<><Button variant="ghost" onClick={onClose}>Keep</Button><Button variant="danger" busy={del.isPending} disabled={!pv.data} onClick={() => del.mutate(undefined as never)}>Remove</Button></>}>
      {pv.loading ? <Spinner /> : pv.data && <DiffView diff={pv.data.diff} />}
    </Dialog>
  );
}

/* ------------------------------------------------------------------ check / history */
function CheckTab() {
  const q = useGet<{ summary: { errors: number; warnings: number; info: number }; issues: CheckIssue[] }>("/memory/check");
  return (
    <Async q={q} skeleton={<Skeleton className="h-64 w-full" />}>
      {(d) => (
        <div className="grid gap-4">
          <div className="flex gap-2"><Badge tone={d.summary.errors ? "neg" : "pos"}>{d.summary.errors} errors</Badge><Badge tone={d.summary.warnings ? "warn" : "neutral"}>{d.summary.warnings} warnings</Badge><Badge>{d.summary.info} notes</Badge></div>
          {d.issues.length === 0 ? <Card><EmptyState icon={<Check className="size-6" />} title="Memory is consistent" /></Card> : (
            <Card pad={false}><ul className="divide-y divide-border">{d.issues.map((i, k) => <li key={k} className="flex gap-3 px-4 py-2.5 text-sm sm:px-5"><Badge tone={i.level === "error" ? "neg" : i.level === "warning" ? "warn" : "neutral"}>{i.level}</Badge><div className="min-w-0"><div>{i.message}</div><div className="truncate text-xs text-faint">{i.file}{i.path ? ` · ${i.path}` : ""}{i.line ? ` · line ${i.line}` : ""}</div></div></li>)}</ul></Card>
          )}
        </div>
      )}
    </Async>
  );
}

function HistoryTab() {
  const q = useGet<{ enabled: boolean; changes: Change[] }>("/memory/history", { limit: 60 });
  const [view, setView] = useState<Change | null>(null);
  return (
    <Card title="Change history" subtitle="Every write to the memory, whoever made it (you in the app, the CLI, the coach). Reverting is done in a terminal." pad={false}>
      <Async q={q}>{(d) => !d.enabled ? <EmptyState title="History is off">Set [memory] history = true in config.toml to record changes.</EmptyState> : d.changes.length === 0 ? <EmptyState title="No recorded change yet" /> : (
        <ul className="divide-y divide-border">{d.changes.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5">
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{c.subject.replace(/^coach: /, "")}</div><div className="text-xs text-muted">{fmtDateTime(c.date)} · <code>{c.id}</code>{c.source ? ` · ${c.source}` : ""} · {c.files.join(", ")}</div>{c.reason && <div className="truncate text-xs text-faint">{c.reason}</div>}</div>
            <Button size="sm" variant="ghost" onClick={() => setView(c)}>View</Button>
          </li>))}</ul>)}
      </Async>
      {view && <ViewChange c={view} onClose={() => setView(null)} />}
    </Card>
  );
}

function ViewChange({ c, onClose }: { c: Change; onClose: () => void }) {
  const q = useGet<{ diff: string; revert_command: string }>(`/memory/history/${c.id}/diff`);
  return (
    <Dialog open onClose={onClose} size="lg" title={c.subject} description={`${fmtDateTime(c.date)} · ${c.id}`}>
      {q.isPending ? <Spinner /> : q.isError ? <Notice tone="neg">{q.error.message}</Notice> : (
        <div className="grid gap-4">
          <DiffView diff={q.data.diff} />
          <div className="grid gap-1.5">
            <div className="text-xs font-medium text-muted">To undo this change, run this in your own terminal (reverting is a deliberate, terminal-only action):</div>
            <CopyCommand command={q.data.revert_command} />
          </div>
        </div>
      )}
    </Dialog>
  );
}
