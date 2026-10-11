import { Fragment, ReactNode, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { FileText, Receipt, Repeat } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Money } from "@/components/ui";
import { cn } from "@/lib/utils";
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
    p.split(INLINE_RE).forEach((seg, j) => {
      const k = `${key}-${i}-${j}`;
      if (/^\*\*[^*]+\*\*$/.test(seg)) out.push(<strong key={k}>{seg.slice(2, -2)}</strong>);
      else if (/^`[^`]+`$/.test(seg)) out.push(<code key={k} className="rounded bg-surface-2 px-1 text-[0.92em]">{seg.slice(1, -1)}</code>);
      else if (/^\*[^*\s][^*]*\*$/.test(seg)) out.push(<em key={k}>{seg.slice(1, -1)}</em>);
      else if (seg) out.push(<Fragment key={k}>{seg}</Fragment>);
    });
  });
  return out;
}

/** **bold**, `code` and *italic* (never _italic_: category keys such as food.work_meals hold underscores). */
const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET_RE = /^\s*[-*+]\s+/;
const NUMBERED_RE = /^\s*\d+[.)]\s+/;

type Block = { kind: "h"; level: number; text: string } | { kind: "ul" | "ol"; items: string[] } | { kind: "p"; lines: string[] };

/** A small, line-based Markdown subset: headings, bullet and numbered lists, paragraphs (a blank line or a heading / list ends one). */
function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const last = blocks[blocks.length - 1];
    const h = HEADING_RE.exec(line);
    if (!line.trim()) blocks.push({ kind: "p", lines: [] });
    else if (h) blocks.push({ kind: "h", level: h[1].length, text: h[2] });
    else if (BULLET_RE.test(line) || NUMBERED_RE.test(line)) {
      const kind = BULLET_RE.test(line) ? "ul" : "ol";
      const item = line.replace(kind === "ul" ? BULLET_RE : NUMBERED_RE, "");
      if (last?.kind === kind) last.items.push(item);
      else blocks.push({ kind, items: [item] });
    } else if (last?.kind === "p") last.lines.push(line);
    else if ((last?.kind === "ul" || last?.kind === "ol") && /^\s+\S/.test(line)) last.items[last.items.length - 1] += ` ${line.trim()}`;
    else blocks.push({ kind: "p", lines: [line] });
  }
  return blocks.filter((b) => b.kind !== "p" || b.lines.length > 0);
}

const HEADING_CLASS = ["", "text-base font-semibold text-text", "text-[15px] font-semibold text-text", "text-sm font-semibold text-text"];

/** Answer text as safe React nodes (never HTML): headings, paragraphs, bullet and numbered lists, **bold**, *italic*, `code`, and clickable evidence refs. */
export function CoachText({ text }: { text: string }) {
  const refs = useMemo(() => refsIn(text), [text]);
  const q = useResolved(refs);
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <div className="grid gap-2 break-words">
      {blocks.map((b, i) => {
        if (b.kind === "h") {
          const Tag = `h${Math.min(b.level + 2, 6)}` as "h3";
          return <Tag key={i} className={cn(HEADING_CLASS[Math.min(b.level, 3)], i > 0 && "mt-2")}>{inline(b.text, q.data, String(i))}</Tag>;
        }
        if (b.kind !== "p") {
          const List = b.kind;
          return (
            <List key={i} className={cn("ml-5 grid gap-0.5", b.kind === "ul" ? "list-disc" : "list-decimal")}>
              {b.items.map((l, j) => (
                <li key={j}>{inline(l, q.data, `${i}-${j}`)}</li>
              ))}
            </List>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {inline(b.lines.join("\n"), q.data, String(i))}
          </p>
        );
      })}
    </div>
  );
}
