import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Check, Target } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Segmented, Skeleton, Stat } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtPct } from "@/lib/format";
import type { EvalRun, GoldItem, GoldSummary } from "@/api/types";

type Strategy = "money" | "stratified";
const ORIGIN: Record<string, ParseKeys<"quality">> = { manual: "gold.origin.manual", merchant_label: "gold.origin.merchant_label", annotation: "gold.origin.annotation", override: "gold.origin.override", split: "gold.origin.split" };
const SOURCE: Record<string, ParseKeys<"quality">> = { llm: "gold.row.source.llm", llm_web: "gold.row.source.llm_web", knn: "gold.row.source.knn", rule: "gold.row.source.rule", entity: "gold.row.source.entity", none: "gold.row.source.none" };
function usePct() {
  const { t } = useTranslation("quality");
  return (v: unknown) => (typeof v === "number" ? fmtPct(v, 1) : t("gold.notAvailable"));
}

export default function Gold() {
  const { t } = useTranslation("quality");
  const pct = usePct();
  const [strategy, setStrategy] = useState<Strategy>("money");
  const q = useGet<GoldSummary>("/gold", { n: 8, strategy });
  const rescore = useWrite(() => api.post("/eval/classify", {}), { success: t("gold.rescored") });
  const bootstrap = useWrite(() => api.post("/gold/bootstrap", {}), { success: t("gold.bootstrapped") });
  return (
    <>
      <PageHeader title={t("gold.title")} subtitle={t("gold.subtitle")} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => { const v2 = d.latest?.summary.method === "v2"; return (
          <div className="grid gap-4">
            <Card title={t("gold.card")} action={<div className="flex gap-2"><Button size="sm" busy={bootstrap.isPending} onClick={() => bootstrap.mutate(undefined as never)}>{t("gold.bootstrap")}</Button><Button size="sm" variant="primary" busy={rescore.isPending} onClick={() => rescore.mutate(undefined as never)}>{t("gold.score")}</Button></div>}>
              <div className="grid gap-4 sm:grid-cols-4">
                <Stat label={t("gold.transactions")} value={d.counts.total} hint={t("gold.categories", { count: d.counts.categories })} />
                <Stat label={t("gold.accuracy")} value={pct(v2 ? d.latest?.summary.accuracy_tx : null)} hint={v2 ? t("gold.accuracyHint", { money: pct(d.latest?.summary.accuracy_money), rows: d.latest?.summary.n ?? 0 }) : undefined} />
                <Stat label={t("gold.aiOnly")} value={pct(v2 ? d.latest?.summary.llm_accuracy_tx : null)} hint={v2 ? t("gold.aiOnlyHint", { count: d.latest?.summary.llm_n ?? 0 }) : undefined} />
                <Stat label={t("gold.pipeline")} value={pct(v2 ? d.latest?.summary.pipeline_accuracy_tx : null)} hint={t("gold.pipelineHint")} />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5" aria-label={t("gold.originsAria")}>
                {Object.entries(d.counts.by_origin).map(([k, v]) => <Badge key={k} title={t("gold.originTitle")}>{t("gold.originCount", { origin: ORIGIN[k] ? t(ORIGIN[k]) : k, count: v })}</Badge>)}
              </div>
              {d.latest && !v2 && <Notice tone="warn" className="mt-3"><Trans t={t} i18nKey="gold.oldMethod" components={{ code: <code /> }} /></Notice>}
              {!d.latest && d.counts.total > 0 && <Notice className="mt-3">{t("gold.notScored")}</Notice>}
              {d.latest && <p className="mt-3 text-xs text-faint"><Trans t={t} i18nKey="gold.lastScored" values={{ date: fmtDate(d.latest.ts.slice(0, 10), "dayMonth"), count: d.latest.n }} components={{ code: <code /> }} /></p>}
              <Runs runs={d.runs} />
            </Card>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-base font-semibold">{t("gold.toLabel")}</h2>
              <Segmented<Strategy> label={t("gold.pick")} value={strategy} onChange={setStrategy} options={[{ value: "money", label: t("gold.pickMoney") }, { value: "stratified", label: t("gold.pickStratified") }]} />
            </div>
            {d.sample.length === 0 ? <Card><EmptyState icon={<Target className="size-6" />} title={t("gold.emptyTitle")}>{t("gold.emptyBody")}</EmptyState></Card> : (
              <ul className="grid gap-3" aria-label={t("gold.toLabel")}>{d.sample.map((i) => <Row key={i.tx_key} i={i} />)}</ul>
            )}
          </div>
        ); }}
      </Async>
    </>
  );
}

function Runs({ runs }: { runs: EvalRun[] }) {
  const { t } = useTranslation("quality");
  const pct = usePct();
  if (runs.length < 2) return null;
  return (
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer text-muted">{t("gold.runs.summary")}</summary>
      <table className="mt-2 w-full text-xs">
        <thead className="text-left text-muted"><tr><th scope="col" className="py-1 font-medium">{t("gold.runs.when")}</th><th scope="col" className="py-1 text-right font-medium">{t("gold.runs.transactions")}</th><th scope="col" className="py-1 text-right font-medium">{t("gold.runs.byTransaction")}</th><th scope="col" className="py-1 text-right font-medium">{t("gold.runs.byMoney")}</th></tr></thead>
        <tbody>{runs.map((r) => <tr key={r.id} className="border-t border-border"><td className="py-1">{fmtDate(r.ts.slice(0, 10), "dayMonth")} · {r.label}{r.summary.method === "v2" ? "" : t("gold.runs.deprecated")}</td><td className="num py-1 text-right">{r.n}</td><td className="num py-1 text-right">{pct(r.summary.accuracy_tx)}</td><td className="num py-1 text-right">{pct(r.summary.accuracy_money)}</td></tr>)}</tbody>
      </table>
    </details>
  );
}

function Row({ i }: { i: GoldItem }) {
  const { t } = useTranslation("quality");
  const [cat, setCat] = useState(i.category !== "other.uncategorized" ? i.category : "");
  const [skipped, setSkipped] = useState(false);
  const label = useWrite((category: string) => api.post("/gold/label", { tx_key: i.tx_key, category }), { success: t("gold.row.added") });
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
              {hasLabel ? <Trans t={t} i18nKey="gold.row.currently" values={{ category: catLabel(i.category) }} components={{ b: <b className="text-text" /> }} /> : t("gold.row.noLabel")}
              {" "}<Badge title={t("gold.row.sourceTitle")}>{SOURCE[i.source] ? t(SOURCE[i.source]) : i.source}{i.confidence !== null ? ` ${fmtPct(i.confidence, 0)}` : ""}</Badge>
            </p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
            <div className="min-w-48 flex-1"><CategoryPicker value={cat} onChange={setCat} allowEmpty emptyLabel={t("gold.row.choose")} /></div>
            <Button variant="primary" disabled={!cat} busy={label.isPending} onClick={() => label.mutate(cat)}><Check className="size-4" aria-hidden /> {hasLabel && cat === i.category ? t("gold.row.itIs", { category: catLabel(i.category) }) : t("gold.row.set")}</Button>
            <Button variant="ghost" onClick={() => setSkipped(true)}>{t("gold.row.skip")}</Button>
          </div>
        </div>
      </Card>
    </li>
  );
}
