import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Bot, Eye, Send, ShieldAlert, Sparkles, Square } from "lucide-react";
import { Badge, Button, Card, Disclosure, Notice, PageHeader } from "@/components/ui";
import { CopyCommand } from "@/components/CopyCommand";
import { CoachText } from "@/components/CoachText";
import { useDisclaimers, useGet } from "@/api/hooks";
import { api, streamSSE } from "@/lib/api";
import { errorText } from "@/i18n/server";
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
// EU AI Act transparency (E11-5): every coach text is labelled. The server sends the label in the answer's language with the answer; while it
// streams, the label of the interface language (GET /meta/disclaimers). Its wording lives only in src/coach/disclaimers.py: never a translation key.

const TOOLS = [
  "coverage", "category_averages", "cashflow", "recurring", "price_changes", "anomalies", "forecast", "budget_status", "budget_suggestions", "calendar",
  "goals", "year_review", "transactions_search", "explain_transaction", "memory_context", "open_questions", "memory_propose", "add_insight",
  "monthly_review", "explain_spike", "subscription_audit", "cancellability", "savings_estimate", "mortgage_check", "what_if", "tax_candidates",
  "onboarding_status", "questions_propose",
] as const;
const toolKey = (name: string): ParseKeys<"coach"> | null => ((TOOLS as readonly string[]).includes(name) ? (`tool.${name}` as ParseKeys<"coach">) : null);
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
  const { t } = useTranslation("coach");
  const disclaimers = useDisclaimers();
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
      setMsgs((m) => [...m.filter((x) => x.id !== reply || x.text), { id: ++n, role: "notice", text: errorText(e) }]);
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
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      {st && !st.configured && <Notice tone="warn" className="mb-4" title={t("unavailable")}>{st.message}</Notice>}
      {st?.configured && <p className="mb-3 text-xs text-faint"><Trans t={t} i18nKey="status" values={{ backend: st.backend, model: st.model, max: st.max_tool_calls }} components={{ b: <b />, em: <em /> }} /></p>}
      <Card pad={false} className="flex min-h-[55dvh] flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5" aria-live="polite" aria-label={t("conversation")}>
          {msgs.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4 py-10 text-center">
              <Sparkles className="size-8 text-accent" aria-hidden />
              <p className="max-w-sm text-sm text-muted">{t("try")}</p>
              <div className="flex max-w-xl flex-wrap justify-center gap-2">{prompts.data?.prompts.map((p) => <button key={p.id} onClick={() => void ask(p.text, p.skill)} className="rounded-full border border-border-strong px-3 py-1.5 text-sm hover:bg-surface-2" title={p.skill ? t("runsSkill", { skill: p.skill }) : undefined}>{p.text}</button>)}</div>
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
                          {m.tools!.map((x) => <Badge key={x.id} tone={x.suspicious ? "warn" : x.ok === false ? "neg" : "neutral"} title={x.args || undefined}>{toolKey(x.name) ? t(toolKey(x.name)!) : x.name}</Badge>)}
                        </div>
                      )}
                      {m.text ? <CoachText text={m.text} /> : m.pending ? <span className="text-muted">{m.tools?.length ? t("lookingAtFigures") : t("thinking")}</span> : null}
                      {m.compliance?.flagged && (
                        <Notice tone="warn" className="mt-2" title={t("generalInfo")}>
                          <span data-testid="compliance-banner">{m.compliance.banner}</span>
                        </Notice>
                      )}
                      {m.text && <p className="mt-2 flex items-start gap-1.5 text-[12px] text-faint" data-testid="ai-label"><Bot className="mt-0.5 size-3.5 shrink-0" aria-hidden /><span>{m.compliance?.label ?? disclaimers?.ai_label}</span></p>}
                      {m.suspicious && (
                        <Notice tone="warn" className="mt-2" title={t("suspiciousTitle")}>
                          <span className="inline-flex items-center gap-1"><ShieldAlert className="size-3.5" aria-hidden /></span> {t("suspiciousBody")}
                        </Notice>
                      )}
                      {(m.unverified?.length ?? 0) > 0 && <p className="mt-2 text-[12px] text-warn" data-testid="unverified">{t("unverified", { count: m.unverified!.length, list: m.unverified!.slice(0, 5).join(", ") })}</p>}
                      {m.proposals?.map((p) => (
                        <div key={p.id} className="mt-3 grid gap-1.5 rounded-lg border border-border bg-surface p-2.5">
                          <div className="text-[13px] font-medium"><Trans t={t} i18nKey="proposal" values={{ id: p.id }} components={{ link: <Link className="text-accent hover:underline" to="/memory" /> }} /></div>
                          <div className="text-[12px] text-muted">{t("proposalHint")}</div>
                          {p.command && <CopyCommand command={p.command} />}
                        </div>
                      ))}
                      {m.usage && (
                        <Disclosure summary={<span className="text-[12px]">{t("howAnswered")}</span>}>
                          <p className="text-[12px] text-muted" data-testid="coach-usage">
                            {m.usage.backend} · {m.usage.model} · {t("usage.lookups", { count: m.usage.tool_calls })} · {t("usage.tokens", { in: m.usage.tokens_in.toLocaleString(), out: m.usage.tokens_out.toLocaleString() })}
                            {m.usage.cost_usd ? t("usage.cost", { cost: `$${m.usage.cost_usd.toFixed(4)}` }) + (m.usage.cost_is_estimate ? t(m.usage.backend === "claude-code" ? "usage.estimateNotional" : "usage.estimate") : "") : ""} · {t("usage.seconds", { s: m.usage.duration_s.toFixed(1) })}
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
          <label className="sr-only" htmlFor="coach-q">{t("question")}</label>
          <input id="coach-q" value={text} onChange={(e) => setText(e.target.value)} placeholder={t("placeholder")} maxLength={4000} disabled={busy} className="min-h-11 flex-1 rounded-lg border border-border-strong bg-surface px-3 text-sm" />
          {busy ? (
            <Button type="button" onClick={() => void cancel()}><Square className="size-4" aria-hidden /> {t("cancel")}</Button>
          ) : (
            <Button type="submit" variant="primary" disabled={!text.trim() || st?.configured === false}><Send className="size-4" aria-hidden /> {t("ask")}</Button>
          )}
        </form>
      </Card>
    </>
  );
}
