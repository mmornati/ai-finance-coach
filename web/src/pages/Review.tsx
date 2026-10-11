import { useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Bot, Check, ListChecks, Sparkles } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Skeleton } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { DecisionRow } from "@/components/DecisionRow";
import { useDisclaimers, useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtPct } from "@/lib/format";

interface Item { key: string; name: string; category: string | null; confidence: number | null; source: string | null; reason: string; n: number; total: string; at_stake: string; banks: string[]; accounts: string[] }
interface AiPath { allowed: boolean; code: string; why: string }
interface AiStatus { web: AiPath; label: AiPath }
interface Suggestion { key: string; mode: "web" | "label"; model: string; merchant: string; category: string; confidence: number; evidence: string | null }
interface SuggestJob { id: string; key: string; state: "running" | "done" | "failed"; mode: "web" | "label" | null; suggestion: Suggestion | null; error: { code: string; message: string } | null; ai_label?: string; ai_label_short?: string }
const MAX_CONF = 0.7; // the threshold of the queue on screen: confirming uses the same one
const REASON: Record<string, ParseKeys<"categories">> = { low_confidence: "review.reason.low_confidence", uncategorized: "review.reason.uncategorized", not_labelled: "review.reason.not_labelled", held_back_person_like: "review.reason.held_back_person_like" };
const AI_ERROR: Record<string, ParseKeys<"categories">> = { person_like: "review.ai.error.person_like", backend_unavailable: "review.ai.error.backend_unavailable", unusable_answer: "review.ai.error.unusable_answer", busy: "review.ai.error.busy", decided_by_rule: "review.ai.error.decided_by_rule" };

export default function Review() {
  const q = useGet<{ total: number; items: Item[] }>("/review", { limit: 40, max_conf: MAX_CONF });
  const ai = useGet<AiStatus>("/review/ai", undefined, { staleTime: 60_000 });
  const { t } = useTranslation("categories");
  return (
    <>
      <PageHeader title={t("review.title")} subtitle={t("review.subtitle")} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => d.items.length === 0 ? <Card><EmptyState icon={<ListChecks className="size-6" />} title={t("review.emptyTitle")}>{t("review.emptyBody")}</EmptyState></Card> : (
          <div className="grid gap-3">
            <p className="text-sm text-muted">{t("review.queue", { count: d.total })}{d.total > d.items.length ? t("review.biggestShown", { n: d.items.length }) : ""}.</p>
            {d.items.map((i) => <Row key={i.key} i={i} ai={ai.data?.web && ai.data.label ? ai.data : undefined} />)}
          </div>
        )}
      </Async>
    </>
  );
}

function Row({ i, ai }: { i: Item; ai?: AiStatus }) {
  const [cat, setCat] = useState(i.category && i.category !== "other.uncategorized" ? i.category : "");
  const [job, setJob] = useState<SuggestJob | null>(null);
  const { t } = useTranslation("categories");
  const confirm = useWrite(() => api.post("/review/confirm", { key: i.key, max_conf: MAX_CONF }), { success: t("review.confirmed") });
  const correct = useWrite((category: string) => api.post("/review/correct", { key: i.key, category }), { success: t("review.saved") });
  // the proposal is not stored: nothing to refetch when it starts or ends
  const ask = useWrite(() => api.post<SuggestJob>("/review/suggest", { key: i.key, max_conf: MAX_CONF }), { invalidate: false, onSuccess: (j) => setJob(j) });
  const poll = useGet<SuggestJob>(`/review/suggest/${job?.id ?? ""}`, undefined, {
    enabled: job?.state === "running", staleTime: 0, refetchInterval: (q) => (q.state.data?.state ?? "running") === "running" ? 2000 : false,
  });
  const current = poll.data && poll.data.id === job?.id ? poll.data : job;
  const canConfirm = !!i.category && i.category !== "other.uncategorized" && i.source !== "knn" && i.source !== null;
  const keep = canConfirm && cat === i.category;
  const personLike = i.reason === "held_back_person_like";
  const aiUsable = !!ai && (ai.web.allowed || ai.label.allowed);
  const running = ask.isPending || current?.state === "running";
  return (
    <DecisionRow
      title={<Link to={`/transactions?merchant=${encodeURIComponent(i.key)}`} className="truncate text-[15px] font-semibold hover:underline">{i.name}</Link>}
      badge={<Badge tone={personLike ? "warn" : "neutral"}>{REASON[i.reason] ? t(REASON[i.reason]) : i.reason}</Badge>}
      meta={<>
        {t("review.transactions", { count: i.n })} · <Trans t={t} i18nKey="review.moved" components={{ amount: <Money v={i.at_stake} round /> }} /> · {i.accounts.join(", ")}
        {i.category && <> · <Trans t={t} i18nKey="review.currently" values={{ category: catLabel(i.category) }} components={{ b: <b className="text-text" /> }} />{i.confidence !== null && t("review.sure", { pct: fmtPct(i.confidence, 0) })}</>}
      </>}
      picker={<CategoryPicker value={cat} onChange={setCat} allowEmpty emptyLabel={t("review.choose")} />}
      actions={<>
        {keep
          ? <Button variant="primary" busy={confirm.isPending} onClick={() => confirm.mutate(undefined as never)}><Check className="size-4" aria-hidden /> {t("review.keep", { category: catLabel(i.category) })}</Button>
          : <Button variant="primary" disabled={!cat} busy={correct.isPending} onClick={() => correct.mutate(cat)}><Check className="size-4" aria-hidden /> {t("review.set")}</Button>}
        {!personLike && ai && (
          <Button disabled={!aiUsable} busy={running} onClick={() => ask.mutate(undefined as never)}
                  title={ai.web.allowed ? t("review.ai.hintWeb") : ai.label.allowed ? t("review.ai.hintLabel") : ai.label.why}>
            <Sparkles className="size-4" aria-hidden /> {running ? t("review.ai.running") : t("review.ai.ask")}
          </Button>
        )}
      </>}
    >
      {i.source === "knn" && <Notice className="mt-3">{t("review.knn")}</Notice>}
      {current?.state === "running" && <Notice className="mt-3">{current.mode === "web" ? t("review.ai.searchingWeb") : t("review.ai.asking")}</Notice>}
      {current?.state === "failed" && current.error && (
        <Notice tone="warn" className="mt-3">{AI_ERROR[current.error.code] ? t(AI_ERROR[current.error.code]) : current.error.message}</Notice>
      )}
      {current?.state === "done" && current.suggestion && (
        <SuggestionBox s={current.suggestion} job={current} saving={correct.isPending}
                       onAccept={() => { setCat(current.suggestion!.category); correct.mutate(current.suggestion!.category); }} />
      )}
    </DecisionRow>
  );
}

function SuggestionBox({ s, job, saving, onAccept }: { s: Suggestion; job: SuggestJob; saving: boolean; onAccept: () => void }) {
  const { t } = useTranslation("categories");
  // the EU AI Act label (E11-5): its wording lives only in src/coach/disclaimers.py
  const disclaimers = useDisclaimers();
  return (
    <div className="mt-3 rounded-lg border border-border bg-surface-2 px-3 py-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="neutral" title={job.ai_label ?? disclaimers?.ai_label}><Bot className="size-3" aria-hidden /> {job.ai_label_short ?? disclaimers?.ai_label_short}</Badge>
        <span>
          <Trans t={t} i18nKey="review.ai.proposal" values={{ category: catLabel(s.category), pct: fmtPct(s.confidence, 0), merchant: s.merchant }}
                 components={{ b: <b className="text-text" /> }} />
        </span>
        <span className="text-muted">· {s.mode === "web" ? t("review.ai.viaWeb") : t("review.ai.viaLabel")}</span>
      </div>
      {s.evidence && <p className="mt-1 text-muted">{s.evidence}</p>}
      <div className="mt-2">
        <Button size="sm" variant="primary" busy={saving} onClick={onAccept}><Check className="size-4" aria-hidden /> {t("review.ai.accept", { category: catLabel(s.category) })}</Button>
      </div>
    </div>
  );
}
