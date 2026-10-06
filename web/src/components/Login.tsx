import { useEffect, useState } from "react";
import { KeyRound, Terminal } from "lucide-react";
import { Button, Notice, Spinner } from "./ui";
import { ApiError, exchangeToken } from "@/lib/api";
import { CommandBox } from "./CopyCommand";

/** The only screen before a session. No cookie is ever given to a plain page load: the person opens the one-time link that
 *  `coach ui` printed in their own terminal; its token sits in the URL fragment (never sent to a server) and is traded once. */
export function Login({ onDone }: { onDone: () => void }) {
  const token = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("t");
  const [state, setState] = useState<"idle" | "working" | "failed">(token ? "working" : "idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) return;
    exchangeToken(token)
      .then(() => {
        // the token never stays in the address bar or the history
        window.history.replaceState(null, "", window.location.pathname === "/login" ? "/" : window.location.pathname + window.location.search);
        onDone();
      })
      .catch((e: unknown) => {
        setState("failed");
        setMessage(e instanceof ApiError ? e.message : "Could not reach the app server.");
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      });
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 px-5 py-10">
      <div className="flex items-center gap-3">
        <img src="/favicon.svg" alt="" className="size-9 rounded-lg" />
        <h1 className="text-xl font-semibold">Coach</h1>
      </div>
      {state === "working" ? (
        <Spinner label="Signing in" />
      ) : (
        <>
          {state === "failed" && (
            <Notice tone="neg" title="This login link did not work">
              {message} Links work once and only for two minutes.
            </Notice>
          )}
          <div className="grid gap-3 rounded-xl border border-border bg-surface p-5">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold">
              <KeyRound className="size-4 text-accent" aria-hidden /> Open the login link from your terminal
            </h2>
            <p className="text-sm text-muted">
              For your safety this page never signs you in by itself. When you start the app, <code className="rounded bg-surface-2 px-1">coach ui</code> prints a
              one-time login link in the terminal: open that link in this browser.
            </p>
            <p className="flex items-center gap-2 text-sm text-muted">
              <Terminal className="size-4 shrink-0" aria-hidden /> Lost it, or already used? Print a fresh one:
            </p>
            <CommandBox command="uv run coach ui --login-link" />
            <p className="mt-1 text-xs text-muted">In Docker: <code>docker compose exec coach coach ui --login-link</code></p>
          </div>
          <Button onClick={() => window.location.reload()}>I opened the link: reload</Button>
        </>
      )}
    </main>
  );
}
