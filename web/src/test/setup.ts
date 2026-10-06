import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n, { STORAGE_KEY } from "@/i18n";
import { setLocale } from "@/lib/format";

// Importing "@/i18n" initialises i18next synchronously in English, so t() already works on the first render of every test.
// Dates and numbers keep the French format the older tests were written against ("12,50"); the language tests switch it explicitly.
beforeEach(async () => {
  await i18n.changeLanguage("en");
  setLocale("fr-FR");
  document.documentElement.lang = "en";
});
afterEach(() => {
  try {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem("coach.locale");
  } catch {
    /* ignore */
  }
});

// jsdom has no <dialog> behaviour
if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
}
afterEach(() => cleanup());
