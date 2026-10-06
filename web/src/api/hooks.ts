import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient, UseQueryOptions } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useScope, useToast } from "@/lib/app";
import { humanize } from "@/lib/humanize";
import type * as T from "./types";

type P = Record<string, unknown> | undefined;

export function useGet<R>(path: string, params?: P, opts?: Partial<UseQueryOptions<R, ApiError>>) {
  return useQuery<R, ApiError>({
    queryKey: [path, params ?? {}],
    queryFn: ({ signal }) => api.get<R>(path, params, signal),
    staleTime: 20_000,
    ...opts,
  });
}

/** A scoped analytics query: the household / person switch applies to it. */
export function useScoped<R>(path: string, params?: P, opts?: Partial<UseQueryOptions<R, ApiError>>) {
  const { params: scope } = useScope();
  return useGet<R>(path, { ...scope, ...(params ?? {}) }, opts);
}

export const useSession = () => useGet<T.SessionInfo>("/session", undefined, { staleTime: Infinity });
export const useFilters = () => useGet<T.FiltersMeta>("/meta/filters", undefined, { staleTime: 60_000 });
export const useTaxonomy = () => useGet<T.Taxonomy>("/meta/taxonomy", undefined, { staleTime: 300_000 });
export const useHealth = () => useGet<T.Health>("/health", undefined, { refetchInterval: 120_000 });
export const useBalances = () => useScoped<T.Balances>("/accounts/balances");
export const useCashflow = (months = 6) => useScoped<T.Cashflow>("/analytics/cashflow", { months });
export const useMonthCategories = (month?: string) => useScoped<T.MonthCategories>("/analytics/month-categories", { month });
export const useForecast = (days = 90) => useScoped<T.Forecast>("/analytics/forecast", { days, points: true });
export const useBudgets = () => useScoped<T.BudgetStatus>("/budgets");
export const useInsights = () => useScoped<T.Insights>("/insights");
export const useQuestions = (status = "open") => useGet<T.Questions>("/questions", { status });
export const useProposals = () => useGet<{ proposals: T.Proposal[] }>("/proposals", { status: "pending" });
export const useConnections = () => useGet<T.Connections>("/connections");

/** Write helper: after any write, every cached read is refetched (a memory change can move any figure). */
export function useWrite<R, V>(fn: (v: V) => Promise<R>, o?: { success?: string; onSuccess?: (r: R, v: V) => void; invalidate?: boolean }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  return useMutation<R, ApiError, V>({
    mutationFn: fn,
    onSuccess: (r, v) => {
      if (o?.invalidate !== false) void qc.invalidateQueries();
      if (o?.success) toast(o.success, "success");
      o?.onSuccess?.(r, v);
    },
    onError: (e) => toast(e.message, "error"),
  });
}

export { keepPreviousData };

/** Preview of a write (the endpoint with dry_run=true): re-run shortly after the body changes, never writes. */
export function useDryRun<R>(path: string, body: unknown, enabled: boolean, method: "post" | "put" = "post") {
  const [state, setState] = useState<{ data: R | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: false });
  const key = JSON.stringify(body);
  useEffect(() => {
    if (!enabled) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    const t = setTimeout(() => {
      api[method]<R>(path, body, { dry_run: true })
        .then((data) => live && setState({ data, error: null, loading: false }))
        .catch((e: Error) => live && setState({ data: null, error: e.message, loading: false }));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, enabled, method]);
  return state;
}

/** `humanize` bound to the taxonomy (so only real category ids are turned into names). */
export function useHumanize(): (text: string) => string {
  const { data } = useTaxonomy();
  const ids = useMemo(() => new Set((data?.groups ?? []).flatMap((g) => g.categories.map((c) => c.id))), [data]);
  return (text: string) => humanize(text, ids.size ? ids : undefined);
}

export const useAlerts = (params?: { status?: string; kind?: string; severity?: string; include_resolved?: boolean }) => useGet<T.Alerts>("/alerts", params);
export const useAlertSummary = () => useGet<T.AlertSummary>("/alerts/summary", undefined, { refetchInterval: 120_000 });
export const useAlertChannels = () => useGet<T.AlertChannels>("/alerts/channels");
export const useAlertDigest = (enabled: boolean) => useGet<T.AlertDigest>("/alerts/digest", undefined, { enabled });

/* E14: the household, the children's money, who pays what (adult logins only) and the child's own view (/me) */
export const useHousehold = () => useGet<T.HouseholdOverview>("/household/overview");
export const useKids = (months = 6) => useGet<{ as_of: string; children: T.KidReport[] }>("/household/kids", { months });
export const useKidBudgets = () => useGet<{ as_of: string; budgets: T.KidBudgetStatus[] }>("/household/kid-budgets");
export const useAllocation = (months = 12) => useGet<T.AllocationReport>("/household/allocation", { months });
export const useMeSummary = () => useGet<T.MeSummary>("/me/summary");
export const useMeTransactions = (limit = 30) => useGet<T.MeTransactions>("/me/transactions", { limit });

/** Member ids -> display names (local pages only: names never leave this machine) */
export function usePeople() {
  const { data } = useFilters();
  return useMemo(() => {
    const byId = new Map((data?.members ?? []).map((m) => [m.id, m]));
    const name = (id: string | null | undefined) =>
      !id ? "Unassigned" : id === "joint" ? "Joint" : id === "other" ? "Someone else" : id === "unknown" ? "Unknown source" : (byId.get(id)?.name ?? id);
    return { members: data?.members ?? [], name, first: (id: string | null | undefined) => name(id).split(" ")[0], role: (id: string) => byId.get(id)?.role };
  }, [data]);
}

export const useRentalList = () => useGet<T.RentalList>("/rental/properties");
export const useRental = (id: string | undefined, params?: Record<string, unknown>) => useGet<T.RentalDetail>(`/rental/${id}`, params, { enabled: !!id });
export const useRentalTax = (id: string | undefined, year: number | undefined) => useGet<T.RentalTax>(`/rental/${id}/tax`, year ? { year } : undefined, { enabled: !!id });
export const useRentalIndicators = (id: string | undefined, params?: Record<string, unknown>) => useGet<T.RentalIndicators>(`/rental/${id}/indicators`, params, { enabled: !!id });
