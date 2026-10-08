import { useEffect, useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Link } from "react-router";
import { Check, ChevronRight, CircleDot, Link2Off, Tag } from "lucide-react";
import { Badge, Button, Chips, Dialog, DiffView, Disclosure, Field, Input, Money, Notice, Segmented, Select, Spinner, Textarea } from "./ui";
import { CategoryPicker } from "./CategoryPicker";
import { useFilters, useGet, usePeople, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { errorText, serverLabel, tServer, tServerList } from "@/i18n/server";
import { catLabel, fmtDate, fmtMoney } from "@/lib/format";
import type { AttributionWhy, CategoryChangePreview, EditResult, TxDetail } from "@/api/types";
import { cn } from "@/lib/utils";

/** Where a category came from (the server sends the code; an unknown code is shown as it is). */
const SOURCE_KEYS: Record<string, ParseKeys> = {
  override: "tx.source.override", transfer_link: "tx.source.transfer_link", type_rule: "tx.source.type_rule", user: "tx.source.user", rule: "tx.source.rule",
  entity: "tx.source.entity", llm: "tx.source.llm", llm_web: "tx.source.llm_web", knn: "tx.source.knn", memory: "tx.source.memory", split: "tx.source.split", none: "tx.source.none",
};

export function TxPanel({ txKey, onClose }: { txKey: string | null; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={!!txKey} onClose={onClose} side title={t("tx.title")} description={t("tx.subtitle")}>
      {txKey && <Body txKey={txKey} onClose={onClose} />}
    </Dialog>
  );
}

function Body({ txKey, onClose }: { txKey: string; onClose: () => void }) {
  const q = useGet<TxDetail>("/transactions/detail", { tx_key: txKey }, { staleTime: 0 });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <Notice tone="neg">{errorText(q.error)}</Notice>;
  return <Detail d={q.data} onClose={onClose} />;
}

function Detail({ d, onClose }: { d: TxDetail; onClose: () => void }) {
  const { t: tr } = useTranslation();
  const t = d.transaction;
  const item = d.item;
  return (
    <div className="grid gap-5">
      <section>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="truncate text-lg font-semibold">{item?.entity ?? t.description}</div>
            <div className="text-[13px] text-muted">{fmtDate(t.date, "long")} · {t.account}{t.bank ? ` · ${t.bank}` : ""}</div>
          </div>
          <Money v={t.amount} colored className="text-xl font-semibold" />
        </div>
        <p className="mt-2 break-words rounded-lg bg-surface-2 px-3 py-2 text-[13px] text-muted">{t.description}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Badge tone="info">{catLabel(d.final.category)}</Badge>
          <Badge>{SOURCE_KEYS[d.final.source] ? tr(SOURCE_KEYS[d.final.source]) : d.final.source}</Badge>
          <Chips items={d.final.tags} />
          {d.final.event && <Badge tone="warn">{tr("tx.eventBadge", { event: d.final.event })}</Badge>}
          {item?.transfer_linked && <Badge tone="pos">{tr("tx.internalTransfer")}</Badge>}
        </div>
      </section>

      <Why d={d} />
      <Attribution txKey={t.tx_key} />
      <ChangeCategory d={d} onDone={onClose} />
      <Annotate d={d} />
      <Extras d={d} />
    </div>
  );
}

function Why({ d }: { d: TxDetail }) {
  const { t } = useTranslation();
  return (
    <Disclosure summary={t("tx.why.summary")} defaultOpen={false}>
      <ol className="grid gap-2 text-[13px]">
        {d.steps.map((s) => (
          <li key={s.step} className={cn("flex gap-2", !s.applies && "opacity-60")}>
            <span className="mt-0.5">{s.decides ? <Check className="size-4 text-pos" aria-label={t("tx.why.decidesAria")} /> : s.applies ? <CircleDot className="size-4 text-warn" aria-label={t("tx.why.appliesAria")} /> : <ChevronRight className="size-4 text-faint" aria-hidden />}</span>
            <div>
              <div className="font-medium">{serverLabel("explainStep", s.step_code, s.step)}{s.decides && <span className="ml-2 text-pos">{t("tx.why.decides")}</span>}{s.applies && !s.decides && <span className="ml-2 text-warn">{t("tx.why.outranked")}</span>}</div>
              <div className="text-muted">{tServer(s.detail_msg, s.detail)}</div>
            </div>
          </li>
        ))}
        {d.memory.annotations.length > 0 && (
          <li className="border-t border-border pt-2">
            <div className="font-medium">{t("tx.why.annotations")}</div>
            <ul className="mt-1 grid gap-1 text-muted">
              {d.memory.annotations.map((a) => (
                <li key={a.id}>{a.matched ? (a.winner ? "✓ " : "~ ") : "· "}<code className="text-xs">{a.id}</code>: {tServer(a.reason_msg, a.reason)}</li>
              ))}
            </ul>
          </li>
        )}
        {!d.consistent && <Notice tone="warn">{t("tx.why.inconsistent")}</Notice>}
      </ol>
    </Disclosure>
  );
}

type Scope = "transaction" | "merchant" | "memory";

function ChangeCategory({ d, onDone }: { d: TxDetail; onDone: () => void }) {
  const { t } = useTranslation();
  const [category, setCategory] = useState(d.final.category);
  const [scope, setScope] = useState<Scope>("merchant");
  const [match, setMatch] = useState<"merchant" | "transaction">("merchant");
  const [note, setNote] = useState("");
  const txKey = d.transaction.tx_key;
  const changed = category !== d.final.category;
  const body = useMemo(() => ({ tx_key: txKey, category, scope, match, note: note || undefined }), [txKey, category, scope, match, note]);
  const [preview, setPreview] = useState<CategoryChangePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPreview(null);
    setError(null);
    if (!changed) return;
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      api.post<CategoryChangePreview>("/transactions/category", body, { dry_run: true }).then((r) => !ctl.signal.aborted && setPreview(r)).catch((e) => !ctl.signal.aborted && setError(errorText(e)));
    }, 250);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [body, changed]);

  const apply = useWrite(() => api.post<CategoryChangePreview>("/transactions/category", body, { dry_run: false }), {
    success: t("tx.change.updated"),
    onSuccess: () => onDone(),
  });
  const a = preview?.affected;
  const sameMerchant = d.same_merchant_count;
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4">
      <h3 className="text-sm font-semibold">{t("tx.change.title")}</h3>
      <Field label={t("tx.change.newCategory")}>{(id) => <CategoryPicker id={id} value={category} onChange={setCategory} />}</Field>
      {changed && (
        <>
          <fieldset className="grid gap-1.5">
            <legend className="mb-1 text-xs font-medium text-muted">{t("tx.change.applyTo")}</legend>
            {([
              ["transaction", t("tx.change.onlyThis"), t("tx.change.onlyThisHint")],
              ["merchant", t("tx.change.merchant", { entity: d.item?.entity ?? t("tx.change.merchantFallback") }), t("tx.change.merchantHint", { count: sameMerchant })],
              ["memory", t("tx.change.memory"), t("tx.change.memoryHint")],
            ] as [Scope, string, string][]).map(([v, label, hint]) => (
              <label key={v} className={cn("flex cursor-pointer gap-2.5 rounded-lg border px-3 py-2", scope === v ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-2")}>
                <input type="radio" name="scope" className="mt-1" checked={scope === v} onChange={() => setScope(v)} />
                <span>
                  <span className="block text-sm font-medium">{label}</span>
                  <span className="block text-xs text-muted">{hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {scope === "memory" && (
            <div className="grid gap-3">
              <Segmented label={t("tx.change.annotationAppliesTo")} value={match} onChange={setMatch} options={[{ value: "merchant", label: t("tx.change.theMerchant") }, { value: "transaction", label: t("tx.change.onlyThisTransaction") }]} />
              <Field label={t("tx.change.note")}>{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("tx.change.notePlaceholder")} />}</Field>
            </div>
          )}
          {error && <Notice tone="neg">{error}</Notice>}
          {!preview && !error && <Spinner label={t("tx.change.checking")} />}
          {preview && a && (
            <div className="grid gap-2" aria-live="polite">
              <p className="text-sm" data-testid="effect">
                {a.count === 0 ? (
                  <Trans i18nKey={scope === "transaction" ? "tx.effect.noneThisTransaction" : "tx.effect.noneNoTransaction"} values={{ category: catLabel(category) }} components={{ b: <b /> }} />
                ) : scope === "transaction" ? (
                  <Trans i18nKey="tx.effect.one" values={{ category: catLabel(category) }} components={{ b: <b /> }} />
                ) : (
                  <>
                    <Trans i18nKey="tx.effect.change" count={a.count} values={{ category: catLabel(category) }} components={{ b: <b /> }} />
                    {a.total && <> {t("tx.effect.total", { total: fmtMoney(a.total) })}</>}
                    {a.already ? t("tx.effect.already", { count: a.already }) : "."}
                  </>
                )}
              </p>
              {a.from_categories && a.from_categories.length > 0 && <p className="text-xs text-muted">{t("tx.effect.currently", { list: a.from_categories.map((f) => `${catLabel(f.category)} ×${f.n}`).join(", ") })}</p>}
              {tServerList(preview.warnings, preview.warnings_msg).map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
              {scope === "memory" && <DiffView diff={preview.diff} />}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCategory(d.final.category)}>{t("tx.change.cancel")}</Button>
            <Button variant="primary" busy={apply.isPending} disabled={!preview || !preview.changed} onClick={() => apply.mutate(undefined as never)}>{t("tx.change.apply")}</Button>
          </div>
        </>
      )}
    </section>
  );
}

function Annotate({ d }: { d: TxDetail }) {
  const { t: tr } = useTranslation();
  const filters = useFilters();
  const [tags, setTags] = useState<string[]>([]);
  const [custom, setCustom] = useState("");
  const [event, setEvent] = useState("");
  const [note, setNote] = useState("");
  const dirty = tags.length > 0 || !!event || !!note;
  const body = useMemo(() => ({ tx_keys: [d.transaction.tx_key], tags, event: event || undefined, note: note || undefined }), [d.transaction.tx_key, tags, event, note]);
  const [preview, setPreview] = useState<EditResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setPreview(null);
    setError(null);
    if (!tags.length && !event) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      api.post<EditResult>("/annotations", body, { dry_run: true }).then((r) => !ctl.signal.aborted && setPreview(r)).catch((e) => !ctl.signal.aborted && setError(errorText(e)));
    }, 250);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [body, tags.length, event]);
  const apply = useWrite(() => api.post<EditResult>("/annotations", body, { dry_run: false }), {
    success: tr("tx.annotate.saved"),
    onSuccess: () => {
      setTags([]);
      setEvent("");
      setNote("");
    },
  });
  const toggle = (t: string) => setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));
  const known = ["one_off", "reimbursable", "capital", "savings", "investment", "exclude_from_averages"];
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Tag className="size-4" aria-hidden /> {tr("tx.annotate.title")}</h3>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={tr("tx.annotate.tags")}>
        {[...known, ...tags.filter((t) => !known.includes(t))].map((t) => (
          <button key={t} type="button" aria-pressed={tags.includes(t)} onClick={() => toggle(t)} className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", tags.includes(t) ? "border-accent bg-accent-soft text-accent" : "border-border-strong text-muted hover:bg-surface-2")}>
            {t}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <Input aria-label={tr("tx.annotate.customTag")} value={custom} onChange={(e) => setCustom(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder={tr("tx.annotate.customTagPlaceholder")} className="flex-1" />
        <Button onClick={() => { if (custom) { setTags((c) => (c.includes(custom) ? c : [...c, custom])); setCustom(""); } }}>{tr("tx.annotate.add")}</Button>
      </div>
      <Field label={tr("tx.annotate.event")} hint={tr("tx.annotate.eventHint")}>
        {(id) => (
          <Select id={id} value={event} onChange={(e) => setEvent(e.target.value)}>
            <option value="">{tr("tx.annotate.noEvent")}</option>
            {(filters.data?.events ?? []).map((e) => <option key={e.id} value={e.id}>{e.title ?? e.id}</option>)}
          </Select>
        )}
      </Field>
      <Field label={tr("tx.annotate.note")}>{(id) => <Textarea id={id} value={note} onChange={(e) => setNote(e.target.value)} placeholder={tr("tx.annotate.notePlaceholder")} />}</Field>
      {error && <Notice tone="neg">{error}</Notice>}
      {preview && (
        <div className="grid gap-2">
          {tServerList(preview.warnings, preview.warnings_msg).map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          <DiffView diff={preview.diff} />
        </div>
      )}
      {!tags.length && !event && note && <Notice>{tr("tx.annotate.noteAlone")}</Notice>}
      <div className="flex justify-end">
        <Button variant="primary" disabled={!preview || !dirty} busy={apply.isPending} onClick={() => apply.mutate(undefined as never)}>{tr("tx.annotate.save")}</Button>
      </div>
    </section>
  );
}

function Extras({ d }: { d: TxDetail }) {
  const { t } = useTranslation();
  const clear = useWrite(() => api.post("/transactions/override/clear", { tx_key: d.transaction.tx_key }), { success: t("tx.extras.overrideRemoved") });
  const clearSplit = useWrite(() => api.post("/transactions/split/clear", { tx_key: d.transaction.tx_key }), { success: t("tx.extras.splitRemoved") });
  const unlink = useWrite(() => api.post("/transfers/unlink", { ref: d.transaction.tx_key }), { success: t("tx.extras.unlinked") });
  if (!d.override && !d.split && !d.transfer_link) return null;
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4 text-sm">
      {d.override && (
        <div className="flex items-center justify-between gap-3">
          <span><Trans i18nKey="tx.extras.overridden" values={{ category: catLabel(d.override.category) }} components={{ b: <b /> }} /></span>
          <Button size="sm" busy={clear.isPending} onClick={() => clear.mutate(undefined as never)}>{t("tx.extras.removeOverride")}</Button>
        </div>
      )}
      {d.split && (
        <div>
          <div className="mb-1 flex items-center justify-between"><b>{t("tx.extras.split")}</b><Button size="sm" busy={clearSplit.isPending} onClick={() => clearSplit.mutate(undefined as never)}>{t("tx.extras.removeSplit")}</Button></div>
          <ul className="text-[13px] text-muted">{d.split.map((s, i) => <li key={i}>{catLabel(s.category)}: {fmtMoney(s.amount)}{s.note ? ` (${s.note})` : ""}</li>)}</ul>
        </div>
      )}
      {d.transfer_link && (
        <div className="flex items-center justify-between gap-3">
          <span>{t("tx.extras.transferLinked", { method: d.transfer_link.method })}</span>
          <Button size="sm" busy={unlink.isPending} onClick={() => unlink.mutate(undefined as never)}><Link2Off className="size-3.5" /> {t("tx.extras.unlink")}</Button>
        </div>
      )}
    </section>
  );
}

/** E14-3: whose a transaction is, why, and the manual reassignment (recorded and reversible). Shown only when the household has members. */
const SOURCE_TEXT: Record<AttributionWhy["source"], ParseKeys> = { manual: "tx.attr.source.manual", rule: "tx.attr.source.rule", account: "tx.attr.source.account", none: "tx.attr.source.none" };

function Attribution({ txKey }: { txKey: string }) {
  const { t } = useTranslation();
  const people = usePeople();
  const q = useGet<AttributionWhy>("/transactions/attribution", { tx_key: txKey }, { staleTime: 0, enabled: people.members.length > 0 });
  const [member, setMember] = useState("");
  const set = useWrite(() => api.post("/transactions/person", { tx_key: txKey, member }), { success: t("tx.attr.reassigned") });
  const clear = useWrite(() => api.post("/transactions/person/clear", { tx_key: txKey }), { success: t("tx.attr.backToRules") });
  if (people.members.length === 0 || !q.data) return null;
  const a = q.data;
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4 text-sm" aria-label={t("tx.attr.region")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span><Trans i18nKey="tx.attr.belongs" values={{ name: a.person ? people.name(a.person) : t("tx.attr.nobody") }} components={{ b: <b /> }} /> <span className="text-muted">· {t(SOURCE_TEXT[a.source])}{a.rule ? t("tx.attr.ruleSuffix", { rule: a.rule }) : ""}</span></span>
        {a.manual && <Button size="sm" busy={clear.isPending} onClick={() => clear.mutate(undefined as never)}>{t("tx.attr.undo")}</Button>}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label={t("tx.attr.reassignTo")} className="min-w-40 flex-1">
          {(id) => (
            <Select id={id} value={member} onChange={(e) => setMember(e.target.value)}>
              <option value="">{t("tx.attr.choose")}</option>
              {people.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              <option value="joint">{t("tx.attr.joint")}</option>
            </Select>
          )}
        </Field>
        <Button size="sm" variant="primary" disabled={!member || member === a.person} busy={set.isPending} onClick={() => set.mutate(undefined as never)}>{t("tx.attr.reassign")}</Button>
      </div>
      <Disclosure summary={t("tx.attr.why")} defaultOpen={false}>
        <ul className="grid gap-1 text-[13px] text-muted">
          {a.manual && <li>{t("tx.attr.manual", { member: people.name(a.manual.member), by: a.manual.set_by, date: fmtDate(a.manual.set_at.slice(0, 10), "medium") })}{a.manual.note ? t("tx.attr.manualNote", { note: a.manual.note }) : ""}</li>}
          {a.rules.map((r) => <li key={r.id}>{r.matched ? "✓ " : "· "}<Trans i18nKey="tx.attr.rule" values={{ id: r.id, member: people.name(r.member) }} components={{ code: <code className="text-xs" /> }} />{r.matched ? "" : t("tx.attr.ruleWhyNot", { why: tServer(r.why_not_msg, r.why_not ?? "") })}</li>)}
          <li>{t("tx.attr.account", { account: a.account ?? t("tx.attr.unknownAccount"), owner: a.account_owner ?? t("tx.attr.noOwner") })}{a.account_owner_member ? t("tx.attr.ownerMember", { name: people.name(a.account_owner_member) }) : ""}{a.card_last4 ? t("tx.attr.card", { last4: a.card_last4 }) : ""}</li>
          {a.history.map((h) => <li key={h.id}>{t("tx.attr.history", { date: fmtDate(h.at.slice(0, 10), "medium"), action: h.action, from: h.old_member ? people.name(h.old_member) : t("tx.attr.rules"), to: h.new_member ? people.name(h.new_member) : t("tx.attr.rules"), by: h.by })}</li>)}
        </ul>
      </Disclosure>
    </section>
  );
}
