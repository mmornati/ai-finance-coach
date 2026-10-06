import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import i18n, { currentLanguage, setLanguage as applyLanguage } from "@/i18n";
import { localeOf, type LanguageCode, type Locale } from "@/i18n/languages";

/* ------------------------------------------------------------------ theme */
export type ThemePref = "system" | "light" | "dark";

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem("coach.theme");
    if (v === "light" || v === "dark") return v;
  } catch {
    /* ignore */
  }
  return "system";
}

interface PrefsCtx {
  theme: ThemePref;
  setTheme: (t: ThemePref) => void;
  /** the interface language (strings AND the dates / numbers format) */
  language: LanguageCode;
  setLanguage: (l: LanguageCode) => void;
  /** the Intl locale the language implies: changes with the language, so it is also what remounts the page tree */
  locale: Locale;
}
const Prefs = createContext<PrefsCtx | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemePref>(readPref);
  // the language itself lives in i18next (initialised before the first render, see main.tsx); the provider mirrors it
  const [language, setLanguageState] = useState<LanguageCode>(currentLanguage);
  useEffect(() => {
    const sync = () => setLanguageState(currentLanguage());
    i18n.on("languageChanged", sync);
    sync();
    return () => i18n.off("languageChanged", sync);
  }, []);
  const setTheme = useCallback((t: ThemePref) => {
    setThemeState(t);
    try {
      if (t === "system") localStorage.removeItem("coach.theme");
      else localStorage.setItem("coach.theme", t);
    } catch {
      /* ignore */
    }
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  }, []);
  const setLanguage = useCallback((l: LanguageCode) => void applyLanguage(l).catch(() => undefined), []);
  const locale = localeOf(language);
  const v = useMemo(() => ({ theme, setTheme, language, setLanguage, locale }), [theme, setTheme, language, setLanguage, locale]);
  // the pages are keyed by the locale (App.tsx): a language change remounts them so every formatted value is recomputed
  return <Prefs.Provider value={v}>{children}</Prefs.Provider>;
}

export function usePrefs(): PrefsCtx {
  const c = useContext(Prefs);
  if (!c) throw new Error("PrefsProvider missing");
  return c;
}

/* ------------------------------------------------------------------ scope (household / person) */
export interface Scope {
  /** E14-4: one household member id (or "joint"): every figure then covers only what is attributed to that person */
  member: string;
  /** an account owner value (kept for old saved views; the switch offers `member`) */
  owner: string;
  purpose: string;
}
interface ScopeCtx {
  scope: Scope;
  setScope: (s: Scope) => void;
  /** query params for scoped analytics */
  params: Record<string, string>;
}
const ScopeContext = createContext<ScopeCtx | null>(null);

function readScope(): Scope {
  try {
    const raw = localStorage.getItem("coach.scope");
    if (raw) {
      const j = JSON.parse(raw);
      return { member: String(j.member ?? ""), owner: String(j.owner ?? ""), purpose: String(j.purpose ?? "") };
    }
  } catch {
    /* ignore */
  }
  return { member: "", owner: "", purpose: "" };
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [scope, setScopeState] = useState<Scope>(readScope);
  const setScope = useCallback((s: Scope) => {
    setScopeState(s);
    try {
      localStorage.setItem("coach.scope", JSON.stringify(s));
    } catch {
      /* ignore */
    }
  }, []);
  const params = useMemo(() => {
    const p: Record<string, string> = {};
    if (scope.member) p.member = scope.member;
    if (scope.owner) p.owner = scope.owner;
    if (scope.purpose) p.purpose = scope.purpose;
    return p;
  }, [scope]);
  return <ScopeContext.Provider value={{ scope, setScope, params }}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeCtx {
  const c = useContext(ScopeContext);
  if (!c) throw new Error("ScopeProvider missing");
  return c;
}

/* ------------------------------------------------------------------ toasts */
export interface ToastMsg {
  id: number;
  tone: "info" | "success" | "error";
  text: string;
}
interface ToastCtx {
  toast: (text: string, tone?: ToastMsg["tone"]) => void;
  toasts: ToastMsg[];
  dismiss: (id: number) => void;
}
const ToastContext = createContext<ToastCtx | null>(null);
let toastId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMsg[]>([]);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const toast = useCallback(
    (text: string, tone: ToastMsg["tone"] = "info") => {
      const id = ++toastId;
      setToasts((t) => [...t.slice(-3), { id, tone, text }]);
      setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4500);
    },
    [dismiss],
  );
  return <ToastContext.Provider value={{ toast, toasts, dismiss }}>{children}</ToastContext.Provider>;
}

export function useToast(): ToastCtx {
  const c = useContext(ToastContext);
  if (!c) throw new Error("ToastProvider missing");
  return c;
}

/* ------------------------------------------------------------------ the login of this session (E14-8) */
import type { UserInfo } from "@/api/types";

const UserContext = createContext<UserInfo | null>(null);

export function UserProvider({ user, children }: { user: UserInfo | null; children: ReactNode }) {
  return <UserContext.Provider value={user}>{children}</UserContext.Provider>;
}

/** Who is logged in: null for the owner login of `coach ui` before the session is known; role "child" gets the own-data view only. */
export function useUser(): UserInfo | null {
  return useContext(UserContext);
}
