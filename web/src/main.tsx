import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { initLanguage } from "./i18n";

// the saved or detected language is loaded (and <html lang> and the number format set) before the first render: no flash of English
void initLanguage()
  .catch(() => undefined) // a failed language chunk leaves the English interface
  .then(() => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
  });

// Offline shell: static files only (see public/sw.js: the API is never cached)
if ("serviceWorker" in navigator && location.protocol !== "file:" && import.meta.env.PROD) {
  window.addEventListener("load", () => void navigator.serviceWorker.register("/sw.js").catch(() => undefined));
}
