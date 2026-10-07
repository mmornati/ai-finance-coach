import { ReactNode, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { NavLink, Outlet, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Activity, Baby, Building2, LogOut, Brain, Scale, Users, CalendarDays, ClipboardCheck, Gauge, Target, ListChecks, Landmark, LayoutDashboard, Lightbulb, ListOrdered, MessageCircle, Monitor, Moon, MoreHorizontal, Bell, PiggyBank, Plug, Receipt, RefreshCw, Repeat, Sun, SlidersHorizontal, Wallet } from "lucide-react";
import { Badge, Button, Dialog, Dot, Field, IconButton, Select, Skeleton } from "./ui";
import { useConnections, useFilters, useGet, useHealth, useAlertSummary, useInsights, useProposals, useQuestions, useWrite } from "@/api/hooks";
import { usePrefs, useScope, useToast } from "@/lib/app";
import { api } from "@/lib/api";
import { LANGUAGES, isLanguageCode } from "@/i18n/languages";
import { cn } from "@/lib/utils";
import { purposeLabel } from "@/i18n/server";
import type { JobState } from "@/api/types";

interface NavItem { to: string; label: ParseKeys; icon: typeof Gauge; end?: boolean; key?: "insights" | "alerts" | "memory" | "connections" | "review" }
const ALERTS: NavItem = { to: "/alerts", label: "nav.alerts", icon: Bell, key: "alerts" };
const MAIN: NavItem[] = [
  { to: "/", label: "nav.dashboard", icon: LayoutDashboard, end: true },
  { to: "/transactions", label: "nav.transactions", icon: Receipt },
  { to: "/insights", label: "nav.insights", icon: Lightbulb, key: "insights" },
  ALERTS,
  { to: "/coach", label: "nav.coach", icon: MessageCircle },
];
const PLAN: NavItem[] = [
  { to: "/categories", label: "nav.categories", icon: ListOrdered },
  { to: "/review", label: "nav.review", icon: ListChecks, key: "review" },
  { to: "/budgets", label: "nav.budgets", icon: PiggyBank },
  { to: "/subscriptions", label: "nav.subscriptions", icon: Repeat },
  { to: "/wealth", label: "nav.wealth", icon: Landmark },
  { to: "/rental", label: "nav.rental", icon: Building2 },
  { to: "/calendar", label: "nav.calendar", icon: CalendarDays },
];
const PEOPLE: NavItem[] = [
  { to: "/household", label: "nav.household", icon: Users },
  { to: "/kids", label: "nav.kids", icon: Baby },
  { to: "/who-pays", label: "nav.whoPays", icon: Scale },
];
const SYSTEM: NavItem[] = [
  { to: "/setup", label: "nav.setup", icon: ClipboardCheck },
  { to: "/memory", label: "nav.memory", icon: Brain, key: "memory" },
  { to: "/connections", label: "nav.connections", icon: Plug, key: "connections" },
  { to: "/gold", label: "nav.gold", icon: Target },
  { to: "/usage", label: "nav.usage", icon: Activity },
];

function useNavBadges() {
  const ins = useInsights();
  const al = useAlertSummary();
  const q = useQuestions("open");
  const p = useProposals();
  const h = useHealth();
  const r = useGet<{ total: number }>("/review", { limit: 1 }, { staleTime: 60_000 });
  return {
    review: r.data?.total ?? 0,
    insights: ins.data?.cards.filter((c) => c.severity !== "low").length ?? 0,
    alerts: al.data?.open ?? 0,
    alertsHigh: al.data?.high ?? 0,
    memory: (q.data?.counts.open ?? 0) + (p.data?.proposals.length ?? 0),
    health: h.data?.level,
  };
}

function NavList({ items, onNavigate }: { items: NavItem[]; onNavigate?: () => void }) {
  const { t } = useTranslation();
  const b = useNavBadges();
  return (
    <ul className="flex flex-col gap-0.5">
      {items.map((it) => {
        const count = it.key === "insights" ? b.insights : it.key === "alerts" ? b.alerts : it.key === "memory" ? b.memory : it.key === "review" ? b.review : 0;
        return (
          <li key={it.to}>
            <NavLink
              to={it.to}
              end={it.end}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn("flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors", isActive ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-text")
              }
            >
              <it.icon className="size-[18px] shrink-0" aria-hidden />
              <span className="flex-1 truncate">{t(it.label)}</span>
              {count > 0 && <Badge tone={it.key === "alerts" ? (b.alertsHigh > 0 ? "neg" : "warn") : it.key === "insights" ? "warn" : "info"}>{count}</Badge>}
              {it.key === "connections" && b.health && b.health !== "green" && <Dot level={b.health} />}
            </NavLink>
          </li>
        );
      })}
    </ul>
  );
}

function Sidebar() {
  const { t } = useTranslation();
  return (
    <aside className="no-print sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-5 overflow-y-auto border-r border-border bg-surface px-3 py-4 lg:flex" aria-label={t("nav.main")}>
      <div className="flex items-center gap-2.5 px-2 pb-1">
        <img src="/favicon.svg" alt="" className="size-7 rounded-md" />
        <span className="text-[15px] font-semibold tracking-tight">{t("app.name")}</span>
      </div>
      <nav className="flex flex-col gap-4">
        <NavList items={MAIN} />
        <div>
          <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-faint">{t("nav.section.plan")}</div>
          <NavList items={PLAN} />
        </div>
        <div>
          <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-faint">{t("nav.section.people")}</div>
          <NavList items={PEOPLE} />
        </div>
        <div>
          <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-faint">{t("nav.section.system")}</div>
          <NavList items={SYSTEM} />
        </div>
      </nav>
      <p className="mt-auto px-3 text-[11px] leading-snug text-faint">{t("nav.localOnly")}</p>
    </aside>
  );
}

function BottomNav({ onMore }: { onMore: () => void }) {
  const { t } = useTranslation();
  const b = useNavBadges();
  const tabs = MAIN.filter((i) => i !== ALERTS).slice(0, 4);
  return (
    <nav aria-label={t("nav.main")} className="no-print pad-safe-bottom fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur lg:hidden">
      <ul className="mx-auto grid max-w-xl grid-cols-5">
        {tabs.map((tab) => (
          <li key={tab.to}>
            <NavLink to={tab.to} end={tab.end} className={({ isActive }) => cn("relative flex min-h-14 flex-col items-center justify-center gap-0.5 text-[11px] font-medium", isActive ? "text-accent" : "text-muted")}>
              <tab.icon className="size-5" aria-hidden />
              {tab.label === "nav.coach" ? t("nav.coachShort") : t(tab.label)}
              {tab.key === "insights" && b.insights > 0 && <span className="absolute left-[55%] top-1.5 min-w-4 rounded-full bg-warn px-1 text-center text-[10px] font-semibold leading-4 text-white">{b.insights}</span>}
            </NavLink>
          </li>
        ))}
        <li>
          <button type="button" onClick={onMore} className="flex min-h-14 w-full flex-col items-center justify-center gap-0.5 text-[11px] font-medium text-muted">
            <MoreHorizontal className="size-5" aria-hidden />
            {t("nav.more")}
          </button>
        </li>
      </ul>
    </nav>
  );
}

export function ScopeSwitch() {
  const { t } = useTranslation();
  const { scope, setScope } = useScope();
  const { data } = useFilters();
  const [open, setOpen] = useState(false);
  const member = (id: string) => (id === "joint" ? t("scope.joint") : data?.members.find((m) => m.id === id)?.name.split(" ")[0] ?? id);
  const narrowed = !!(scope.member || scope.owner || scope.purpose);
  const label = narrowed ? [scope.member && member(scope.member), scope.owner && !scope.member && t("scope.accountsOf", { name: member(scope.owner) }), scope.purpose && purposeLabel(scope.purpose)].filter(Boolean).join(" · ") : t("scope.household");
  const people = data?.members ?? [];
  return (
    <>
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)} aria-haspopup="dialog" className={cn(narrowed && "border-accent text-accent")}>
        <SlidersHorizontal className="size-3.5" aria-hidden />
        {label}
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} title={t("scope.title")} size="sm" description={t("scope.description")}>
        <div className="grid gap-4">
          <Field label={t("scope.person")} hint={t("scope.personHint")}>
            {(id) => (
              <Select id={id} value={scope.member} onChange={(e) => setScope({ ...scope, member: e.target.value, owner: "" })}>
                <option value="">{t("scope.everyone")}</option>
                {people.map((m) => (
                  <option key={m.id} value={m.id}>
                    {t("scope.personRole", { name: m.name, role: m.role === "child" ? t("scope.role.child") : t("scope.role.adult") })}
                  </option>
                ))}
                {people.length > 0 && <option value="joint">{t("scope.jointOption")}</option>}
              </Select>
            )}
          </Field>
          <Field label={t("scope.purpose")}>
            {(id) => (
              <Select id={id} value={scope.purpose} onChange={(e) => setScope({ ...scope, purpose: e.target.value })}>
                <option value="">{t("scope.allPurposes")}</option>
                {(data?.purposes ?? []).map((p) => (
                  <option key={p} value={p}>
                    {purposeLabel(p)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="flex justify-between">
            <Button variant="ghost" onClick={() => setScope({ member: "", owner: "", purpose: "" })}>
              {t("scope.reset")}
            </Button>
            <Button variant="primary" onClick={() => setOpen(false)}>
              {t("scope.done")}
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}

/** Sync now: respects the daily limit per account (skipped accounts are reported); polls the job while it runs. */
export function SyncButton({ compact }: { compact?: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { toast } = useToast();
  const conn = useConnections();
  const [job, setJob] = useState<JobState | null>(null);
  const start = useWrite(() => api.post<JobState>("/sync", {}), { invalidate: false, onSuccess: (j) => setJob(j) });
  const running = job?.state === "running" || conn.data?.sync.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => {
      const j = await api.get<JobState>("/sync/status");
      setJob(j);
      if (j.state !== "running") {
        void qc.invalidateQueries();
        toast(j.message ?? t("sync.finished"), j.state === "failed" ? "error" : "success");
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [running, qc, toast, t]);
  const configured = conn.data?.enable_banking_configured ?? true;
  return (
    <Button size="sm" onClick={() => start.mutate(undefined as never)} busy={running || start.isPending} disabled={!configured} title={configured ? t("sync.hint") : t("sync.notConfigured")}>
      {!running && <RefreshCw className="size-3.5" aria-hidden />}
      {compact ? <span className="sr-only">{t("sync.now")}</span> : running ? t("sync.running") : t("sync.now")}
    </Button>
  );
}

function ThemeToggle() {
  const { t } = useTranslation();
  const { theme, setTheme } = usePrefs();
  const next = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const Icon = theme === "system" ? Monitor : theme === "light" ? Sun : Moon;
  return (
    <IconButton label={t("theme.toggle", { theme: t(`theme.${theme}`), next: t(`theme.${next}`) })} onClick={() => setTheme(next)}>
      <Icon className="size-[18px]" />
    </IconButton>
  );
}

function Header() {
  const { t } = useTranslation();
  const health = useHealth();
  return (
    <header className="no-print sticky top-0 z-20 flex min-h-14 items-center gap-2 border-b border-border bg-bg/90 px-4 backdrop-blur sm:px-6">
      <div className="flex items-center gap-2 lg:hidden">
        <img src="/favicon.svg" alt="" className="size-6 rounded-md" />
        <span className="text-[15px] font-semibold">{t("app.name")}</span>
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <ScopeSwitch />
        {health.data && health.data.level !== "green" && (
          <NavLink to="/connections" className="hidden items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-muted hover:bg-surface-2 sm:flex">
            <Dot level={health.data.level} /> {health.data.level === "red" ? t("header.connectionRed") : t("header.connectionWarn")}
          </NavLink>
        )}
        <span className="hidden sm:block">
          <SyncButton />
        </span>
        <span className="sm:hidden">
          <SyncButton compact />
        </span>
        <LanguageSelect className="hidden !min-h-8 !w-auto !py-0 text-[13px] sm:block" />
        <ThemeToggle />
        <IconButton label={t("header.signOut")} onClick={() => void api.logout().finally(() => window.location.assign("/"))}>
          <LogOut className="size-[18px]" />
        </IconButton>
      </div>
    </header>
  );
}

/** The one language setting: the interface text AND the dates / numbers format. Built from the registry, one option per language. */
export function LanguageSelect({ id, className }: { id?: string; className?: string }) {
  const { t } = useTranslation();
  const { language, setLanguage } = usePrefs();
  return (
    <Select id={id} aria-label={id ? undefined : t("language.label")} value={language} onChange={(e) => isLanguageCode(e.target.value) && setLanguage(e.target.value)} className={className}>
      {LANGUAGES.map((l) => (
        <option key={l.code} value={l.code} lang={l.code}>
          {l.name}
        </option>
      ))}
    </Select>
  );
}

function MoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onClose={onClose} title={t("nav.more")} size="sm">
      <nav aria-label={t("nav.morePages")} className="flex flex-col gap-4">
        <NavList items={[ALERTS]} onNavigate={onClose} />
        <NavList items={PLAN} onNavigate={onClose} />
        <NavList items={PEOPLE} onNavigate={onClose} />
        <NavList items={SYSTEM} onNavigate={onClose} />
      </nav>
      <div className="mt-4 border-t border-border pt-4">
        <Field label={t("language.label")}>{(id) => <LanguageSelect id={id} />}</Field>
      </div>
    </Dialog>
  );
}

function ToastHost() {
  const { t } = useTranslation();
  const { toasts, dismiss } = useToast();
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 lg:bottom-6" aria-live="polite">
      {toasts.map((m) => (
        <div key={m.id} role={m.tone === "error" ? "alert" : "status"} className={cn("pointer-events-auto flex max-w-md items-start gap-3 rounded-lg border px-4 py-2.5 text-sm shadow-lg", m.tone === "error" ? "border-neg/40 bg-neg-soft text-neg" : m.tone === "success" ? "border-pos/40 bg-pos-soft text-pos" : "border-border-strong bg-surface text-text")}>
          <span className="flex-1">{m.text}</span>
          <button type="button" onClick={() => dismiss(m.id)} className="text-xs underline opacity-80">
            {t("toast.dismiss")}
          </button>
        </div>
      ))}
    </div>
  );
}

function PageFallback() {
  return (
    <div className="grid gap-4" aria-busy="true">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

export function Layout() {
  const { t } = useTranslation();
  const [more, setMore] = useState(false);
  const loc = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [loc.pathname]);
  return (
    <div className="flex min-h-dvh">
      <a href="#main" className="sr-only z-50 rounded bg-accent px-3 py-2 text-accent-fg focus:not-sr-only focus:fixed focus:left-3 focus:top-3">
        {t("nav.skip")}
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1200px] flex-1 px-4 pb-28 pt-5 outline-none sm:px-6 lg:pb-10">
          <Suspense fallback={<PageFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      <BottomNav onMore={() => setMore(true)} />
      <MoreSheet open={more} onClose={() => setMore(false)} />
      <ToastHost />
    </div>
  );
}
