import { KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { useTaxonomy } from "@/api/hooks";
import { catLabel, groupLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

type Opt = { value: string; label: string; group?: string; hint?: string; haystack: string };

/** Lower case, accents dropped: "cinema" finds "Cinéma et sorties", "sante" finds "Santé". */
const fold = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();

/** A searchable category picker (ARIA 1.2 combobox): type part of a category, of its group, of its id or of its description
 *  to narrow the list, arrows to move, Enter to pick, Escape to close. With an empty query it lists every category, grouped. */
export function CategoryPicker({ id, value, onChange, allowEmpty, emptyLabel, includeGroups, className }: {
  id?: string; value: string; onChange: (v: string) => void; allowEmpty?: boolean; emptyLabel?: string; includeGroups?: boolean; className?: string;
}) {
  const { t } = useTranslation();
  const { data } = useTaxonomy();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const options = useMemo<Opt[]>(() => {
    const out: Opt[] = [];
    if (allowEmpty) out.push({ value: "", label: emptyLabel ?? t("picker.any"), haystack: "" });
    for (const g of data?.groups ?? []) {
      const gl = groupLabel(g.id);
      if (includeGroups) out.push({ value: g.id, label: t("picker.allIn", { group: gl.toLowerCase() }), group: g.id, haystack: fold(`${gl} ${g.id}`) });
      for (const c of g.categories) out.push({ value: c.id, label: catLabel(c.id), group: g.id, hint: c.description, haystack: fold(`${catLabel(c.id)} ${gl} ${c.id} ${c.description ?? ""}`) });
    }
    return out;
  }, [data, allowEmpty, emptyLabel, includeGroups, t]);

  const shown = useMemo(() => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return options;
    return options.filter((o) => o.value && words.every((w) => o.haystack.includes(w)));
  }, [options, query]);

  const selected = options.find((o) => o.value === value);
  const selectedLabel = selected?.label ?? (value ? catLabel(value) : "");

  useEffect(() => {
    if (open) listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  function show() {
    if (open) return;
    setQuery("");
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  }
  function close() {
    setOpen(false);
    setQuery("");
  }
  function pick(o: Opt) {
    onChange(o.value);
    close();
  }
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) return show();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (shown.length ? (a + step + shown.length) % shown.length : 0));
    } else if (e.key === "Enter") {
      if (!open) return;
      e.preventDefault();
      if (shown[active]) pick(shown[active]);
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        close();
      }
    } else if (e.key === "Home" && open && !query) {
      setActive(0);
    } else if (e.key === "End" && open && !query) {
      setActive(shown.length - 1);
    }
  }

  return (
    <div className={cn("relative", className)}>
      <input
        ref={inputRef}
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && shown[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        className="w-full min-h-10 rounded-lg border border-border-strong bg-surface pl-3 pr-8 text-sm text-text placeholder:text-faint focus-visible:border-accent"
        placeholder={open ? t("picker.search") : selectedLabel || t("picker.search")}
        value={open ? query : selectedLabel}
        onClick={show}
        onChange={(e) => {
          if (!open) show();
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        onBlur={close}
      />
      <ChevronDown aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" />
      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={t("picker.listLabel")}
          className="absolute left-0 right-0 z-30 mt-1 max-h-72 overflow-auto rounded-lg border border-border bg-surface py-1 shadow-[var(--shadow)]"
        >
          {shown.length === 0 && <li className="px-3 py-2 text-sm text-muted">{t("picker.noMatch")}</li>}
          {shown.map((o, i) => (
            <PickerRow key={o.value || "__empty"} o={o} i={i} listId={listId} header={o.group !== undefined && o.group !== shown[i - 1]?.group}
              active={i === active} selected={o.value === value} onHover={() => setActive(i)} onPick={() => pick(o)} />
          ))}
        </ul>
      )}
    </div>
  );
}

function PickerRow({ o, i, listId, header, active, selected, onHover, onPick }: {
  o: Opt; i: number; listId: string; header: boolean; active: boolean; selected: boolean; onHover: () => void; onPick: () => void;
}) {
  return (
    <>
      {header && <li role="presentation" className="px-3 pb-0.5 pt-2 text-xs font-medium text-faint">{groupLabel(o.group!)}</li>}
      <li
        id={`${listId}-${i}`}
        data-index={i}
        role="option"
        aria-selected={selected}
        title={o.hint}
        // mousedown, not click: the input keeps the focus, so its blur does not close the list before the pick lands
        onMouseDown={(e) => {
          e.preventDefault();
          onPick();
        }}
        onMouseMove={onHover}
        className={cn("cursor-pointer px-3 py-1.5 text-sm", o.group !== undefined && "pl-5", active && "bg-surface-2", selected && "font-medium text-accent")}
      >
        {o.label}
      </li>
    </>
  );
}
