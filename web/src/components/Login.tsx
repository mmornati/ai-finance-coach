import { useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Fingerprint, KeyRound, ShieldCheck, Terminal } from "lucide-react";
import { Button, Notice, Spinner } from "./ui";
import { ApiError, exchangeToken, publicGet } from "@/lib/api";
import { passkeysSupported, signInWithPasskey } from "@/lib/webauthn";
import { errorText } from "@/i18n/server";
import { CommandBox } from "./CopyCommand";
import type { SessionMethods } from "@/api/types";

/** The only screen before a session. No cookie is ever given to a plain page load: the person opens the one-time link that
 *  `coach ui` printed in their own terminal; its token sits in the URL fragment (never sent to a server) and is traded once.
 *  E16: when the server enables them, a passkey (Face ID, Touch ID, a security key) opens a session too, and behind an SSO proxy the
 *  session opens by itself: this page then only appears when the proxy's identity is not mapped (`reason` says why). */
export function Login({ onDone, reason }: { onDone: () => void; reason?: ApiError | null }) {
  const { t } = useTranslation();
  const token = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("t");
  const [state, setState] = useState<"idle" | "working" | "failed">(token ? "working" : "idle");
  const [message, setMessage] = useState("");
  const [methods, setMethods] = useState<SessionMethods | null>(null);
  const [passkey, setPasskey] = useState<{ busy: boolean; error: string }>({ busy: false, error: "" });

  useEffect(() => {
    publicGet<SessionMethods>("/session/methods").then(setMethods).catch(() => setMethods(null));
  }, []);

  const usePasskey = () => {
    setPasskey({ busy: true, error: "" });
    signInWithPasskey()
      .then(() => onDone())
      .catch((e: unknown) => setPasskey({ busy: false, error: e instanceof ApiError ? errorText(e) : t("login.passkeyCancelled") }));
  };
  const sso = reason && reason.code.startsWith("sso_") ? reason : null;
  const identity = sso && typeof (sso.details as { identity?: unknown } | null)?.identity === "string" ? String((sso.details as { identity: string }).identity) : "";

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
        setMessage(e instanceof ApiError ? errorText(e) : t("login.unreachable"));
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      });
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 px-5 py-10">
      <div className="flex items-center gap-3">
        <img src="/favicon.svg" alt="" className="size-9 rounded-lg" />
        <h1 className="text-xl font-semibold">{t("app.name")}</h1>
      </div>
      {state === "working" ? (
        <Spinner label={t("login.signingIn")} />
      ) : (
        <>
          {state === "failed" && (
            <Notice tone="neg" title={t("login.failedTitle")}>
              {t("login.failedBody", { message })}
            </Notice>
          )}
          {sso && (
            <Notice tone={sso.code === "sso_unmapped" ? "warn" : "info"} title={t("login.ssoTitle")}>
              {errorText(sso)}
              {identity && <> <code className="rounded bg-surface-2 px-1">{identity}</code></>}
            </Notice>
          )}
          {methods?.passkeys && passkeysSupported() && (
            <div className="grid gap-3 rounded-xl border border-border bg-surface p-5">
              <h2 className="flex items-center gap-2 text-[15px] font-semibold">
                <Fingerprint className="size-4 text-accent" aria-hidden /> {t("login.passkeyHeading")}
              </h2>
              <p className="text-sm text-muted">{t("login.passkeyIntro")}</p>
              {passkey.error && <Notice tone="neg">{passkey.error}</Notice>}
              <Button variant="primary" onClick={usePasskey} busy={passkey.busy}>
                <Fingerprint className="size-4" aria-hidden /> {t("login.passkey")}
              </Button>
            </div>
          )}
          {methods?.sso && !sso && (
            <p className="flex items-center gap-2 text-sm text-muted">
              <ShieldCheck className="size-4 shrink-0" aria-hidden /> {t("login.ssoHint")}
            </p>
          )}
          <div className="grid gap-3 rounded-xl border border-border bg-surface p-5">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold">
              <KeyRound className="size-4 text-accent" aria-hidden /> {t("login.heading")}
            </h2>
            <p className="text-sm text-muted">
              <Trans i18nKey="login.intro" components={{ code: <code className="rounded bg-surface-2 px-1" /> }} />
            </p>
            <p className="flex items-center gap-2 text-sm text-muted">
              <Terminal className="size-4 shrink-0" aria-hidden /> {t("login.lost")}
            </p>
            <CommandBox command="uv run coach ui --login-link" />
            <p className="mt-1 text-xs text-muted"><Trans i18nKey="login.docker" components={{ code: <code /> }} /></p>
          </div>
          <Button onClick={() => window.location.reload()}>{t("login.reload")}</Button>
        </>
      )}
    </main>
  );
}
