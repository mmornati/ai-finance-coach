import { lazy, ReactNode, useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PrefsProvider, ScopeProvider, ToastProvider, UserProvider, usePrefs, useScope, useUser } from "@/lib/app";
import { Layout } from "@/components/Layout";
import { ApiError, api, UNAUTHORIZED_EVENT } from "@/lib/api";
import { Login } from "@/components/Login";
import { Spinner } from "@/components/ui";
import { hasSavedLanguage, setLanguage } from "@/i18n";
import { languageFromTag } from "@/i18n/languages";
import type { SessionInfo } from "@/api/types";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Transactions = lazy(() => import("@/pages/Transactions"));
const Categories = lazy(() => import("@/pages/Categories"));
const CategoryPage = lazy(() => import("@/pages/CategoryPage"));
const Review = lazy(() => import("@/pages/Review"));
const Budgets = lazy(() => import("@/pages/Budgets"));
const Subscriptions = lazy(() => import("@/pages/Subscriptions"));
const Wealth = lazy(() => import("@/pages/Wealth"));
const CalendarPage = lazy(() => import("@/pages/CalendarPage"));
const Insights = lazy(() => import("@/pages/Insights"));
const Alerts = lazy(() => import("@/pages/Alerts"));
const Coach = lazy(() => import("@/pages/Coach"));
const Memory = lazy(() => import("@/pages/Memory"));
const Gold = lazy(() => import("@/pages/Gold"));
const Usage = lazy(() => import("@/pages/Usage"));
const Connections = lazy(() => import("@/pages/Connections"));
const Setup = lazy(() => import("@/pages/Setup"));
const Household = lazy(() => import("@/pages/Household"));
const Kids = lazy(() => import("@/pages/Kids"));
const WhoPays = lazy(() => import("@/pages/WhoPays"));
const Rental = lazy(() => import("@/pages/Rental"));
const KidHome = lazy(() => import("@/pages/KidHome"));
const NotFound = lazy(() => import("@/pages/NotFound"));

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 1,
      refetchOnWindowFocus: false,
    },
  },
});

/** Nothing but the login screen is shown until the server accepted a session (one-time link, see Login). */
function AuthGate({ children }: { children: (user: SessionInfo["user"]) => ReactNode }) {
  const { t } = useTranslation();
  const [state, setState] = useState<"checking" | "in" | "out" | "down">("checking");
  const [user, setUser] = useState<SessionInfo["user"]>(null);
  const [reason, setReason] = useState<ApiError | null>(null);     // E16: why there is no session (an SSO identity not mapped, for one)
  const check = () =>
    api
      .get<SessionInfo>("/session")
      .then((s) => {
        setUser(s?.user ?? null);
        setReason(null);
        setState("in");
      })
      .catch((e: unknown) => {
        setReason(e instanceof ApiError ? e : null);
        setState(e instanceof ApiError && e.status === 401 ? "out" : "down");
      });
  useEffect(() => {
    void check();
    const lost = () => setState("out");
    window.addEventListener(UNAUTHORIZED_EVENT, lost);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, lost);
  }, []);
  if (state === "checking") return <div className="grid min-h-dvh place-items-center"><Spinner label={t("app.starting")} /></div>;
  if (state === "out") return <Login reason={reason} onDone={() => { queryClient.clear(); void check(); }} />;
  if (state === "down")
    return (
      <div className="grid min-h-dvh place-items-center p-6 text-center text-sm text-muted">
        <p><Trans i18nKey="app.serverDown" components={{ code: <code /> }} /></p>
      </div>
    );
  return <>{children(user)}</>;
}

/** The preferences stored for this login (E14-8) are applied once at sign-in: theme, language, the default person view. */
function ApplyUserPrefs() {
  const user = useUser();
  const { setTheme } = usePrefs();
  const { scope, setScope } = useScope();
  useEffect(() => {
    if (!user) return;
    const p = user.prefs;
    if (p.theme === "light" || p.theme === "dark" || p.theme === "system") setTheme(p.theme);
    // the stored value is a locale tag ("fr-FR", "en-GB", "it-IT") or a language code: both name a language. A language picked in
    // this browser wins, and the login's one is not saved locally, so it keeps being followed until the person picks one here.
    const lang = languageFromTag(p.locale);
    if (lang && !hasSavedLanguage()) void setLanguage(lang, { persist: false }).catch(() => undefined);
    if (user.role === "adult" && p.default_member && !scope.member && !localStorage.getItem("coach.scope")) setScope({ ...scope, member: p.default_member });
    // once per login
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);
  return null;
}

function Landing() {
  const user = useUser();
  const to = user?.prefs.landing === "transactions" ? "/transactions" : user?.prefs.landing === "kids" ? "/kids" : null;
  return to ? <Navigate to={to} replace /> : <Dashboard />;
}

/** A one-time login link opened while a session is already valid (E13 hand test): not a page. Go home and drop the fragment so the unused token does
 *  not linger in the address bar or the history. The token is left unused (it expires); nothing is consumed silently. */
export function LoginRedirect() {
  useEffect(() => {
    try {
      window.history.replaceState(window.history.state, "", "/");
    } catch {
      /* history blocked: the redirect below still leaves the page */
    }
  }, []);
  return <Navigate to="/" replace />;
}

export function Routed() {
  const { locale } = usePrefs();
  const user = useUser();
  if (user?.role === "child") {
    // a child login only ever gets its own, read-only page (the server denies every other endpoint to it anyway)
    return (
      <div key={locale}>
        <BrowserRouter>
          <Routes>
            <Route path="login" element={<LoginRedirect />} />
            <Route path="*" element={<KidHome />} />
          </Routes>
        </BrowserRouter>
      </div>
    );
  }
  return (
    <div key={locale}>
      <BrowserRouter>
        <Routes>
          <Route path="login" element={<LoginRedirect />} />
          <Route element={<Layout />}>
            <Route index element={<Landing />} />
            <Route path="transactions" element={<Transactions />} />
            <Route path="categories" element={<Categories />} />
            <Route path="categories/:id" element={<CategoryPage />} />
            <Route path="review" element={<Review />} />
            <Route path="budgets" element={<Budgets />} />
            <Route path="subscriptions" element={<Subscriptions />} />
            <Route path="wealth" element={<Wealth />} />
            <Route path="rental" element={<Rental />} />
            <Route path="calendar" element={<CalendarPage />} />
            <Route path="insights" element={<Insights />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="coach" element={<Coach />} />
            <Route path="memory" element={<Memory />} />
            <Route path="household" element={<Household />} />
            <Route path="kids" element={<Kids />} />
            <Route path="who-pays" element={<WhoPays />} />
            <Route path="gold" element={<Gold />} />
            <Route path="usage" element={<Usage />} />
            <Route path="connections" element={<Connections />} />
            <Route path="setup" element={<Setup />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </div>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <PrefsProvider>
        <ScopeProvider>
          <ToastProvider>
            <AuthGate>
              {(user) => (
                <UserProvider user={user ?? null}>
                  <ApplyUserPrefs />
                  <Routed />
                </UserProvider>
              )}
            </AuthGate>
          </ToastProvider>
        </ScopeProvider>
      </PrefsProvider>
    </QueryClientProvider>
  );
}
