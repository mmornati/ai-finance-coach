import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Bot, Eye, Send, ShieldAlert, Sparkles, Square } from "lucide-react";
import { Badge, Button, Card, Disclosure, Notice, PageHeader } from "@/components/ui";
import { CopyCommand } from "@/components/CopyCommand";
import { CoachText } from "@/components/CoachText";
import { useGet } from "@/api/hooks";
import { api, streamSSE } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Compliance, CoachStatus } from "@/api/types";

interface Tool { id: string; name: string; args: string; ok?: boolean; suspicious?: boolean }
interface Usage { backend: string; model: string; tokens_in: number; tokens_out: number; cache_read_tokens: number; cost_usd: number | null; cost_is_estimate: boolean; tool_calls: number; duration_s: number }
interface Msg {
  id: number;
  role: "user" | "coach" | "notice";
  text: string;
  pending?: boolean;
  jobId?: string;
  tools?: Tool[];
  proposals?: { id: string; command?: string }[];
  usage?: Usage;
  suspicious?: boolean;
  unverified?: string[];
  cancelled?: boolean;
  compliance?: Compliance;
}
let n = 0;
// EU AI Act transparency (E11-5): every coach text is labelled; the server sends the label in the answer's language, this one shows while it streams
const AI_LABEL = "AI-generated content: it can contain mistakes. Check the figures against your accounts.";

const TOOL_LABEL: Record<string, string> = {
  coverage: "data coverage", category_averages: "usual spending", cashflow: "cash flow", recurring: "recurring payments", price_changes: "price changes",
  anomalies: "unusual payments", forecast: "forecast", budget_status: "budgets", budget_suggestions: "budget suggestions", calendar: "upcoming payments",
  goals: "goals", year_review: "year in review", transactions_search: "transactions", explain_transaction: "a categorisation", memory_context: "household memory",
  open_questions: "open questions", memory_propose: "a memory proposal", add_insight: "an insight",
  monthly_review: "monthly review", explain_spike: "spike breakdown", subscription_audit: "subscription audit", cancellability: "cancellation rules",
  savings_estimate: "savings estimate", mortgage_check: "mortgage check", what_if: "what-if scenario", tax_candidates: "tax candidates",
  onboarding_status: "setup checklist", questions_propose: "proposed questions",
};
interface QuickPrompt { id: string; text: string; skill?: string }

export default function Coach() {
  const status = useGet<CoachStatus>("/coach/status");
  const prompts = useGet<{ prompts: QuickPrompt[] }>("/coach/prompts");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const jobRef = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" }), [msgs]);
  useEffect(() => () => ctl.current?.abort(), []);

  async function ask(q: string, skill?: string) {
    if (!q.trim() || busy) return;
    const reply = ++n;
    const patch = (f: (m: Msg) => Msg) => setMsgs((m) => m.map((x) => (x.id === reply ? f(x) : x)));
    setMsgs((m) => [...m, { id: ++n, role: "user", text: q }, { id: reply, role: "coach", text: "", pending: true, tools: [] }]);
    setText("");
    setBusy(true);
    jobRef.current = null;
    ctl.current = new AbortController();
    try {
      await streamSSE("/coach/stream", skill ? { question: q, skill } : { question: q }, (ev, data) => {
        if (ev === "meta") {
          jobRef.current = data.job_id;
          patch((x) => ({ ...x, jobId: data.job_id }));
        } else if (ev === "delta") patch((x) => ({ ...x, text: x.text + data.text }));
        else if (ev === "tool_call") patch((x) => ({ ...x, tools: [...(x.tools ?? []), { id: data.id, name: data.name, args: data.args }] }));
        else if (ev === "tool_result") patch((x) => ({ ...x, tools: (x.tools ?? []).map((t) => (t.id === data.id ? { ...t, ok: data.ok, suspicious: data.suspicious } : t)) }));
        else if (ev === "proposal") patch((x) => ({ ...x, proposals: [...(x.proposals ?? []), { id: data.id, command: data.command }] }));
        else if (ev === "usage") patch((x) => ({ ...x, usage: data }));
        else if (ev === "answer") patch((x) => ({ ...x, suspicious: data.suspicious, unverified: data.unverified_numbers ?? [], compliance: data.compliance }));
        else if (ev === "notice" || ev === "error") {
          if (data.code === "suspicious") patch((x) => ({ ...x, suspicious: true }));
          else {
            if (data.code === "cancelled") patch((x) => ({ ...x, cancelled: true }));
            setMsgs((m) => [...m.filter((x) => !(x.id === reply && !x.text && ev === "error")), { id: ++n, role: "notice", text: data.message }]);
          }
        } else if (ev === "done") patch((x) => ({ ...x, pending: false }));
      }, ctl.current.signal);
    } catch (e) {
      setMsgs((m) => [...m.filter((x) => x.id !== reply || x.text), { id: ++n, role: "notice", text: (e as Error).message }]);
    } finally {
      patch((x) => ({ ...x, pending: false }));
      setBusy(false);
      void status.refetch();
    }
  }
  async function cancel() {
    if (jobRef.current) await api.post(`/coach/jobs/${jobRef.current}/cancel`, {}).catch(() => undefined);
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(text);
  };
  const st = status.data;
  return (
    <>
      <PageHeader title="Ask the coach" subtitle="Questions about your money, answered from your own figures with the transactions cited." />
      {st && !st.configured && <Notice tone="warn" className="mb-4" title="The coach is not available">{st.message}</Notice>}
      {st?.configured && <p className="mb-3 text-xs text-faint">Answered by <b>{st.backend}</b> ({st.model}), up to {st.max_tool_calls} look-ups per question. The coach only sees redacted, already computed figures, and can only <em>propose</em> changes to your memory.</p>}
      <Card pad={false} className="flex min-h-[55dvh] flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5" aria-live="polite" aria-label="Conversation">
          {msgs.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 py-10 text-center">
              <Sparkles className="size-8 text-accent" aria-hidden />
              <p className="max-w-sm text-sm text-muted">Try one of these, or type your own question.</p>
              <div className="flex max-w-xl flex-wrap justify-center gap-2">{prompts.data?.prompts.map((p) => <button key={p.id} onClick={() => void ask(p.text, p.skill)} className="rounded-full border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-2" title={p.skill ? `Runs the ${p.skill} skill` : undefined}>{p.text}</button>)}</div>
            </div>
          ) : (
            <ul className="grid gap-3">
              {msgs.map((m) => (
                <li key={m.id} className={cn("flex", m.role === "user" && "justify-end")}>
                  {m.role === "notice" ? <Notice className="max-w-xl">{m.text}</Notice> : m.role === "user" ? (
                    <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-accent px-4 py-2.5 text-sm text-accent-fg">{m.text}</div>
                  ) : (
                    <div className="max-w-[92%] rounded-2xl border border-border bg-surface-2 px-4 py-2.5 text-sm" data-testid="coach-answer">
                      {(m.tools?.length ?? 0) > 0 && (
                        <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[12px] text-muted" data-testid="coach-tools">
                          <Eye className="size-3.5" aria-hidden />
                          {m.tools!.map((t) => <Badge key={t.id} tone={t.suspicious ? "warn" : t.ok === false ? "neg" : "neutral"} title={t.args || undefined}>{TOOL_LABEL[t.name] ?? t.name}</Badge>)}
                        </div>
                      )}
                      {m.text ? <CoachText text={m.text} /> : m.pending ? <span className="text-muted">{m.tools?.length ? "Looking at your figures…" : "Thinking…"}</span> : null}
                      {m.compliance?.flagged && (
                        <Notice tone="warn" className="mt-2" title="General information only">
                          <span data-testid="compliance-banner">{m.compliance.banner}</span>
                        </Notice>
                      )}
                      {m.text && <p className="mt-2 flex items-start gap-1.5 text-[12px] text-faint" data-testid="ai-label"><Bot className="mt-0.5 size-3.5 shrink-0" aria-hidden /><span>{m.compliance?.label ?? AI_LABEL}</span></p>}
                      {m.suspicious && (
                        <Notice tone="warn" className="mt-2" title="Suspicious text in your data">
                          <span className="inline-flex items-center gap-1"><ShieldAlert className="size-3.5" aria-hidden /></span> A merchant name or description looked like an instruction aimed at the coach. It was treated as data and ignored; a memory proposal from this answer needs a separate confirmation of each field.
                        </Notice>
                      )}
                      {(m.unverified?.length ?? 0) > 0 && <p className="mt-2 text-[12px] text-warn" data-testid="unverified">{m.unverified!.length} number{m.unverified!.length > 1 ? "s" : ""} in this answer could not be traced to a computed figure ({m.unverified!.slice(0, 5).join(", ")}): check before relying on {m.unverified!.length > 1 ? "them" : "it"}.</p>}
                      {m.proposals?.map((p) => (
                        <div key={p.id} className="mt-3 grid gap-1.5 rounded-lg border border-border bg-surface p-2.5">
                          <div className="text-[13px] font-medium">Proposal <Link className="text-accent hover:underline" to="/memory">{p.id}</Link>: nothing is changed yet</div>
                          <div className="text-[12px] text-muted">Review it in Memory, then accept it yourself in a terminal:</div>
                          {p.command && <CopyCommand command={p.command} />}
                        </div>
                      ))}
                      {m.usage && (
                        <Disclosure summary={<span className="text-[12px]">How this was answered</span>}>
                          <p className="text-[12px] text-muted" data-testid="coach-usage">
                            {m.usage.backend} · {m.usage.model} · {m.usage.tool_calls} look-up{m.usage.tool_calls === 1 ? "" : "s"} · {m.usage.tokens_in.toLocaleString()} tokens in / {m.usage.tokens_out.toLocaleString()} out
                            {m.usage.cost_usd ? ` · about $${m.usage.cost_usd.toFixed(4)}${m.usage.cost_is_estimate ? " (estimate" + (m.usage.backend === "claude-code" ? ", notional on a subscription)" : ")") : ""}` : ""} · {m.usage.duration_s.toFixed(1)} s
                          </p>
                        </Disclosure>
                      )}
                    </div>
                  )}
                </li>
              ))}
              <div ref={end} />
            </ul>
          )}
        </div>
        <form onSubmit={submit} className="flex gap-2 border-t border-border p-3 sm:p-4">
          <label className="sr-only" htmlFor="coach-q">Your question</label>
          <input id="coach-q" value={text} onChange={(e) => setText(e.target.value)} placeholder="Why was September high?" maxLength={4000} disabled={busy} className="min-h-11 flex-1 rounded-lg border border-border-strong bg-surface px-3 text-sm" />
          {busy ? (
            <Button type="button" onClick={() => void cancel()}><Square className="size-4" aria-hidden /> Cancel</Button>
          ) : (
            <Button type="submit" variant="primary" disabled={!text.trim() || st?.configured === false}><Send className="size-4" aria-hidden /> Ask</Button>
          )}
        </form>
      </Card>
    </>
  );
}
