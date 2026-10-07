// The ONE list of interface languages. Adding a language = a folder `src/locales/<code>/` with the same JSON files as
// `en/` + one entry here (see docs/i18n.md). Everything else (the language select, the dates and numbers format, the
// completeness test) is derived from this array.

export const LANGUAGES = [
  // `locale` is the Intl locale the language implies for dates, numbers and money
  { code: "en", name: "English", locale: "en-GB" },
  { code: "fr", name: "Français", locale: "fr-FR" },
  { code: "it", name: "Italiano", locale: "it-IT" },
] as const;

export type Language = (typeof LANGUAGES)[number];
export type LanguageCode = Language["code"];
export type Locale = Language["locale"];

/** The language whose strings fill any gap, and the one bundled eagerly. */
export const FALLBACK_LANGUAGE: LanguageCode = "en";

export const LANGUAGE_CODES: LanguageCode[] = LANGUAGES.map((l) => l.code);
export const LOCALES: Locale[] = LANGUAGES.map((l) => l.locale);

export function isLanguageCode(v: unknown): v is LanguageCode {
  return typeof v === "string" && LANGUAGES.some((l) => l.code === v);
}

export function localeOf(code: LanguageCode): Locale {
  return LANGUAGES.find((l) => l.code === code)!.locale;
}

/** "fr", "fr-CA", "fr_FR", "it-IT" -> "fr" / "it" when that language is supported, else null. */
export function languageFromTag(tag: string | null | undefined): LanguageCode | null {
  if (!tag) return null;
  const base = tag.toLowerCase().split(/[-_]/)[0];
  return isLanguageCode(base) ? base : null;
}
