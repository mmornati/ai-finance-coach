import { useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Check, ListChecks } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, Money, Notice, PageHeader, Skeleton } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtPct } from "@/lib/format";

interface Item { key: string; name: string; category: string | null; confidence: number | null; source: string | null; reason: string; n: number; total: string; at_stake: string; banks: string[]; accounts: string[] }
const MAX_CONF = 0.7; // the threshold of the queue on screen: confirming uses the same one
const REASON: Record<string, ParseKeys<"categories">> = { low_confidence: "review.reason.low_confidence", uncategorized: "review.reason.uncategorized", not_labelled: "review.reason.not_labelled", held_back_person_like: "review.reason.held_back_person_like" };

export default function Review() {
  const q = useGet<{ total: number; items: Item[] }>("/review", { limit: 40, max_conf: MAX_CONF });
  const { t } = useTranslation("categories");
  return (
    <>
      <PageHeader title={t("review.title")} subtitle={t("review.subtitle")} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => d.items.length === 0 ? <Card><EmptyState icon={<ListChecks className="size-6" />} title={t("review.emptyTitle")}>{t("review.emptyBody")}</EmptyState></Card> : (
          <div className="grid gap-3">
            <p className="text-sm text-muted">{t("review.queue", { count: d.total })}{d.total > d.items.length ? t("review.biggestShown", { n: d.items.length }) : ""}.</p>
            {d.items.map((i) => <Row key={i.key} i={i} />)}
          </div>
        )}
      </Async>
    </>
  );
}

function Row({ i }: { i: Item }) {
  const [cat, setCat] = useState(i.category && i.category !== "other.uncategorized" ? i.category : "");
  const { t } = useTranslation("categories");
  const confirm = useWrite(() => api.post("/review/confirm", { key: i.key, max_conf: MAX_CONF }), { success: t("review.confirmed") });
  const correct = useWrite(() => api.post("/review/correct", { key: i.key, category: cat }), { success: t("review.saved") });
  const canConfirm = !!i.category && i.category !== "other.uncategorized" && i.source !== "knn" && i.source !== null;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/transactions?merchant=${encodeURIComponent(i.key)}`} className="truncate text-[15px] font-semibold hover:underline">{i.name}</Link>
            <Badge tone={i.reason === "held_back_person_like" ? "warn" : "neutral"}>{REASON[i.reason] ? t(REASON[i.reason]) : i.reason}</Badge>
          </div>
          <p className="mt-1 text-[13px] text-muted">
            {t("review.transactions", { count: i.n })} · <Trans t={t} i18nKey="review.moved" components={{ amount: <Money v={i.at_stake} round /> }} /> · {i.accounts.join(", ")}
            {i.category && <> · <Trans t={t} i18nKey="review.currently" values={{ category: catLabel(i.category) }} components={{ b: <b className="text-text" /> }} />{i.confidence !== null && t("review.sure", { pct: fmtPct(i.confidence, 0) })}</>}
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <div className="min-w-48 flex-1"><CategoryPicker value={cat} onChange={setCat} allowEmpty emptyLabel={t("review.choose")} /></div>
          <Button variant="primary" disabled={!cat} busy={correct.isPending} onClick={() => correct.mutate(undefined as never)}>{t("review.set")}</Button>
          {canConfirm && <Button busy={confirm.isPending} onClick={() => confirm.mutate(undefined as never)}><Check className="size-4" aria-hidden /> {t("review.keep", { category: catLabel(i.category) })}</Button>}
        </div>
      </div>
      {i.source === "knn" && <Notice className="mt-3">{t("review.knn")}</Notice>}
    </Card>
  );
}
