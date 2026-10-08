import { ButtonHTMLAttributes, forwardRef, HTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes, useEffect, useId, useRef } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtMoney, tone as toneOf, type MoneyOpts } from "@/lib/format";
import { errorText } from "@/i18n/server";

/* ------------------------------------------------------------------ surfaces */
export function Card({ title, subtitle, action, children, className, pad = true, as: Tag = "section" }: {
  title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; pad?: boolean; as?: "section" | "div" | "article";
}) {
  const id = useId();
  return (
    <Tag aria-labelledby={title ? id : undefined} className={cn("rounded-xl border border-border bg-surface shadow-[var(--shadow)]", className)}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 px-4 pt-3.5 sm:px-5">
          <div className="min-w-0">
            {title && (
              <h2 id={id} className="text-[13px] font-semibold tracking-normal text-muted">
                {title}
              </h2>
            )}
            {subtitle && <p className="mt-0.5 text-xs text-faint">{subtitle}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={cn(pad && "px-4 pb-4 sm:px-5 sm:pb-5", title || action ? "pt-2.5" : pad && "pt-4 sm:pt-5")}>{children}</div>
    </Tag>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold leading-tight sm:text-2xl">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ money */
export function Money({ v, signed, colored, className, ...o }: { v: string | number | null | undefined; signed?: boolean; colored?: boolean; className?: string } & MoneyOpts) {
  const t = toneOf(v);
  return (
    <span className={cn("num whitespace-nowrap", colored && t === "pos" && "text-pos", colored && t === "neg" && "text-neg", className)}>
      {fmtMoney(v, { signed, ...o })}
    </span>
  );
}

export function Stat({ label, value, hint, tone, big }: { label: ReactNode; value: ReactNode; hint?: ReactNode; tone?: "pos" | "neg" | "warn"; big?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className={cn("num mt-0.5 truncate font-semibold leading-tight", big ? "text-[28px] sm:text-[32px]" : "text-xl", tone === "pos" && "text-pos", tone === "neg" && "text-neg", tone === "warn" && "text-warn")}>
        {value}
      </div>
      {hint && <div className="mt-0.5 text-xs text-faint">{hint}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ badges / notices */
type Tone = "neutral" | "pos" | "neg" | "warn" | "info";
const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted",
  pos: "bg-pos-soft text-pos",
  neg: "bg-neg-soft text-neg",
  warn: "bg-warn-soft text-warn",
  info: "bg-accent-soft text-accent",
};
export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium leading-4", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Dot({ level }: { level: "green" | "amber" | "red" | string }) {
  const c = level === "green" ? "bg-pos" : level === "amber" ? "bg-warn" : "bg-neg";
  return <span aria-hidden className={cn("inline-block size-2 rounded-full", c)} />;
}

export function Notice({ tone = "info", children, className, title }: { tone?: "info" | "warn" | "neg" | "pos"; children: ReactNode; className?: string; title?: string }) {
  const Icon = tone === "warn" || tone === "neg" ? AlertTriangle : tone === "pos" ? CheckCircle2 : Info;
  const colors = { info: "border-border bg-surface-2 text-muted", warn: "border-warn/30 bg-warn-soft text-warn", neg: "border-neg/30 bg-neg-soft text-neg", pos: "border-pos/30 bg-pos-soft text-pos" }[tone];
  return (
    <div role={tone === "neg" ? "alert" : "note"} className={cn("flex gap-2.5 rounded-lg border px-3 py-2 text-[13px] leading-snug", colors, className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0">
        {title && <div className="font-semibold">{title}</div>}
        {children}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ buttons */
type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md"; busy?: boolean };
export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button({ variant = "secondary", size = "md", busy, className, children, disabled, ...p }, ref) {
  const v = {
    primary: "bg-accent text-accent-fg hover:bg-accent-hover border-transparent",
    secondary: "bg-surface text-text border-border-strong hover:bg-surface-2",
    ghost: "bg-transparent text-muted border-transparent hover:bg-surface-2 hover:text-text",
    danger: "bg-neg text-white border-transparent hover:opacity-90",
  }[variant];
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled || busy}
      className={cn("inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "min-h-8 px-2.5 text-[13px]" : "min-h-10 px-3.5 text-sm", v, className)}
      {...p}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

export function IconButton({ label, children, className, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button type="button" aria-label={label} title={label} className={cn("inline-flex size-10 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-text", className)} {...p}>
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ forms */
export function Field({ label, hint, error, children, className }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={cn("min-w-0", className)}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
        {label}
      </label>
      {children(id)}
      {hint && !error && <p className="mt-1 text-xs text-faint">{hint}</p>}
      {error && <p className="mt-1 text-xs text-neg">{error}</p>}
    </div>
  );
}

const control = "w-full min-h-10 rounded-lg border border-border-strong bg-surface px-3 text-sm text-text placeholder:text-faint focus-visible:border-accent disabled:opacity-60";
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <input ref={ref} className={cn(control, className)} {...p} />;
});
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...p }, ref) {
  return (
    <select ref={ref} className={cn(control, "pr-8", className)} {...p}>
      {children}
    </select>
  );
});
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...p }, ref) {
  return <textarea ref={ref} className={cn(control, "min-h-20 py-2", className)} {...p} />;
});

export function Segmented<V extends string>({ value, onChange, options, label }: { value: V; onChange: (v: V) => void; options: { value: V; label: ReactNode }[]; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-border-strong bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn("min-h-8 rounded-md px-3 text-[13px] font-medium transition-colors", value === o.value ? "bg-surface text-text shadow-[var(--shadow)]" : "text-muted hover:text-text")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Section switcher: a nav of buttons (aria-current marks the open section), scrollable on a phone. */
export function Tabs<V extends string>({ value, onChange, tabs, label }: { value: V; onChange: (v: V) => void; tabs: { value: V; label: ReactNode; badge?: ReactNode }[]; label: string }) {
  return (
    <nav aria-label={label} className="mb-4 flex gap-1 overflow-x-auto border-b border-border">
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          aria-current={value === t.value ? "page" : undefined}
          onClick={() => onChange(t.value)}
          className={cn("-mb-px flex min-h-10 shrink-0 items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors", value === t.value ? "border-accent text-text" : "border-transparent text-muted hover:text-text")}
        >
          {t.label}
          {t.badge}
        </button>
      ))}
    </nav>
  );
}

/* ------------------------------------------------------------------ progress */
export function ProgressBar({ value, max = 1, tone = "info", marker, label }: { value: number; max?: number; tone?: "info" | "pos" | "warn" | "neg"; marker?: number; label: string }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  const color = { info: "bg-accent", pos: "bg-pos", warn: "bg-warn", neg: "bg-neg" }[tone];
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} className="relative h-2 w-full overflow-hidden rounded-full bg-surface-2">
      <div className={cn("h-full rounded-full transition-[width] duration-300", color)} style={{ width: `${pct}%` }} />
      {marker !== undefined && <div aria-hidden className="absolute inset-y-0 w-0.5 bg-text/60" style={{ left: `${Math.min(100, Math.max(0, marker))}%` }} />}
    </div>
  );
}

/* ------------------------------------------------------------------ states */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton", className)} />;
}

export function EmptyState({ title, children, action, icon }: { title: string; children?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      {icon && <div className="mb-1 text-faint">{icon}</div>}
      <p className="text-sm font-semibold">{title}</p>
      {children && <p className="max-w-md text-[13px] text-muted">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useTranslation();
  const msg = errorText(error, t("ui.error.generic"));
  const offline = error instanceof TypeError;
  return (
    <div role="alert" className="flex flex-col items-start gap-2 rounded-lg border border-neg/30 bg-neg-soft px-4 py-3 text-sm text-neg">
      <div className="font-semibold">{offline ? t("ui.error.offlineTitle") : t("ui.error.loadTitle")}</div>
      <div className="text-[13px] opacity-90">{offline ? t("ui.error.offlineBody") : msg}</div>
      {retry && (
        <Button size="sm" onClick={retry}>
          {t("ui.error.retry")}
        </Button>
      )}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  const { t } = useTranslation();
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label ?? t("ui.loading")}
    </span>
  );
}

/* ------------------------------------------------------------------ dialog (native <dialog>: focus trap, Esc, inert page) */
export function Dialog({ open, onClose, title, children, footer, size = "md", side = false, description }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; size?: "sm" | "md" | "lg"; side?: boolean; description?: ReactNode;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  const w = { sm: "sm:max-w-md", md: "sm:max-w-xl", lg: "sm:max-w-3xl" }[size];
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className={cn(
        "m-0 w-full max-w-none overflow-hidden border border-border bg-surface p-0 text-text shadow-2xl",
        "mt-auto max-h-[92dvh] rounded-t-2xl open:flex open:flex-col",
        side ? "sm:ml-auto sm:mt-0 sm:h-dvh sm:max-h-dvh sm:w-[min(540px,100vw)] sm:rounded-none sm:rounded-l-2xl" : cn("sm:m-auto sm:max-h-[88dvh] sm:rounded-2xl", w),
      )}
    >
      <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-3.5">
        <div className="min-w-0">
          <h2 id={titleId} className="text-base font-semibold">
            {title}
          </h2>
          {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
        </div>
        <IconButton label={t("ui.close")} onClick={onClose} className="-mr-2 -mt-1 size-9">
          <X className="size-5" />
        </IconButton>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{open && children}</div>
      {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border bg-surface-2/50 px-5 py-3 pad-safe-bottom">{footer}</div>}
    </dialog>
  );
}

/* ------------------------------------------------------------------ diff */
export function DiffView({ diff, empty }: { diff: string; empty?: string }) {
  const { t } = useTranslation();
  if (!diff.trim()) return <p className="text-[13px] text-muted">{empty ?? t("ui.diff.empty")}</p>;
  return (
    <pre aria-label={t("ui.diff.label")} className="max-h-72 overflow-auto rounded-lg border border-border bg-surface-2 p-3 font-mono text-xs leading-5">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={cn("whitespace-pre-wrap break-all", l.startsWith("+") && !l.startsWith("+++") && "bg-pos-soft text-pos", l.startsWith("-") && !l.startsWith("---") && "bg-neg-soft text-neg", (l.startsWith("@@") || l.startsWith("+++") || l.startsWith("---")) && "text-faint")}>
          {l || " "}
        </div>
      ))}
    </pre>
  );
}

/* ------------------------------------------------------------------ misc */
export function Disclosure({ summary, children, defaultOpen }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group rounded-lg border border-border">
      <summary className="flex min-h-10 cursor-pointer list-none items-center gap-2 px-3 text-[13px] font-medium text-muted marker:hidden hover:text-text">
        <span aria-hidden className="text-faint transition-transform group-open:rotate-90">›</span>
        {summary}
      </summary>
      <div className="border-t border-border px-3 py-3">{children}</div>
    </details>
  );
}

export function Kbd({ children }: HTMLAttributes<HTMLElement>) {
  return <kbd className="rounded border border-border-strong bg-surface-2 px-1.5 py-0.5 font-mono text-[11px]">{children}</kbd>;
}

export function Chips({ items, tone = "neutral" }: { items: string[]; tone?: Tone }) {
  return (
    <>
      {items.map((t) => (
        <Badge key={t} tone={tone}>
          {t}
        </Badge>
      ))}
    </>
  );
}

/* ------------------------------------------------------------------ async wrapper for a query */
import type { UseQueryResult } from "@tanstack/react-query";
export function Async<T>({ q, children, skeleton, empty }: { q: UseQueryResult<T, Error>; children: (d: T) => ReactNode; skeleton?: ReactNode; empty?: (d: T) => boolean | ReactNode }) {
  if (q.isPending) return <>{skeleton ?? <div className="grid gap-2" aria-busy="true"><Skeleton className="h-5 w-1/2" /><Skeleton className="h-24 w-full" /></div>}</>;
  if (q.isError) return <ErrorState error={q.error} retry={() => void q.refetch()} />;
  return <>{children(q.data)}</>;
}
