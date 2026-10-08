import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Download, Filter, Search, X } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, Money, PageHeader, Select, Skeleton, Spinner } from "@/components/ui";
import { CategoryPicker } from "@/components/CategoryPicker";
import { TxPanel } from "@/components/TxPanel";
import { useFilters } from "@/api/hooks";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import { useScope } from "@/lib/app";
import { catLabel, fmtDate, fmtMoney, fmtNumber, getLocale } from "@/lib/format";
import { purposeLabel } from "@/i18n/server";
import { cn, debounce, download } from "@/lib/utils";
import type { TxItem, TxList } from "@/api/types";

const PAGE = 60;
const KEYS = ["q", "date_from", "date_to", "account", "owner", "purpose", "category", "group", "merchant", "entity", "amount_min", "amount_max", "direction", "tag", "source", "event", "exclude_transfers", "sort"] as const;

function presets(): { id: string; label: ParseKeys<"transactions">; from: string; to: string }[] {
  const d = new Date();
  const iso = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  const first = new Date(d.getFullYear(), d.getMonth(), 1);
  const lastPrev = new Date(d.getFullYear(), d.getMonth(), 0);
  const firstPrev = new Date(d.getFullYear(), d.getMonth() - 1, 1);
  const d90 = new Date(d.getTime() - 90 * 86400000);
  return [
    { id: "month", label: "preset.month", from: iso(first), to: "" },
    { id: "prev", label: "preset.prev", from: iso(firstPrev), to: iso(lastPrev) },
    { id: "90", label: "preset.days90", from: iso(d90), to: "" },
    { id: "year", label: "preset.year", from: `${d.getFullYear()}-01-01`, to: "" },
  ];
}

export default function Transactions() {
  const [sp, setSp] = useSearchParams();
  const { params: scope } = useScope();
  const filters = useFilters();
  const [open, setOpen] = useState(false);
  const txKey = sp.get("tx");
  const { t } = useTranslation("transactions");
  const { t: tc } = useTranslation();
  // the classification step that decided a category: the shared wording of the transaction panel, else the code itself
  const sourceLabel = (s: string) => (i18n.exists(`tx.source.${s}`) ? tc(`tx.source.${s}` as "tx.source.none") : s);

  const f: Record<string, string> = {};
  KEYS.forEach((k) => {
    const v = sp.get(k);
    if (v) f[k] = v;
  });
  const apiParams = { ...scope, ...f } as Record<string, unknown>; // the person switch applies unless the page filter says otherwise
  const nFilters = Object.keys(f).filter((k) => !["q", "sort"].includes(k)).length;

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    setSp(next, { replace: true });
  };

  const q = useInfiniteQuery<TxList>({
    queryKey: ["/transactions", apiParams],
    queryFn: ({ pageParam, signal }) => api.get<TxList>("/transactions", { ...apiParams, limit: PAGE, offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    staleTime: 20_000,
  });

  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => e[0]?.isIntersecting && q.hasNextPage && !q.isFetchingNextPage && void q.fetchNextPage(), { rootMargin: "600px" });
    io.observe(el);
    return () => io.disconnect();
  }, [q]);

  const items = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const totals = q.data?.pages[0]?.totals;

  // the search box types freely and updates the URL after a pause
  const [text, setText] = useState(f.q ?? "");
  const push = useMemo(() => debounce((v: string) => set({ q: v }), 300), [sp]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setText(sp.get("q") ?? ""), [sp.get("q")]); // eslint-disable-line react-hooks/exhaustive-deps

  const exportUrl = api.url("/transactions/export.csv", { ...apiParams, locale: getLocale() });

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <Button onClick={() => download(exportUrl)} disabled={!totals?.count}>
            <Download className="size-4" aria-hidden /> {t("exportCsv")}
          </Button>
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1">
          <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input aria-label={t("search")} type="search" value={text} onChange={(e) => { setText(e.target.value); push(e.target.value); }} placeholder={t("searchPlaceholder")} className="pl-9" />
        </div>
        <Button onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <Filter className="size-4" aria-hidden /> {t("filters")}{nFilters > 0 && <Badge tone="info">{nFilters}</Badge>}
        </Button>
        <div className="flex gap-1 overflow-x-auto">
          {presets().map((p) => {
            const on = f.date_from === p.from && (f.date_to ?? "") === p.to;
            return (
              <Button key={p.id} size="sm" className="shrink-0 whitespace-nowrap" variant={on ? "primary" : "secondary"} onClick={() => set(on ? { date_from: "", date_to: "" } : { date_from: p.from, date_to: p.to })} aria-pressed={on}>
                {t(p.label)}
              </Button>
            );
          })}
        </div>
      </div>

      {open && (
        <Card className="mb-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t("filter.from")}>{(id) => <Input id={id} type="date" value={f.date_from ?? ""} onChange={(e) => set({ date_from: e.target.value })} />}</Field>
            <Field label={t("filter.to")}>{(id) => <Input id={id} type="date" value={f.date_to ?? ""} onChange={(e) => set({ date_to: e.target.value })} />}</Field>
            <Field label={t("filter.account")}>
              {(id) => (
                <Select id={id} value={f.account ?? ""} onChange={(e) => set({ account: e.target.value })}>
                  <option value="">{t("filter.allAccounts")}</option>
                  {filters.data?.accounts.map((a) => <option key={a.uid} value={a.uid}>{a.label}</option>)}
                </Select>
              )}
            </Field>
            <Field label={t("filter.person")}>
              {(id) => (
                <Select id={id} value={f.owner ?? ""} onChange={(e) => set({ owner: e.target.value })}>
                  <option value="">{t("filter.anyone")}</option>
                  {filters.data?.owners.map((o) => <option key={o} value={o}>{o}</option>)}
                </Select>
              )}
            </Field>
            <Field label={t("filter.purpose")}>
              {(id) => (
                <Select id={id} value={f.purpose ?? ""} onChange={(e) => set({ purpose: e.target.value })}>
                  <option value="">{t("filter.any")}</option>
                  {filters.data?.purposes.map((p) => <option key={p} value={p}>{purposeLabel(p)}</option>)}
                </Select>
              )}
            </Field>
            <Field label={t("filter.category")}>{(id) => <CategoryPicker id={id} allowEmpty value={f.category ?? f.group ?? ""} onChange={(v) => (v && !v.includes(".") ? set({ group: v, category: "" }) : set({ category: v, group: "" }))} includeGroups />}</Field>
            <Field label={t("filter.merchant")}>{(id) => <Input id={id} value={f.merchant ?? ""} onChange={(e) => set({ merchant: e.target.value })} />}</Field>
            <Field label={t("filter.direction")}>
              {(id) => (
                <Select id={id} value={f.direction ?? "all"} onChange={(e) => set({ direction: e.target.value === "all" ? "" : e.target.value })}>
                  <option value="all">{t("filter.inAndOut")}</option>
                  <option value="out">{t("filter.out")}</option>
                  <option value="in">{t("filter.in")}</option>
                </Select>
              )}
            </Field>
            <Field label={t("filter.amountMin")}>{(id) => <Input id={id} inputMode="decimal" value={f.amount_min ?? ""} onChange={(e) => set({ amount_min: e.target.value.replace(/[^0-9.,]/g, "").replace(",", ".") })} />}</Field>
            <Field label={t("filter.amountMax")}>{(id) => <Input id={id} inputMode="decimal" value={f.amount_max ?? ""} onChange={(e) => set({ amount_max: e.target.value.replace(/[^0-9.,]/g, "").replace(",", ".") })} />}</Field>
            <Field label={t("filter.tag")}>
              {(id) => (
                <Select id={id} value={f.tag ?? ""} onChange={(e) => set({ tag: e.target.value })}>
                  <option value="">{t("filter.any")}</option>
                  {filters.data?.tags.map((x) => <option key={x} value={x}>{x}</option>)}
                </Select>
              )}
            </Field>
            <Field label={t("filter.source")}>
              {(id) => (
                <Select id={id} value={f.source ?? ""} onChange={(e) => set({ source: e.target.value })}>
                  <option value="">{t("filter.anySource")}</option>
                  {filters.data?.sources.map((x) => <option key={x} value={x}>{sourceLabel(x)}</option>)}
                </Select>
              )}
            </Field>
            <Field label={t("filter.event")}>
              {(id) => (
                <Select id={id} value={f.event ?? ""} onChange={(e) => set({ event: e.target.value })}>
                  <option value="">{t("filter.any")}</option>
                  {filters.data?.events.map((x) => <option key={x.id} value={x.id}>{x.title ?? x.id}</option>)}
                </Select>
              )}
            </Field>
            <label className="flex min-h-10 items-center gap-2 self-end text-sm">
              <input type="checkbox" checked={f.exclude_transfers === "true"} onChange={(e) => set({ exclude_transfers: e.target.checked ? "true" : "" })} /> {t("filter.hideTransfers")}
            </label>
            <Field label={t("filter.sort")}>
              {(id) => (
                <Select id={id} value={f.sort ?? "date_desc"} onChange={(e) => set({ sort: e.target.value === "date_desc" ? "" : e.target.value })}>
                  <option value="date_desc">{t("filter.newest")}</option>
                  <option value="date_asc">{t("filter.oldest")}</option>
                  <option value="amount_desc">{t("filter.largest")}</option>
                  <option value="amount_asc">{t("filter.smallest")}</option>
                </Select>
              )}
            </Field>
          </div>
          <div className="mt-3 flex justify-end">
            <Button variant="ghost" onClick={() => setSp(new URLSearchParams(), { replace: true })}><X className="size-4" aria-hidden /> {t("filter.clear")}</Button>
          </div>
        </Card>
      )}

      {totals && (
        <div className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 rounded-xl border border-border bg-surface px-4 py-3 text-sm sm:grid-cols-4" aria-live="polite">
          <div><span className="text-muted">{t("totals.matching")}</span> <b className="num">{fmtNumber(totals.count)}</b></div>
          <div><span className="text-muted">{t("totals.net")}</span> <Money v={totals.sum} signed colored className="font-semibold" /></div>
          <div><span className="text-muted">{t("totals.in")}</span> <Money v={totals.income} colored className="font-semibold" /></div>
          <div><span className="text-muted">{t("totals.out")}</span> <Money v={totals.outflow} colored className="font-semibold" /></div>
        </div>
      )}

      {q.isPending ? (
        <div className="grid gap-2" aria-busy="true">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
      ) : q.isError ? (
        <ErrorState error={q.error} retry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <Card><EmptyState title={t("emptyTitle")}>{t("emptyBody")}</EmptyState></Card>
      ) : (
        <>
          <TxTable items={items} onOpen={(k) => set({ tx: k })} personName={(id) => filters.data?.members.find((m) => m.id === id)?.name.split(" ")[0] ?? id} />
          <div ref={sentinel} className="py-6 text-center text-xs text-faint">
            {q.isFetchingNextPage ? <Spinner label={t("loadingMore")} /> : q.hasNextPage ? <Button onClick={() => void q.fetchNextPage()}>{t("loadMore")}</Button> : t("shown", { n: fmtNumber(items.length) })}
          </div>
        </>
      )}
      <TxPanel txKey={txKey} onClose={() => set({ tx: "" })} />
    </>
  );
}

export function TxTable({ items, onOpen, personName }: { items: TxItem[]; onOpen: (key: string) => void; personName?: (id: string) => string }) {
  const { t: tr } = useTranslation("transactions");
  let lastDay = "";
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <ul className="divide-y divide-border">
        {items.map((t) => {
          const head = t.date !== lastDay;
          lastDay = t.date;
          return (
            <li key={t.tx_key}>
              {head && <div className="bg-surface-2/60 px-4 py-1 text-[11px] font-semibold uppercase tracking-wide text-faint">{fmtDate(t.date, "weekday")}</div>}
              <button type="button" onClick={() => onOpen(t.tx_key)} className="grid w-full grid-cols-[1fr_auto] items-center gap-x-4 gap-y-0.5 px-4 py-2.5 text-left hover:bg-surface-2/60 focus-visible:bg-surface-2/60 sm:grid-cols-[minmax(0,2.2fr)_minmax(0,1.3fr)_minmax(0,1fr)_auto]">
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{t.entity}</span>
                  <span className="block truncate text-xs text-muted sm:hidden">{catLabel(t.category)} · {t.account_label}</span>
                  <span className="hidden truncate text-xs text-faint sm:block">{t.description}</span>
                </span>
                <span className="num col-start-2 row-start-1 text-right text-sm font-semibold sm:col-start-4"><Money v={t.amount} colored signed /></span>
                <span className="hidden min-w-0 sm:block">
                  <Badge tone={t.source === "override" || t.source === "user" || t.source === "memory" ? "info" : "neutral"}>{catLabel(t.category)}</Badge>
                  <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
                    {t.tags.slice(0, 2).map((x) => <Badge key={x} tone="warn">{x}</Badge>)}
                    {t.transfer_linked && <Badge tone="pos">{tr("row.transfer")}</Badge>}
                    {t.person && t.person !== "joint" && <Badge tone="info" title={tr("row.attributedTo")}>{personName ? personName(t.person) : t.person}</Badge>}
                    {t.split && <Badge>{tr("row.split")}</Badge>}
                  </span>
                </span>
                <span className="hidden truncate text-xs text-muted sm:block">{t.account_label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
