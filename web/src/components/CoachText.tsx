import { Fragment, ReactNode, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { FileText, Receipt, Repeat } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Money } from "@/components/ui";
import type { ResolvedRef } from "@/api/types";

/** The evidence refs the coach cites. `h_` = a hashed transaction (the mapping to the real transaction stays on the server), `rec_` =
 *  a recurring series, `anm_` / `chg_` = an anomaly / a price change, `p-…` = a memory proposal. */
export const REF_RE = /\b(h_[0-9a-f]{10}|(?:rec|anm|chg)_[0-9a-f]{6,}|p-\d{8}-[0-9a-f]{6})\b/g;

export function refsIn(text: string): string[] {
  return [...new Set([...text.matchAll(REF_RE)].map((m) => m[1]))];
}

/** Resolve refs once for a whole message (one request). */
export function useResolved(refs: string[]) {
  const wanted = refs.filter((r) => !r.startsWith("p-")).sort();
  return useQuery<Record<string, ResolvedRef | null>>({
    queryKey: ["coach-resolve", wanted.join(",")],
    enabled: wanted.length > 0,
    staleTime: 60_000,
    queryFn: async () => (await api.post<{ refs: Record<string, ResolvedRef | null> }>("/coach/resolve", { refs: wanted })).refs,
  });
}

export function RefChip({ id, info }: { id: string; info?: ResolvedRef | null }) {
  const { t } = useTranslation();
  const base = "inline-flex items-center gap-1 rounded-full border border-border-strong bg-surface px-2 py-0.5 align-baseline text-[12px] font-medium hover:bg-surface-2";
  if (id.startsWith("p-")) {
    return (
      <Link to="/memory" className={base} title={t("coachText.proposal", { id })}>
        <FileText className="size-3" aria-hidden />
        {id}
      </Link>
    );
  }
  if (!info) return <span className={`${base} text-faint`} title={t("coachText.unresolved")}>{id.slice(0, 8)}…</span>;
  if (info.kind === "transaction") {
    return (
      <Link to={`/transactions?tx=${encodeURIComponent(info.tx_key ?? "")}`} className={base} title={`${info.merchant ?? ""} · ${info.category ?? ""}`}>
        <Receipt className="size-3" aria-hidden />
        {info.date ? fmtDate(info.date, "dayMonth") : t("coachText.transaction")} <Money v={info.amount} signed />
      </Link>
    );
  }
  return (
    <Link to={info.link ?? "/insights"} className={base} title={id}>
      <Repeat className="size-3" aria-hidden />
      {info.kind === "series" ? t("coachText.series") : info.kind === "anomaly" ? t("coachText.anomaly") : t("coachText.priceChange")}
    </Link>
  );
}

function inline(text: string, resolved: Record<string, ResolvedRef | null> | undefined, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  const parts: ReactNode[] = [];
  for (const m of text.matchAll(REF_RE)) {
    if (m.index! > last) parts.push(text.slice(last, m.index));
    parts.push(<RefChip key={`${key}-${m.index}`} id={m[1]} info={resolved?.[m[1]]} />);
    last = m.index! + m[0].length;
  }
  parts.push(text.slice(last));
  parts.forEach((p, i) => {
    if (typeof p !== "string") return void out.push(p);
    p.split(/(\*\*[^*]+\*\*)/g).forEach((seg, j) => {
      if (seg.startsWith("**") && seg.endsWith("**") && seg.length > 4) out.push(<strong key={`${key}-${i}-${j}`}>{seg.slice(2, -2)}</strong>);
      else if (seg) out.push(<Fragment key={`${key}-${i}-${j}`}>{seg}</Fragment>);
    });
  });
  return out;
}

/** Answer text as safe React nodes (never HTML): paragraphs, bullet lists, **bold**, and clickable evidence refs. */
export function CoachText({ text }: { text: string }) {
  const refs = useMemo(() => refsIn(text), [text]);
  const q = useResolved(refs);
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="grid gap-2 break-words">
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
          return (
            <ul key={i} className="ml-5 list-disc">
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*[-*]\s+/, ""), q.data, `${i}-${j}`)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {inline(b, q.data, String(i))}
          </p>
        );
      })}
    </div>
  );
}
