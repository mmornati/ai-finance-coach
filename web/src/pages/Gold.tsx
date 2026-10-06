import { useState } from "react";
import { Check, Target } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Segmented, Skeleton, Stat } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtPct } from "@/lib/format";
import type { EvalRun, GoldItem, GoldSummary } from "@/api/types";

type Strategy = "money" | "stratified";
const ORIGIN: Record<string, string> = { manual: "Labelled by you here", merchant_label: "Your merchant labels", annotation: "Your memory annotations", override: "Your per-transaction categories", split: "Your splits" };
const SOURCE: Record<string, string> = { llm: "AI label", llm_web: "AI label (web)", knn: "Guessed from a similar merchant", rule: "Rule", entity: "Canonical merchant", none: "No label" };
const pct = (v: unknown) => (typeof v === "number" ? fmtPct(v, 1) : "n/a");

export default function Gold() {
  const [strategy, setStrategy] = useState<Strategy>("money");
  const q = useGet<GoldSummary>("/gold", { n: 8, strategy });
  const rescore = useWrite(() => api.post("/eval/classify", {}), { success: "Gold set re-scored" });
  const bootstrap = useWrite(() => api.post("/gold/bootstrap", {}), { success: "Added what you already decided" });
  return (
    <>
      <PageHeader title="Gold set" subtitle="Transactions whose category you have confirmed. They are only used to measure how good the automatic categories are: confirming one here never changes a category in the app." />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => { const v2 = d.latest?.summary.method === "v2"; return (
          <div className="grid gap-4">
            <Card title="The gold set" action={<div className="flex gap-2"><Button size="sm" busy={bootstrap.isPending} onClick={() => bootstrap.mutate(undefined as never)}>Add what I already decided</Button><Button size="sm" variant="primary" busy={rescore.isPending} onClick={() => rescore.mutate(undefined as never)}>Score it now</Button></div>}>
              <div className="grid gap-4 sm:grid-cols-4">
                <Stat label="Transactions" value={d.counts.total} hint={`${d.counts.categories} categories`} />
                <Stat label="Classifier accuracy on merchant-level truth" value={pct(v2 ? d.latest?.summary.accuracy_tx : null)} hint={v2 ? `by transaction, ${pct(d.latest?.summary.accuracy_money)} by money, ${d.latest?.summary.n ?? 0} rows` : undefined} />
                <Stat label="AI labels only" value={pct(v2 ? d.latest?.summary.llm_accuracy_tx : null)} hint={v2 ? `${d.latest?.summary.llm_n ?? 0} transactions` : undefined} />
                <Stat label="Pipeline with memory (what you see)" value={pct(v2 ? d.latest?.summary.pipeline_accuracy_tx : null)} hint="should stay near 100 %: a check of your memory and rules" />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Where the gold labels come from">
                {Object.entries(d.counts.by_origin).map(([k, v]) => <Badge key={k} title="Where this truth comes from">{ORIGIN[k] ?? k}: {v}</Badge>)}
              </div>
              {d.latest && !v2 && <Notice tone="warn" className="mt-3">The last score was made with the first method (<code>method_v1</code>, deprecated: it mixed the classifier and your memory in one figure). Press “Score it now”.</Notice>}
              {!d.latest && d.counts.total > 0 && <Notice className="mt-3">Not scored yet: press “Score it now”. The score is computed here, offline, and stored so that you can compare it over time.</Notice>}
              {d.latest && <p className="mt-3 text-xs text-faint">Last scored {fmtDate(d.latest.ts.slice(0, 10), "dayMonth")} on {d.latest.n} transactions. The classifier is scored only on merchant-level truth, never on a rule that depends on the transaction's context, and a label of yours is never compared with itself (<code>coach eval classify</code> lists them).</p>}
              <Runs runs={d.runs} />
            </Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-base font-semibold">Transactions to label</h2>
              <Segmented<Strategy> label="How to pick" value={strategy} onChange={setStrategy} options={[{ value: "money", label: "Biggest money first" }, { value: "stratified", label: "A bit of everything" }]} />
            </div>
            {d.sample.length === 0 ? <Card><EmptyState icon={<Target className="size-6" />} title="Nothing left to label">Every transaction an automatic step decided is already in the gold set.</EmptyState></Card> : (
              <ul className="grid gap-3" aria-label="Transactions to label">{d.sample.map((i) => <Row key={i.tx_key} i={i} />)}</ul>
            )}
          </div>
        ); }}
      </Async>
    </>
  );
}

function Runs({ runs }: { runs: EvalRun[] }) {
  if (runs.length < 2) return null;
  return (
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer text-muted">Previous scores</summary>
      <table className="mt-2 w-full text-xs">
        <thead className="text-left text-muted"><tr><th scope="col" className="py-1 font-medium">When</th><th scope="col" className="py-1 text-right font-medium">Transactions</th><th scope="col" className="py-1 text-right font-medium">By transaction</th><th scope="col" className="py-1 text-right font-medium">By money</th></tr></thead>
        <tbody>{runs.map((r) => <tr key={r.id} className="border-t border-border"><td className="py-1">{fmtDate(r.ts.slice(0, 10), "dayMonth")} · {r.label}{r.summary.method === "v2" ? "" : " · method_v1 (deprecated)"}</td><td className="num py-1 text-right">{r.n}</td><td className="num py-1 text-right">{pct(r.summary.accuracy_tx)}</td><td className="num py-1 text-right">{pct(r.summary.accuracy_money)}</td></tr>)}</tbody>
      </table>
    </details>
  );
}

function Row({ i }: { i: GoldItem }) {
  const [cat, setCat] = useState(i.category !== "other.uncategorized" ? i.category : "");
  const [skipped, setSkipped] = useState(false);
  const label = useWrite((category: string) => api.post("/gold/label", { tx_key: i.tx_key, category }), { success: "Added to the gold set" });
  if (skipped) return null;
  const hasLabel = i.category !== "other.uncategorized";
  return (
    <li>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-[15px] font-semibold" title={i.description}>{i.description || i.merchant || i.merchant_key}</p>
            <p className="mt-1 text-[13px] text-muted">
              {fmtDate(i.date, "dayMonth")} · <Money v={i.amount} colored /> · {i.account}
              {hasLabel ? <> · currently <b className="text-text">{catLabel(i.category)}</b></> : <> · no label yet</>}
              {" "}<Badge title="Which step decided the current category">{SOURCE[i.source] ?? i.source}{i.confidence !== null ? ` ${fmtPct(i.confidence, 0)}` : ""}</Badge>
            </p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            <div className="min-w-48 flex-1"><CategoryPicker value={cat} onChange={setCat} allowEmpty emptyLabel="Choose a category…" /></div>
            <Button variant="primary" disabled={!cat} busy={label.isPending} onClick={() => label.mutate(cat)}><Check className="size-4" aria-hidden /> {hasLabel && cat === i.category ? `It is ${catLabel(i.category)}` : "Set"}</Button>
            <Button variant="ghost" onClick={() => setSkipped(true)}>Skip</Button>
          </div>
        </div>
      </Card>
    </li>
  );
}
