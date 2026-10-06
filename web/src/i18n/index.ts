// Interface translations (i18next + react-i18next), bundled with the app: no network request, nothing leaves the machine.
// English is imported eagerly (it is the fallback and the source of the key types); the other languages are separate chunks
// loaded the first time they are used. Which languages exist: `./languages.ts`. How to add a string or a language: docs/i18n.md.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { setLocale as setFormatLocale } from "@/lib/format";
import { FALLBACK_LANGUAGE, isLanguageCode, languageFromTag, localeOf, type LanguageCode } from "./languages";

export const STORAGE_KEY = "coach.language";
/** The previous "dates and numbers" selector: "fr-FR" | "en-GB". Still read once, so nobody loses their choice. */
const LEGACY_STORAGE_KEY = "coach.locale";

// every namespace of the fallback language: `src/locales/en/<namespace>.json` (a new file there is picked up as a new namespace)
const enFiles = import.meta.glob<Record<string, unknown>>("../locales/en/*.json", { eager: true, import: "default" });
const otherFiles = import.meta.glob<Record<string, unknown>>(["../locales/*/*.json", "!../locales/en/*.json"], { import: "default" });

function namespaceOf(path: string): string {
  return path.replace(/^.*\/([^/]+)\.json$/, "$1");
}
function languageOfPath(path: string): string {
  return path.replace(/^\.\.\/locales\/([^/]+)\/.*$/, "$1");
}

const enResources = Object.fromEntries(Object.entries(enFiles).map(([p, bundle]) => [namespaceOf(p), bundle]));
export const NAMESPACES = Object.keys(enResources);

/** Make a language's strings available (a no-op when they already are). */
async function loadLanguage(code: LanguageCode): Promise<void> {
  if (code === FALLBACK_LANGUAGE) return;
  for (const [path, load] of Object.entries(otherFiles)) {
    if (languageOfPath(path) !== code) continue;
    const ns = namespaceOf(path);
    if (i18n.hasResourceBundle(code, ns)) continue;
    i18n.addResourceBundle(code, ns, await load(), true, true);
  }
}

// Synchronous English initialisation at import time: components can call t() on the very first render, and tests need no await.
void i18n.use(initReactI18next).init({
  initAsync: false,
  resources: { [FALLBACK_LANGUAGE]: enResources },
  lng: FALLBACK_LANGUAGE,
  fallbackLng: FALLBACK_LANGUAGE,
  supportedLngs: false,
  ns: NAMESPACES,
  defaultNS: "common",
  // React already escapes what it renders; escaping here would show "&amp;" in names
  interpolation: { escapeValue: false },
  returnNull: false,
});

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage blocked
  }
}

/** The language to start in: the saved choice (or the old dates-and-numbers one), else the first supported browser language, else English.
 *  Hand-written on purpose (a dozen lines, three sources): the browser-languagedetector plugin would add a dependency for the same order. */
export function detectLanguage(): LanguageCode {
  const saved = readStorage(STORAGE_KEY);
  if (isLanguageCode(saved)) return saved;
  const legacy = languageFromTag(readStorage(LEGACY_STORAGE_KEY));
  if (legacy) return legacy;
  const wanted = typeof navigator === "undefined" ? [] : navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of wanted) {
    const found = languageFromTag(tag);
    if (found) return found;
  }
  return FALLBACK_LANGUAGE;
}

/** True when the person picked a language in this browser: that choice wins over the one stored for the login. */
export function hasSavedLanguage(): boolean {
  return isLanguageCode(readStorage(STORAGE_KEY));
}

export function currentLanguage(): LanguageCode {
  const l = i18n.resolvedLanguage ?? i18n.language;
  return isLanguageCode(l) ? l : FALLBACK_LANGUAGE;
}

/** Switch the whole interface: the strings, the dates / numbers / money format and <html lang>. `persist: false` for the automatic
 *  start-up choice (so the browser's language keeps being followed until the person picks one). */
export async function setLanguage(code: LanguageCode, opts: { persist?: boolean } = {}): Promise<void> {
  await loadLanguage(code);
  // format and <html lang> first: the languageChanged event re-renders the tree, and it must see the new format
  setFormatLocale(localeOf(code));
  document.documentElement.lang = code;
  await i18n.changeLanguage(code);
  if (opts.persist ?? true) {
    try {
      localStorage.setItem(STORAGE_KEY, code);
    } catch {
      /* storage blocked: the choice lasts until the page is closed */
    }
  }
}

/** Start-up (main.tsx): load and apply the detected language before the first render. */
export function initLanguage(): Promise<void> {
  return setLanguage(detectLanguage(), { persist: false });
}

export default i18n;
