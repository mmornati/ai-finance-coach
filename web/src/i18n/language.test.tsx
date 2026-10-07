import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTranslation } from "react-i18next";
import { PrefsProvider } from "@/lib/app";
import { LanguageSelect } from "@/components/Layout";
import { fmtNumber, getLocale } from "@/lib/format";
import { detectLanguage, hasSavedLanguage, STORAGE_KEY } from "@/i18n";
import { LANGUAGES, languageFromTag, localeOf } from "./languages";

function Probe() {
  const { t } = useTranslation();
  return (
    <>
      <LanguageSelect />
      <p data-testid="label">{t("nav.dashboard")}</p>
      <p data-testid="plural">{t("loan.schedule.instalmentsLeft", { count: 1 })} / {t("loan.schedule.instalmentsLeft", { count: 12 })}</p>
      <p data-testid="number">{fmtNumber(12345.5, 2)}</p>
    </>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("switching the language", () => {
  it("changes the strings, the plurals, the number format, <html lang> and the saved choice", async () => {
    render(
      <PrefsProvider>
        <Probe />
      </PrefsProvider>,
    );
    const select = screen.getByLabelText("Language");
    // one option per language of the registry, shown by its own name
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(LANGUAGES.map((l) => l.name));
    expect(screen.getByTestId("label")).toHaveTextContent("Dashboard");

    await userEvent.selectOptions(select, "fr");
    await waitFor(() => expect(screen.getByTestId("label")).toHaveTextContent("Tableau de bord"));
    expect(document.documentElement.lang).toBe("fr");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("fr");
    expect(getLocale()).toBe("fr-FR");
    expect(screen.getByTestId("number").textContent).toMatch(/^12\s345,50$/);
    expect(screen.getByTestId("plural")).toHaveTextContent("1 échéance restante / 12 échéances restantes");

    await userEvent.selectOptions(select, "it");
    await waitFor(() => expect(screen.getByTestId("label")).toHaveTextContent("Panoramica"));
    expect(document.documentElement.lang).toBe("it");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("it");
    expect(getLocale()).toBe("it-IT");
    expect(screen.getByTestId("number")).toHaveTextContent("12.345,50");
    expect(screen.getByTestId("plural")).toHaveTextContent("1 rata rimanente / 12 rate rimanenti");

    await userEvent.selectOptions(select, "en");
    await waitFor(() => expect(screen.getByTestId("label")).toHaveTextContent("Dashboard"));
    expect(document.documentElement.lang).toBe("en");
    expect(getLocale()).toBe("en-GB");
    expect(screen.getByTestId("number")).toHaveTextContent("12,345.50");
    expect(screen.getByTestId("plural")).toHaveTextContent("1 instalment left / 12 instalments left");
  });

  it("still works when the browser blocks storage", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(
      <PrefsProvider>
        <Probe />
      </PrefsProvider>,
    );
    await userEvent.selectOptions(screen.getByLabelText("Language"), "fr");
    await waitFor(() => expect(screen.getByTestId("label")).toHaveTextContent("Tableau de bord"));
  });
});

describe("the language to start in", () => {
  const languages = (...tags: string[]) => vi.spyOn(window.navigator, "languages", "get").mockReturnValue(tags);

  it("is the saved choice first", () => {
    languages("fr-FR");
    localStorage.setItem(STORAGE_KEY, "it");
    expect(detectLanguage()).toBe("it");
  });

  it("migrates the old dates-and-numbers choice", () => {
    languages("it-IT");
    localStorage.setItem("coach.locale", "fr-FR");
    expect(detectLanguage()).toBe("fr");
    localStorage.setItem("coach.locale", "en-GB");
    expect(detectLanguage()).toBe("en");
  });

  it("is the first supported browser language, whatever its region", () => {
    languages("de-DE", "it-CH", "fr");
    expect(detectLanguage()).toBe("it");
  });

  it("falls back to English", () => {
    languages("de-DE", "ja");
    expect(detectLanguage()).toBe("en");
    localStorage.setItem(STORAGE_KEY, "klingon");
    expect(detectLanguage()).toBe("en");
  });

  it("survives a browser that refuses to read storage", () => {
    languages("fr-CA");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(detectLanguage()).toBe("fr");
  });
});

describe("the language registry", () => {
  it("maps a tag to a supported language and a language to its Intl locale", () => {
    expect(languageFromTag("fr_FR")).toBe("fr");
    expect(languageFromTag("IT-it")).toBe("it");
    expect(languageFromTag("de")).toBeNull();
    expect(languageFromTag(null)).toBeNull();
    expect(LANGUAGES.map((l) => localeOf(l.code))).toEqual(["en-GB", "fr-FR", "it-IT"]);
  });
});

describe("the language stored for a login", () => {
  it("gives way to a language picked in this browser", () => {
    expect(hasSavedLanguage()).toBe(false);
    localStorage.setItem(STORAGE_KEY, "it");
    expect(hasSavedLanguage()).toBe(true);
    localStorage.setItem(STORAGE_KEY, "klingon");
    expect(hasSavedLanguage()).toBe(false);
  });
});
