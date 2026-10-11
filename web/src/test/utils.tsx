import { ReactElement } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { PrefsProvider, ScopeProvider, ToastProvider } from "@/lib/app";

export function renderApp(ui: ReactElement, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PrefsProvider>
        <ScopeProvider>
          <ToastProvider>
            <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
          </ToastProvider>
        </ScopeProvider>
      </PrefsProvider>
    </QueryClientProvider>,
  );
}

/** Choose a category in a CategoryPicker: open it, search the label, click the option. */
export async function pickCategory(combobox: HTMLElement, label: string) {
  await userEvent.click(combobox);
  await userEvent.type(combobox, label);
  await userEvent.click(await within(screen.getByRole("listbox")).findByRole("option", { name: label }));
}
