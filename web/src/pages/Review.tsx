import { useState } from "react";
import { Link } from "react-router";
import { Check, ListChecks } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Skeleton } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtPct } from "@/lib/format";

interface Item { key: string; name: string; category: string | null; confidence: number | null; source: string | null; reason: string; n: number; total: string; at_stake: string; banks: string[]; accounts: string[] }
const MAX_CONF = 0.7; // the threshold of the queue on screen: confirming uses the same one
const REASON: Record<string, string> = { low_confidence: "Unsure label", uncategorized: "Uncategorized", not_labelled: "Not labelled yet", held_back_person_like: "May be a person (never sent to an AI)" };

export default function Review() {
  const q = useGet<{ total: number; items: Item[] }>("/review", { limit: 40, max_conf: MAX_CONF });
  return (
    <>
      <PageHeader title="Merchants to review" subtitle="Labels the app is unsure about, biggest money first. Confirming or correcting one teaches it for every transaction with that merchant." />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => d.items.length === 0 ? <Card><EmptyState icon={<ListChecks className="size-6" />} title="Nothing to review">Every merchant is labelled by you, by a rule or with enough confidence.</EmptyState></Card> : (
          <div className="grid gap-3">
            <p className="text-sm text-muted">{d.total} merchant{d.total > 1 ? "s" : ""} in the queue{d.total > d.items.length ? `, the ${d.items.length} biggest shown` : ""}.</p>
            {d.items.map((i) => <Row key={i.key} i={i} />)}
          </div>
        )}
      </Async>
    </>
  );
}

function Row({ i }: { i: Item }) {
  const [cat, setCat] = useState(i.category && i.category !== "other.uncategorized" ? i.category : "");
  const confirm = useWrite(() => api.post("/review/confirm", { key: i.key, max_conf: MAX_CONF }), { success: "Label confirmed" });
  const correct = useWrite(() => api.post("/review/correct", { key: i.key, category: cat }), { success: "Category saved" });
  const canConfirm = !!i.category && i.category !== "other.uncategorized" && i.source !== "knn" && i.source !== null;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/transactions?merchant=${encodeURIComponent(i.key)}`} className="truncate text-[15px] font-semibold hover:underline">{i.name}</Link>
            <Badge tone={i.reason === "held_back_person_like" ? "warn" : "neutral"}>{REASON[i.reason] ?? i.reason}</Badge>
          </div>
          <p className="mt-1 text-[13px] text-muted">
            {i.n} transaction{i.n > 1 ? "s" : ""} · <Money v={i.at_stake} round /> moved · {i.accounts.join(", ")}
            {i.category && <> · currently <b className="text-text">{catLabel(i.category)}</b>{i.confidence !== null && ` (${fmtPct(i.confidence, 0)} sure)`}</>}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <div className="min-w-48 flex-1"><CategoryPicker value={cat} onChange={setCat} allowEmpty emptyLabel="Choose a category…" /></div>
          <Button variant="primary" disabled={!cat} busy={correct.isPending} onClick={() => correct.mutate(undefined as never)}>Set</Button>
          {canConfirm && <Button busy={confirm.isPending} onClick={() => confirm.mutate(undefined as never)}><Check className="size-4" aria-hidden /> Keep {catLabel(i.category)}</Button>}
        </div>
      </div>
      {i.source === "knn" && <Notice className="mt-3">This label was guessed from a similar merchant: please pick the category yourself.</Notice>}
    </Card>
  );
}
