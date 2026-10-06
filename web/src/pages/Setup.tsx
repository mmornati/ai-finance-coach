import { useState } from "react";
import { Link } from "react-router";
import { Check, Circle, CircleDot, Sparkles } from "lucide-react";
import { Async, Badge, Button, Card, DiffView, Field, Input, Notice, PageHeader, ProgressBar, Select, Skeleton, Spinner } from "@/components/ui";
import { CopyCommand } from "@/components/CopyCommand";
import { useDryRun, useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import type { EditResult, Onboarding, OnboardingStep, WizardStatus } from "@/api/types";

const LINKS: Record<OnboardingStep["id"], { to: string; label: string }> = {
  household: { to: "/memory?tab=household", label: "Members" },
  accounts: { to: "/connections", label: "Accounts" },
  loans: { to: "/memory?tab=items", label: "Loans" },
  contracts: { to: "/memory?tab=items", label: "Contracts" },
  preferences: { to: "/memory", label: "Memory" },
  budgets: { to: "/budgets", label: "Budgets" },
  questions: { to: "/memory?tab=questions", label: "Questions" },
};
const ICON = { done: <Check className="size-4 text-pos" aria-hidden />, partial: <CircleDot className="size-4 text-warn" aria-hidden />, todo: <Circle className="size-4 text-faint" aria-hidden /> };
const TONE = { done: "pos", partial: "warn", todo: "neutral" } as const;

function missingLines(s: OnboardingStep): string[] {
  const out: string[] = [];
  for (const m of s.missing ?? []) out.push(typeof m === "string" ? m : `account ${m.label ?? m.account}: ${m.missing.join(", ")} not set`);
  for (const l of s.liabilities ?? []) if (l.missing.length) out.push(`${l.id} (${l.kind}): ${l.missing.join(", ")}`);
  if (s.loan_payments_without_file?.length) out.push(`${s.loan_payments_without_file.length} loan-like payment(s) without a liability file`);
  if (s.recurring_without_contract?.length) out.push(`${s.recurring_without_contract.length} recurring payment(s) without a contract file`);
  for (const c of s.contracts_with_empty_fields ?? []) out.push(`contract ${c.id}: ${c.missing.join(", ")}`);
  return out;
}

const WIZARD_TONE = { done: "pos", partial: "warn", todo: "neutral", skipped: "neutral", blocked: "neutral" } as const;

/** E13-2: where the first-run wizard stands. Read-only on purpose: its steps (Enable Banking, a bank link, a sync, a model run) ask for a typed
 *  consent in a terminal before anything leaves this machine, so the page shows the state and the command, and starts nothing. */
function FirstRun() {
  const q = useGet<WizardStatus>("/setup/wizard");
  const d = q.data;
  if (!d || !Array.isArray(d.steps) || d.progress.next_step === null) return null;
  const next = d.steps.find((s) => s.id === d.progress.next_step);
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[15px] font-semibold">First run</h2>
        <div className="min-w-48 flex-1"><ProgressBar label="First-run progress" value={d.progress.done} max={d.progress.total} tone="info" /></div>
        <div className="text-sm text-muted" data-testid="wizard-progress">{d.progress.done} of {d.progress.total} first-run steps done</div>
      </div>
      <ul className="mt-2 grid gap-1 text-[13px]" aria-label="First-run steps">
        {d.steps.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-2">
            <Badge tone={WIZARD_TONE[s.status]}>{s.status === "partial" ? "in progress" : s.status === "blocked" ? "waiting" : s.status === "todo" ? "to do" : s.status}</Badge>
            <span>{s.title}{s.optional ? " (optional)" : ""}</span>
            {s.detail && <span className="text-muted">{s.detail}</span>}
          </li>
        ))}
      </ul>
      {next && (
        <div className="mt-3">
          <div className="mb-1 text-xs text-muted">Next, in a terminal (these steps ask for your confirmation before anything leaves this machine, so the page cannot run them):</div>
          <CopyCommand command={d.command} />
          {d.container && <div className="mt-2 text-xs text-muted">Running in Docker: the commands run in the container, not on your host.</div>}
          {next.id === "enablebanking" && (
            <div className="mt-2">
              <div className="mb-1 text-xs text-muted">Enable Banking: the downloaded key is on your host, so mount it for this one command{d.container ? " (the guide keeps a private copy in /config; give it /tmp/eb.pem)" : ""}:</div>
              <CopyCommand command={d.container ? d.commands.enablebanking : "uv run coach setup enablebanking"} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Setup() {
  const q = useGet<Onboarding>("/onboarding");
  return (
    <>
      <PageHeader title="Set up the coach" subtitle="What the coach still does not know about your household. Every change is previewed, validated and recorded in the memory history." />
      <div className="mb-4"><FirstRun /></div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <Card>
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-48 flex-1"><ProgressBar label="Setup progress" value={d.progress.done} max={d.progress.total} tone="info" /></div>
                <div className="text-sm text-muted" data-testid="setup-progress">{d.progress.done} of {d.progress.total} steps done</div>
                <Link to="/coach" className="inline-flex items-center gap-1 text-sm text-accent hover:underline"><Sparkles className="size-3.5" aria-hidden /> Ask the coach to guide me</Link>
              </div>
              <p className="mt-2 text-xs text-faint">The coach can only propose changes: they appear in Memory &gt; Proposals and you accept them yourself in a terminal. Finish with <code>uv run coach memory check</code>.</p>
            </Card>
            {d.steps.map((s) => <StepCard key={s.id} s={s} cmd={d.next_actions.filter((a) => a.step === s.id)} declared={d.declared} />)}
          </div>
        )}
      </Async>
    </>
  );
}

function StepCard({ s, cmd, declared }: { s: OnboardingStep; cmd: { do: string; command: string }[]; declared?: Record<ListKey, string[]> }) {
  const lines = missingLines(s);
  const link = LINKS[s.id];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        {ICON[s.status]}
        <h2 className="text-[15px] font-semibold">{s.heading}</h2>
        <Badge tone={TONE[s.status]}>{s.status === "done" ? "done" : s.status === "partial" ? "in progress" : "to do"}</Badge>
        <Link to={link.to} className="ml-auto text-sm text-accent hover:underline">{link.label}</Link>
      </div>
      {lines.length > 0 && <ul className="mt-2 list-disc pl-5 text-[13px] text-muted">{lines.slice(0, 8).map((l, i) => <li key={i}>{l}</li>)}{lines.length > 8 && <li>and {lines.length - 8} more</li>}</ul>}
      {s.id === "household" && s.status !== "done" && <HouseholdForm have={s.have} declared={declared} />}
      {s.id === "preferences" && s.status !== "done" && <PreferencesForm />}
      {s.status !== "done" && cmd.length > 0 && s.id !== "household" && s.id !== "preferences" && (
        <div className="mt-3 grid gap-1.5">{cmd.slice(0, 1).map((c) => <div key={c.command}><div className="mb-1 text-xs text-muted">{c.do}</div><CopyCommand command={c.command} /></div>)}</div>
      )}
    </Card>
  );
}

const split = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

function Preview({ pv, onWrite, busy, blocked }: { pv: { data: EditResult | null; error: string | null; loading: boolean }; onWrite: () => void; busy: boolean; blocked?: boolean }) {
  return (
    <div className="mt-3 grid gap-2">
      {pv.loading ? <Spinner /> : pv.error ? <Notice tone="neg">{pv.error}</Notice> : pv.data && <DiffView diff={pv.data.diff} empty="Nothing to change." />}
      <div className="flex justify-end"><Button variant="primary" busy={busy} disabled={!pv.data || !pv.data.changed || blocked} onClick={onWrite}>Write this change</Button></div>
    </div>
  );
}

const LIST_LABEL = { employers: "Employer(s)", places: "Town(s) you live or work in", schools: "School(s) or nursery(ies)" } as const;
type ListKey = keyof typeof LIST_LABEL;

function HouseholdForm({ have, declared }: { have: Record<string, unknown>; declared?: Record<ListKey, string[]> }) {
  const [country, setCountry] = useState("");
  const [adds, setAdds] = useState<Record<ListKey, string>>({ employers: "", places: "", schools: "" });
  const [drops, setDrops] = useState<Record<ListKey, string[]>>({ employers: [], places: [], schools: [] });
  const [confirm, setConfirm] = useState(false);
  const keys = Object.keys(LIST_LABEL) as ListKey[];
  const body: Record<string, unknown> = { ...(country ? { country } : {}) };
  for (const k of keys) {
    const add = split(adds[k]);
    if (add.length || drops[k].length) body[k] = { add, remove: drops[k] };
  }
  const removing = keys.some((k) => drops[k].length > 0);
  if (removing && confirm) body.confirm_removal = true;
  const filled = Object.keys(body).filter((k) => k !== "confirm_removal").length > 0;
  const pv = useDryRun<EditResult & { warning?: string }>("/onboarding/household", body, filled, "put");
  const reset = () => { setCountry(""); setAdds({ employers: "", places: "", schools: "" }); setDrops({ employers: [], places: [], schools: [] }); setConfirm(false); };
  const write = useWrite(() => api.put<EditResult>("/onboarding/household", body), { success: "Household updated", onSuccess: reset });
  const toggle = (k: ListKey, v: string) => setDrops({ ...drops, [k]: drops[k].includes(v) ? drops[k].filter((x) => x !== v) : [...drops[k], v] });
  return (
    <div className="mt-3 grid gap-3 rounded-lg border border-border p-3">
      <p className="text-xs text-muted">Country decides the tax and cancellation rules. Declared employers, towns and schools are masked in everything a model sees. Typing new ones <b>adds</b> them; nothing is removed unless you mark it ({String(have.employers ?? 0)} employer(s), {String(have.places ?? 0)} place(s), {String(have.schools ?? 0)} school(s) declared).</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Country">{(id) => <Select id={id} value={country} onChange={(e) => setCountry(e.target.value)}><option value="">Not changed</option><option value="FR">France</option><option value="IT">Italy</option></Select>}</Field>
        {keys.map((k) => (
          <div key={k} className="grid gap-1.5">
            <Field label={`Add ${LIST_LABEL[k]}`} hint="comma separated">{(id) => <Input id={id} value={adds[k]} onChange={(e) => setAdds({ ...adds, [k]: e.target.value })} />}</Field>
            {(declared?.[k] ?? []).length > 0 && (
              <ul className="flex flex-wrap gap-1.5" aria-label={`Declared ${LIST_LABEL[k]}`}>
                {declared![k].map((v) => (
                  <li key={v}><button type="button" aria-pressed={drops[k].includes(v)} aria-label={`${drops[k].includes(v) ? "Keep" : "Remove"} ${v}`} onClick={() => toggle(k, v)}
                    className={`rounded-full border px-2 py-0.5 text-xs ${drops[k].includes(v) ? "border-neg text-neg line-through" : "border-border-strong"}`}>{v} {drops[k].includes(v) ? "↺" : "×"}</button></li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      {removing && (
        <Notice tone="warn" title="Removing declared terms">
          These will no longer be masked for the model. <label className="mt-1 flex items-center gap-2"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> I understand, remove them</label>
        </Notice>
      )}
      {filled && <Preview pv={pv} busy={write.isPending} blocked={removing && !confirm} onWrite={() => write.mutate(undefined as never)} />}
    </div>
  );
}

function PreferencesForm() {
  const [f, setF] = useState({ language: "", tone: "", goals: "", avoid: "" });
  const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim()));
  const filled = Object.keys(body).length > 0;
  const pv = useDryRun<EditResult>("/onboarding/preferences", body, filled);
  const write = useWrite(() => api.post<EditResult>("/onboarding/preferences", body), { success: "Preferences saved", onSuccess: () => setF({ language: "", tone: "", goals: "", avoid: "" }) });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="mt-3 grid gap-3 rounded-lg border border-border p-3">
      <p className="text-xs text-muted">These lines are added to preferences.md, which tells the coach how to talk to you (the coach reads them only when you allow free text in <code>[privacy] model_detail</code>).</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Language">{(id) => <Input id={id} value={f.language} onChange={set("language")} placeholder="fr, en or it" />}</Field>
        <Field label="Tone">{(id) => <Input id={id} value={f.tone} onChange={set("tone")} placeholder="short, direct, numbers first" />}</Field>
        <Field label="Goals">{(id) => <Input id={id} value={f.goals} onChange={set("goals")} />}</Field>
        <Field label="Topics to avoid">{(id) => <Input id={id} value={f.avoid} onChange={set("avoid")} />}</Field>
      </div>
      {filled && <Preview pv={pv} busy={write.isPending} onWrite={() => write.mutate(undefined as never)} />}
    </div>
  );
}
