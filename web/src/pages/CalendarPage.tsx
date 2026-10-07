import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { Async, Badge, Button, Card, EmptyState, IconButton, Money, Notice, PageHeader, Skeleton } from "@/components/ui";
import { useScoped } from "@/api/hooks";
import { api } from "@/lib/api";
import { addMonthsKey, fmtDate, fmtMonth, fmtRelativeDays, getLocale, todayKey } from "@/lib/format";
import { cn, download } from "@/lib/utils";
import type { CalendarItem, CalendarResult } from "@/api/types";

const SRC: Record<string, { label: ParseKeys<"calendar">; color: string }> = {
  recurring: { label: "source.recurring", color: "var(--s1)" },
  liability: { label: "source.liability", color: "var(--s2)" },
  contract: { label: "source.contract", color: "var(--s7)" },
  consent: { label: "source.consent", color: "var(--s8)" },
  asset: { label: "source.asset", color: "var(--s4)" },
};
const CERTAINTY: Record<string, ParseKeys<"calendar">> = { observed: "certainty.observed", scheduled: "certainty.scheduled", deadline: "certainty.deadline", assumed: "certainty.assumed" };

export default function CalendarPage() {
  const [month, setMonth] = useState(todayKey());
  const q = useScoped<CalendarResult>("/calendar", { month });
  const isPast = month < todayKey();
  const { t } = useTranslation("calendar");
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} actions={<Button onClick={() => download(api.url("/calendar.ics", { days: 180 }))}><Download className="size-4" aria-hidden /> {t("download")}</Button>} />
      <div className="mb-3 flex items-center gap-1">
        <IconButton label={t("previous")} onClick={() => setMonth(addMonthsKey(month, -1))} disabled={month <= todayKey()}><ChevronLeft className="size-5" /></IconButton>
        <h2 className="min-w-40 text-center text-base font-semibold capitalize">{fmtMonth(month, "long")}</h2>
        <IconButton label={t("next")} onClick={() => setMonth(addMonthsKey(month, 1))} disabled={month >= addMonthsKey(todayKey(), 12)}><ChevronRight className="size-5" /></IconButton>
        {month !== todayKey() && <Button size="sm" variant="ghost" onClick={() => setMonth(todayKey())}>{t("today")}</Button>}
      </div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">{Object.entries(SRC).map(([k, v]) => <span key={k} className="inline-flex items-center gap-1.5"><span aria-hidden className="size-2.5 rounded-sm" style={{ background: v.color }} />{t(v.label)}{d.counts[k] ? ` (${d.counts[k]})` : ""}</span>)}</div>
            {isPast && <Notice>{t("pastMonths")}</Notice>}
            <Grid month={month} items={d.items} />
            <Agenda items={d.items} />
          </div>
        )}
      </Async>
    </>
  );
}

function Grid({ month, items }: { month: string; items: CalendarItem[] }) {
  const [y, m] = month.split("-").map(Number);
  const { t } = useTranslation("calendar");
  const by = useMemo(() => {
    const o: Record<string, CalendarItem[]> = {};
    items.forEach((i) => (o[i.date] ??= []).push(i));
    return o;
  }, [items]);
  const first = new Date(y, m - 1, 1);
  const lead = (first.getDay() + 6) % 7; // Monday first
  const days = new Date(y, m, 0).getDate();
  const cells = Array.from({ length: Math.ceil((lead + days) / 7) * 7 }, (_, i) => i - lead + 1);
  const wd = Array.from({ length: 7 }, (_, i) => new Intl.DateTimeFormat(getLocale(), { weekday: "short" }).format(new Date(2024, 0, 1 + i)));
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return (
    <Card pad={false} className="hidden sm:block">
      <div role="grid" aria-label={t("grid", { month: fmtMonth(month, "long") })}>
        <div role="row" className="grid grid-cols-7 border-b border-border text-center text-xs font-medium text-muted">{wd.map((d) => <div key={d} role="columnheader" className="py-2">{d}</div>)}</div>
        <div className="grid grid-cols-7">
          {cells.map((n, i) => {
            const inMonth = n >= 1 && n <= days;
            const iso = inMonth ? `${month}-${String(n).padStart(2, "0")}` : "";
            const its = by[iso] ?? [];
            return (
              <div key={i} role="gridcell" className={cn("min-h-24 border-b border-r border-border p-1.5 [&:nth-child(7n)]:border-r-0", !inMonth && "bg-surface-2/40")}>
                {inMonth && <div className={cn("mb-1 text-xs", iso === today ? "inline-flex size-5 items-center justify-center rounded-full bg-accent font-semibold text-accent-fg" : "text-muted")}>{n}</div>}
                <ul className="grid gap-0.5">
                  {its.slice(0, 3).map((it) => (
                    <li key={it.ref + it.kind} title={it.amount ? t("amountTitle", { title: it.title, amount: it.amount }) : it.title} className="flex items-center gap-1 truncate rounded px-1 text-[11px] leading-5" style={{ background: `color-mix(in srgb, ${SRC[it.source]?.color ?? "var(--s1)"} 16%, transparent)` }}>
                      <span aria-hidden className="size-1.5 shrink-0 rounded-full" style={{ background: SRC[it.source]?.color }} />
                      <span className="truncate">{it.title}</span>
                    </li>
                  ))}
                  {its.length > 3 && <li className="px-1 text-[11px] text-faint">{t("more", { n: its.length - 3 })}</li>}
                </ul>
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
}

function Agenda({ items }: { items: CalendarItem[] }) {
  const { t } = useTranslation("calendar");
  if (!items.length) return <Card><EmptyState title={t("empty")} /></Card>;
  const dates = [...new Set(items.map((i) => i.date))];
  return (
    <Card title={t("agenda")} pad={false}>
      <ul className="divide-y divide-border">
        {dates.map((dte) => (
          <li key={dte} className="px-4 py-2.5 sm:px-5">
            <div className="mb-1 text-xs font-semibold text-muted">{fmtDate(dte, "weekday")} <span className="font-normal text-faint">· {fmtRelativeDays(items.find((i) => i.date === dte)!.days_until)}</span></div>
            <ul className="grid gap-1.5">
              {items.filter((i) => i.date === dte).map((i) => (
                <li key={i.ref + i.kind} className="flex items-center gap-3 text-sm">
                  <span aria-hidden className="size-2.5 shrink-0 rounded-sm" style={{ background: SRC[i.source]?.color }} />
                  <span className="min-w-0 flex-1 truncate">{i.title}{i.account_label && <span className="text-xs text-faint"> · {i.account_label}</span>}</span>
                  {i.certainty !== "observed" && <Badge>{CERTAINTY[i.certainty] ? t(CERTAINTY[i.certainty]) : i.certainty}</Badge>}
                  {i.amount !== null && <Money v={i.amount} colored className="font-medium" />}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </Card>
  );
}
