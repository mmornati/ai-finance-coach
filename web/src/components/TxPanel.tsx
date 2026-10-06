import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { Check, ChevronRight, CircleDot, Link2Off, Tag } from "lucide-react";
import { Badge, Button, Chips, Dialog, DiffView, Disclosure, Field, Input, Money, Notice, Segmented, Select, Spinner, Textarea } from "./ui";
import { CategoryPicker } from "./CategoryPicker";
import { useFilters, useGet, usePeople, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney } from "@/lib/format";
import type { AttributionWhy, CategoryChangePreview, EditResult, TxDetail } from "@/api/types";
import { cn } from "@/lib/utils";

const SOURCE_LABEL: Record<string, string> = {
  override: "your override for this transaction", transfer_link: "an internal transfer between your accounts", type_rule: "the kind of operation",
  user: "your label for this merchant", rule: "a built-in rule", entity: "the canonical merchant's default", llm: "automatic label (AI)", llm_web: "automatic label (AI + web)",
  knn: "automatic label (similar merchant)", memory: "a memory annotation", split: "a split over categories", none: "no label yet",
};

export function TxPanel({ txKey, onClose }: { txKey: string | null; onClose: () => void }) {
  return (
    <Dialog open={!!txKey} onClose={onClose} side title="Transaction" description="Why it has this category, and how to change it">
      {txKey && <Body txKey={txKey} onClose={onClose} />}
    </Dialog>
  );
}

function Body({ txKey, onClose }: { txKey: string; onClose: () => void }) {
  const q = useGet<TxDetail>("/transactions/detail", { tx_key: txKey }, { staleTime: 0 });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <Notice tone="neg">{q.error.message}</Notice>;
  return <Detail d={q.data} onClose={onClose} />;
}

function Detail({ d, onClose }: { d: TxDetail; onClose: () => void }) {
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
          <Badge>{SOURCE_LABEL[d.final.source] ?? d.final.source}</Badge>
          <Chips items={d.final.tags} />
          {d.final.event && <Badge tone="warn">event: {d.final.event}</Badge>}
          {item?.transfer_linked && <Badge tone="pos">internal transfer</Badge>}
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
  return (
    <Disclosure summary="Why this category?" defaultOpen={false}>
      <ol className="grid gap-2 text-[13px]">
        {d.steps.map((s) => (
          <li key={s.step} className={cn("flex gap-2", !s.applies && "opacity-60")}>
            <span className="mt-0.5">{s.decides ? <Check className="size-4 text-pos" aria-label="decides" /> : s.applies ? <CircleDot className="size-4 text-warn" aria-label="applies but outranked" /> : <ChevronRight className="size-4 text-faint" aria-hidden />}</span>
            <div>
              <div className="font-medium">{s.step}{s.decides && <span className="ml-2 text-pos">decides</span>}{s.applies && !s.decides && <span className="ml-2 text-warn">outranked</span>}</div>
              <div className="text-muted">{s.detail}</div>
            </div>
          </li>
        ))}
        {d.memory.annotations.length > 0 && (
          <li className="border-t border-border pt-2">
            <div className="font-medium">Memory annotations (first match wins)</div>
            <ul className="mt-1 grid gap-1 text-muted">
              {d.memory.annotations.map((a) => (
                <li key={a.id}>{a.matched ? (a.winner ? "✓ " : "~ ") : "· "}<code className="text-xs">{a.id}</code>: {a.reason}</li>
              ))}
            </ul>
          </li>
        )}
        {!d.consistent && <Notice tone="warn">This chain disagrees with the classifier's own answer. Please report it.</Notice>}
      </ol>
    </Disclosure>
  );
}

type Scope = "transaction" | "merchant" | "memory";

function ChangeCategory({ d, onDone }: { d: TxDetail; onDone: () => void }) {
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
    const t = setTimeout(() => {
      api.post<CategoryChangePreview>("/transactions/category", body, { dry_run: true }).then((r) => !ctl.signal.aborted && setPreview(r)).catch((e) => !ctl.signal.aborted && setError(e.message));
    }, 250);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [body, changed]);

  const apply = useWrite(() => api.post<CategoryChangePreview>("/transactions/category", body, { dry_run: false }), {
    success: "Category updated",
    onSuccess: () => onDone(),
  });
  const a = preview?.affected;
  const sameMerchant = d.same_merchant_count;
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4">
      <h3 className="text-sm font-semibold">Change the category</h3>
      <Field label="New category">{(id) => <CategoryPicker id={id} value={category} onChange={setCategory} />}</Field>
      {changed && (
        <>
          <fieldset className="grid gap-1.5">
            <legend className="mb-1 text-xs font-medium text-muted">Apply to</legend>
            {([
              ["transaction", "This transaction only", "A one-off exception; the merchant keeps its category."],
              ["merchant", `Every "${d.item?.entity ?? "merchant"}" transaction`, `Teaches the app this merchant (${sameMerchant} transaction${sameMerchant === 1 ? "" : "s"} with the same key).`],
              ["memory", "Remember with a memory annotation", "Written to your household memory with a visible diff; can carry tags and notes."],
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
              <Segmented label="Annotation applies to" value={match} onChange={setMatch} options={[{ value: "merchant", label: "The merchant" }, { value: "transaction", label: "Only this transaction" }]} />
              <Field label="Note (kept in memory)">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why is it this category?" />}</Field>
            </div>
          )}
          {error && <Notice tone="neg">{error}</Notice>}
          {!preview && !error && <Spinner label="Checking what would change" />}
          {preview && a && (
            <div className="grid gap-2" aria-live="polite">
              <p className="text-sm" data-testid="effect">
                {a.count === 0 ? (
                  <>Nothing would change: {scope === "transaction" ? "this transaction" : "no transaction"} would not end up as <b>{catLabel(category)}</b>.</>
                ) : scope === "transaction" ? (
                  <>This one transaction becomes <b>{catLabel(category)}</b>.</>
                ) : (
                  <>
                    <b>{a.count}</b> transaction{a.count === 1 ? "" : "s"} will change to <b>{catLabel(category)}</b>
                    {a.total && <> (total <Money v={a.total} />)</>}
                    {a.already ? <>; {a.already} already {a.already === 1 ? "is" : "are"}.</> : "."}
                  </>
                )}
              </p>
              {a.from_categories && a.from_categories.length > 0 && <p className="text-xs text-muted">Currently: {a.from_categories.map((f) => `${catLabel(f.category)} ×${f.n}`).join(", ")}</p>}
              {preview.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
              {scope === "memory" && <DiffView diff={preview.diff} />}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCategory(d.final.category)}>Cancel</Button>
            <Button variant="primary" busy={apply.isPending} disabled={!preview || !preview.changed} onClick={() => apply.mutate(undefined as never)}>Apply</Button>
          </div>
        </>
      )}
    </section>
  );
}

function Annotate({ d }: { d: TxDetail }) {
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
      api.post<EditResult>("/annotations", body, { dry_run: true }).then((r) => !ctl.signal.aborted && setPreview(r)).catch((e) => !ctl.signal.aborted && setError(e.message));
    }, 250);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [body, tags.length, event]);
  const apply = useWrite(() => api.post<EditResult>("/annotations", body, { dry_run: false }), {
    success: "Saved to memory",
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
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Tag className="size-4" aria-hidden /> Tags, event and note</h3>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Tags">
        {[...known, ...tags.filter((t) => !known.includes(t))].map((t) => (
          <button key={t} type="button" aria-pressed={tags.includes(t)} onClick={() => toggle(t)} className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", tags.includes(t) ? "border-accent bg-accent-soft text-accent" : "border-border-strong text-muted hover:bg-surface-2")}>
            {t}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <Input aria-label="Custom tag" value={custom} onChange={(e) => setCustom(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder="custom tag" className="flex-1" />
        <Button onClick={() => { if (custom) { setTags((c) => (c.includes(custom) ? c : [...c, custom])); setCustom(""); } }}>Add</Button>
      </div>
      <Field label="Event" hint="Trips, works, one-off projects. Events come from your memory.">
        {(id) => (
          <Select id={id} value={event} onChange={(e) => setEvent(e.target.value)}>
            <option value="">No event</option>
            {(filters.data?.events ?? []).map((e) => <option key={e.id} value={e.id}>{e.title ?? e.id}</option>)}
          </Select>
        )}
      </Field>
      <Field label="Note">{(id) => <Textarea id={id} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional. Kept in the annotation." />}</Field>
      {error && <Notice tone="neg">{error}</Notice>}
      {preview && (
        <div className="grid gap-2">
          {preview.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
          <DiffView diff={preview.diff} />
        </div>
      )}
      {!tags.length && !event && note && <Notice>A note alone is not saved: add a tag or an event as well.</Notice>}
      <div className="flex justify-end">
        <Button variant="primary" disabled={!preview || !dirty} busy={apply.isPending} onClick={() => apply.mutate(undefined as never)}>Save to memory</Button>
      </div>
    </section>
  );
}

function Extras({ d }: { d: TxDetail }) {
  const clear = useWrite(() => api.post("/transactions/override/clear", { tx_key: d.transaction.tx_key }), { success: "Override removed" });
  const clearSplit = useWrite(() => api.post("/transactions/split/clear", { tx_key: d.transaction.tx_key }), { success: "Split removed" });
  const unlink = useWrite(() => api.post("/transfers/unlink", { ref: d.transaction.tx_key }), { success: "Transfer unlinked" });
  if (!d.override && !d.split && !d.transfer_link) return null;
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4 text-sm">
      {d.override && (
        <div className="flex items-center justify-between gap-3">
          <span>Overridden to <b>{catLabel(d.override.category)}</b></span>
          <Button size="sm" busy={clear.isPending} onClick={() => clear.mutate(undefined as never)}>Remove override</Button>
        </div>
      )}
      {d.split && (
        <div>
          <div className="mb-1 flex items-center justify-between"><b>Split over categories</b><Button size="sm" busy={clearSplit.isPending} onClick={() => clearSplit.mutate(undefined as never)}>Remove split</Button></div>
          <ul className="text-[13px] text-muted">{d.split.map((s, i) => <li key={i}>{catLabel(s.category)}: {fmtMoney(s.amount)}{s.note ? ` (${s.note})` : ""}</li>)}</ul>
        </div>
      )}
      {d.transfer_link && (
        <div className="flex items-center justify-between gap-3">
          <span>Linked as an internal transfer ({d.transfer_link.method})</span>
          <Button size="sm" busy={unlink.isPending} onClick={() => unlink.mutate(undefined as never)}><Link2Off className="size-3.5" /> Unlink</Button>
        </div>
      )}
    </section>
  );
}

/** E14-3: whose a transaction is, why, and the manual reassignment (recorded and reversible). Shown only when the household has members. */
const SOURCE_TEXT: Record<AttributionWhy["source"], string> = { manual: "you reassigned it", rule: "an attribution rule", account: "the owner of the account", none: "the account has no known owner" };

function Attribution({ txKey }: { txKey: string }) {
  const people = usePeople();
  const q = useGet<AttributionWhy>("/transactions/attribution", { tx_key: txKey }, { staleTime: 0, enabled: people.members.length > 0 });
  const [member, setMember] = useState("");
  const set = useWrite(() => api.post("/transactions/person", { tx_key: txKey, member }), { success: "Reassigned" });
  const clear = useWrite(() => api.post("/transactions/person/clear", { tx_key: txKey }), { success: "Back to the rules and the account owner" });
  if (people.members.length === 0 || !q.data) return null;
  const a = q.data;
  return (
    <section className="grid gap-3 rounded-xl border border-border p-4 text-sm" aria-label="Whose transaction">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>Belongs to <b>{a.person ? people.name(a.person) : "nobody yet"}</b> <span className="text-muted">· {SOURCE_TEXT[a.source]}{a.rule ? ` (${a.rule})` : ""}</span></span>
        {a.manual && <Button size="sm" busy={clear.isPending} onClick={() => clear.mutate(undefined as never)}>Undo my reassignment</Button>}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Reassign to" className="min-w-40 flex-1">
          {(id) => (
            <Select id={id} value={member} onChange={(e) => setMember(e.target.value)}>
              <option value="">Choose…</option>
              {people.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              <option value="joint">Joint</option>
            </Select>
          )}
        </Field>
        <Button size="sm" variant="primary" disabled={!member || member === a.person} busy={set.isPending} onClick={() => set.mutate(undefined as never)}>Reassign</Button>
      </div>
      <Disclosure summary="Why this person?" defaultOpen={false}>
        <ul className="grid gap-1 text-[13px] text-muted">
          {a.manual && <li>Reassigned to {people.name(a.manual.member)} by {a.manual.set_by} on {fmtDate(a.manual.set_at.slice(0, 10), "medium")}{a.manual.note ? `: ${a.manual.note}` : ""}</li>}
          {a.rules.map((r) => <li key={r.id}>{r.matched ? "✓ " : "· "}rule <code className="text-xs">{r.id}</code> ({people.name(r.member)}){r.matched ? "" : `: ${r.why_not}`}</li>)}
          <li>Account {a.account ?? "?"}: owner {a.account_owner ?? "none"}{a.account_owner_member ? ` (${people.name(a.account_owner_member)})` : ""}{a.card_last4 ? ` · card ending ${a.card_last4}` : ""}</li>
          {a.history.map((h) => <li key={h.id}>{fmtDate(h.at.slice(0, 10), "medium")}: {h.action} {h.old_member ? people.name(h.old_member) : "rules"} → {h.new_member ? people.name(h.new_member) : "rules"} by {h.by}</li>)}
        </ul>
      </Disclosure>
    </section>
  );
}
