import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderApp } from "@/test/utils";
import { setLanguage } from "@/i18n";
import { resetCsrfForTests } from "@/lib/api";
import NotFound from "./NotFound";
import Review from "./Review";

// The page bodies follow the language picked in the header (docs/i18n.md). Synthetic data only.
const items = [
  { key: "CORNER SHOP", name: "Corner Shop", category: "food.groceries", confidence: 0.5, source: "llm", reason: "low_confidence", n: 5, total: "-200.00", at_stake: "200.00", banks: [], accounts: ["Main"] },
  { key: "PERSON LIKE", name: "Person Like", category: null, confidence: null, source: null, reason: "held_back_person_like", n: 1, total: "-30.00", at_stake: "30.00", banks: [], accounts: ["Main"] },
];

beforeEach(() => {
  resetCsrfForTests();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    let body: unknown = {};
    if (url.endsWith("/session")) body = { csrf_token: "tok" };
    else if (url.startsWith("/api/v1/review?")) body = { total: 2, items };
    else if (url.startsWith("/api/v1/meta/taxonomy")) body = { groups: [{ id: "food", categories: [{ id: "food.groceries", description: "" }] }] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("translated pages", () => {
  it("shows the review queue in French, with plurals and the reason codes translated", async () => {
    await setLanguage("fr");
    renderApp(<Review />);
    expect(await screen.findByRole("heading", { name: "Commerçants à vérifier" })).toBeInTheDocument();
    expect(await screen.findByText(/2 commerçants dans la file/)).toBeInTheDocument();
    expect(screen.getByText("Peut-être une personne (jamais envoyé à une IA)")).toBeInTheDocument();
    expect(screen.getByText(/5 transactions/)).toBeInTheDocument();
    expect(screen.getByText(/1 transaction ·/)).toBeInTheDocument();
    // the server's data (the merchant name) is shown as it is
    expect(screen.getByText("Corner Shop")).toBeInTheDocument();
  });

  it("shows the review queue in Italian", async () => {
    await setLanguage("it");
    renderApp(<Review />);
    expect(await screen.findByRole("heading", { name: "Esercenti da rivedere" })).toBeInTheDocument();
    expect(await screen.findByText(/2 esercenti in coda/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Applica" })).toHaveLength(2);
  });

  it("translates the page-not-found message", async () => {
    await setLanguage("fr");
    renderApp(<NotFound />);
    expect(screen.getByText("Cette page n'existe pas")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Retour au tableau de bord" })).toHaveAttribute("href", "/");
    await setLanguage("it");
    expect(await screen.findByText("Questa pagina non esiste")).toBeInTheDocument();
  });
});
