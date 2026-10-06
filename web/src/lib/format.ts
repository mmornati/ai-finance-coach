// Presentation only: the API sends money as decimal strings ("-12.34") computed by the Python analytics; these helpers
// parse them for display and charts and format them for the chosen locale. Nothing here computes a business figure.

export type Locale = "fr-FR" | "en-GB";
export const LOCALES: Locale[] = ["fr-FR", "en-GB"];

let current: Locale = "fr-FR";
export function setLocale(l: Locale) {
  current = l;
}
export function getLocale(): Locale {
  return current;
}
export function initialLocale(): Locale {
  try {
    const v = localStorage.getItem("coach.locale");
    if (v === "fr-FR" || v === "en-GB") return v;
  } catch {
    /* storage blocked */
  }
  return "fr-FR";
}

/** "-1234.50" -> -1234.5; null/undefined/garbage -> null. */
export function parseMoney(s: string | number | null | undefined): number | null {
  if (s === null || s === undefined || s === "") return null;
  const n = typeof s === "number" ? s : Number(s);
  return Number.isFinite(n) ? n : null;
}

const cache = new Map<string, Intl.NumberFormat>();
function nf(locale: Locale, opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = locale + JSON.stringify(opts);
  let f = cache.get(key);
  if (!f) {
    f = new Intl.NumberFormat(locale, opts);
    cache.set(key, f);
  }
  return f;
}

export interface MoneyOpts {
  /** always show + for positive amounts */
  signed?: boolean;
  /** drop the cents (large figures) */
  round?: boolean;
  /** 12 300 -> 12,3 k EUR */
  compact?: boolean;
  locale?: Locale;
}

export function fmtMoney(v: string | number | null | undefined, o: MoneyOpts = {}): string {
  const n = parseMoney(v);
  if (n === null) return "–";
  const locale = o.locale ?? current;
  const opts: Intl.NumberFormatOptions = {
    style: "currency",
    currency: "EUR",
    signDisplay: o.signed ? "exceptZero" : "auto",
    ...(o.round ? { maximumFractionDigits: 0, minimumFractionDigits: 0 } : {}),
    ...(o.compact ? { notation: "compact", maximumFractionDigits: 1 } : {}),
  };
  return nf(locale, opts).format(n);
}

export function fmtNumber(n: number | null | undefined, digits = 0, locale: Locale = current): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return nf(locale, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n);
}

/** 0.1234 -> "12,3 %" */
export function fmtPct(ratio: number | null | undefined, digits = 0, o: { signed?: boolean; locale?: Locale } = {}): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "–";
  return nf(o.locale ?? current, {
    style: "percent",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
    signDisplay: o.signed ? "exceptZero" : "auto",
  }).format(ratio);
}

function toDate(iso: string): Date {
  // "2026-10-04" is a calendar date, not an instant: build it in local time so the day never shifts
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (m && iso.length <= 10) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return new Date(iso);
}

const dfCache = new Map<string, Intl.DateTimeFormat>();
function df(locale: Locale, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = locale + JSON.stringify(opts);
  let f = dfCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(locale, opts);
    dfCache.set(key, f);
  }
  return f;
}

export type DateStyle = "short" | "medium" | "long" | "weekday" | "dayMonth";

export function fmtDate(iso: string | null | undefined, style: DateStyle = "medium", locale: Locale = current): string {
  if (!iso) return "–";
  const d = toDate(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const opts: Record<DateStyle, Intl.DateTimeFormatOptions> = {
    short: { day: "2-digit", month: "2-digit" },
    dayMonth: { day: "numeric", month: "short" },
    medium: { day: "numeric", month: "short", year: "numeric" },
    long: { day: "numeric", month: "long", year: "numeric" },
    weekday: { weekday: "short", day: "numeric", month: "short" },
  };
  return df(locale, opts[style]).format(d);
}

/** "2026-09" -> "sept. 2026" */
export function fmtMonth(key: string | null | undefined, style: "short" | "long" | "narrow" = "short", locale: Locale = current): string {
  if (!key) return "–";
  const [y, m] = key.split("-").map(Number);
  if (!y || !m) return key;
  const d = new Date(y, m - 1, 1);
  if (style === "narrow") return df(locale, { month: "narrow" }).format(d);
  if (style === "long") return df(locale, { month: "long", year: "numeric" }).format(d);
  return df(locale, { month: "short", year: "2-digit" }).format(d);
}

export function fmtDateTime(iso: string | null | undefined, locale: Locale = current): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return df(locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(d);
}

/** "in 3 days" / "yesterday" from a day count (negative = past). */
export function fmtRelativeDays(days: number, locale: Locale = current): string {
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(days, "day");
}

export function addMonthsKey(key: string, n: number): string {
  const [y, m] = key.split("-").map(Number);
  const i = y * 12 + (m - 1) + n;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
}

export function todayKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** Sign class for an amount string. */
export function tone(v: string | number | null | undefined): "pos" | "neg" | "zero" {
  const n = parseMoney(v);
  if (n === null || n === 0) return "zero";
  return n > 0 ? "pos" : "neg";
}

const CAT_NAMES: Record<string, string> = {};
/** "food.groceries" -> "Groceries" (the id is the source of truth; the label is for reading). */
export function catLabel(id: string | null | undefined): string {
  if (!id) return "–";
  if (CAT_NAMES[id]) return CAT_NAMES[id];
  const [group, ...rest] = id.split(".");
  const leaf = rest.length ? rest.join(" ") : group;
  const s = leaf.replace(/_/g, " ");
  const label = s.charAt(0).toUpperCase() + s.slice(1);
  // "internal" or "salary" alone say nothing: income and transfers keep their group in front
  return rest.length && (group === "transfer" || group === "income") ? `${group.charAt(0).toUpperCase() + group.slice(1)}: ${s}` : label;
}
export function groupLabel(id: string): string {
  const s = id.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function initialsOf(s: string): string {
  return s
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}
