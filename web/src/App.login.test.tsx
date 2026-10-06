import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PrefsProvider, ScopeProvider, ToastProvider, UserProvider } from "@/lib/app";
import { Routed } from "./App";
import type { UserInfo } from "@/api/types";

const user = (role: "adult" | "child"): UserInfo => ({ id: `u-${role}`, role, member_id: null, created_at: null, disabled: false, prefs: {} });

function mount(role: "adult" | "child") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PrefsProvider><ScopeProvider><ToastProvider>
        <UserProvider user={user(role)}><Routed /></UserProvider>
      </ToastProvider></ScopeProvider></PrefsProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } })));
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("a one-time login link opened with a valid session", () => {
  for (const role of ["adult", "child"] as const) {
    it(`${role}: goes home, shows no "page does not exist", and drops the fragment`, async () => {
      window.history.replaceState(null, "", "/login#t=unused-one-time-token");
      mount(role);
      await waitFor(() => expect(window.location.pathname).toBe("/"));
      expect(window.location.hash).toBe("");
      expect(window.location.href).not.toContain("unused-one-time-token");
      expect(screen.queryByText(/does not exist/i)).not.toBeInTheDocument();
    });
  }
});
