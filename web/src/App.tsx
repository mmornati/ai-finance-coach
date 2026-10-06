import { lazy, ReactNode, useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PrefsProvider, ScopeProvider, ToastProvider, UserProvider, usePrefs, useScope, useUser } from "@/lib/app";
import { Layout } from "@/components/Layout";
import { ApiError, api, UNAUTHORIZED_EVENT } from "@/lib/api";
import { Login } from "@/components/Login";
import { Spinner } from "@/components/ui";
import type { Locale } from "@/lib/format";
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
  const [state, setState] = useState<"checking" | "in" | "out" | "down">("checking");
  const [user, setUser] = useState<SessionInfo["user"]>(null);
  const check = () =>
    api
      .get<SessionInfo>("/session")
      .then((s) => {
        setUser(s?.user ?? null);
        setState("in");
      })
      .catch((e: unknown) => setState(e instanceof ApiError && e.status === 401 ? "out" : "down"));
  useEffect(() => {
    void check();
    const lost = () => setState("out");
    window.addEventListener(UNAUTHORIZED_EVENT, lost);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, lost);
  }, []);
  if (state === "checking") return <div className="grid min-h-dvh place-items-center"><Spinner label="Starting" /></div>;
  if (state === "out") return <Login onDone={() => { queryClient.clear(); void check(); }} />;
  if (state === "down")
    return (
      <div className="grid min-h-dvh place-items-center p-6 text-center text-sm text-muted">
        <p>The app server is not reachable. Is <code>coach ui</code> still running? Reload once it is back.</p>
      </div>
    );
  return <>{children(user)}</>;
}

/** The preferences stored for this login (E14-8) are applied once at sign-in: theme, language, the default person view. */
function ApplyUserPrefs() {
  const user = useUser();
  const { setTheme, setLocale } = usePrefs();
  const { scope, setScope } = useScope();
  useEffect(() => {
    if (!user) return;
    const p = user.prefs;
    if (p.theme === "light" || p.theme === "dark" || p.theme === "system") setTheme(p.theme);
    if (p.locale === "fr-FR" || p.locale === "en-GB") setLocale(p.locale as Locale);
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

function Routed() {
  const { locale } = usePrefs();
  const user = useUser();
  if (user?.role === "child") {
    // a child login only ever gets its own, read-only page (the server denies every other endpoint to it anyway)
    return (
      <div key={locale}>
        <BrowserRouter>
          <Routes>
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
