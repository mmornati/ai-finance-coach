import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { useGet } from "@/api/hooks";
import type { WizardStatus } from "@/api/types";

/** A command to run in the person's own terminal, with a copy button (the page never runs it). */
export function CopyCommand({ command: given, className }: { command: string; className?: string }) {
  // Inside the Docker image the server says so (setup status): `uv run coach X` becomes `docker compose exec coach coach X`. Unknown (not signed in): as given.
  const container = useGet<WizardStatus>("/setup/wizard", undefined, { retry: false }).data?.container === true;
  return <CommandBox command={container ? given.replace(/^uv run coach /, "docker compose exec coach coach ") : given} className={className} />;
}

/** The box itself: no data fetching (the sign-in page has no session and no query client). */
export function CommandBox({ command, className }: { command: string; className?: string }) {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setDone(true);
      setTimeout(() => setDone(false), 2000);
    } catch {
      /* clipboard blocked: the command stays selectable */
    }
  }
  return (
    <div className={cn("flex items-center gap-2 rounded-lg border border-border bg-surface-2 py-1 pl-3 pr-1", className)}>
      <code className="min-w-0 flex-1 select-all overflow-x-auto whitespace-nowrap font-mono text-[13px]">{command}</code>
      <button type="button" onClick={copy} aria-label={t("copy.aria", { command })} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted hover:bg-surface hover:text-text">
        {done ? <Check className="size-3.5 text-pos" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
        {done ? t("copy.copied") : t("copy.copy")}
      </button>
    </div>
  );
}
