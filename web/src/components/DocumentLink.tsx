import { ExternalLink, FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

export function DocumentLink({ doc, className }: { doc: string; className?: string }) {
  const { t } = useTranslation("common");
  const isUrl = doc.startsWith("http://") || doc.startsWith("https://");
  if (!isUrl) {
    return (
      <span className={cn("inline-flex items-center gap-1 rounded bg-surface-2 px-2 py-0.5 text-xs text-muted font-mono", className)}>
        <FileText className="size-3 text-muted shrink-0" aria-hidden />
        <span className="truncate max-w-48">{doc}</span>
      </span>
    );
  }

  let label = t("ui.cloudDoc");
  try {
    const u = new URL(doc);
    const host = u.hostname.toLowerCase();
    if (host === "drive.google.com" || host.endsWith(".drive.google.com") || host === "docs.google.com" || host.endsWith(".docs.google.com")) {
      label = "Google Drive";
    } else if (host === "onedrive.live.com" || host.endsWith(".onedrive.live.com") || host === "sharepoint.com" || host.endsWith(".sharepoint.com")) {
      label = "OneDrive";
    } else if (host === "dropbox.com" || host.endsWith(".dropbox.com")) {
      label = "Dropbox";
    } else {
      label = host.replace(/^www\./, "");
    }
  } catch {
    // fallback to generic translated label
  }

  return (
    <a
      href={doc}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-accent/30 bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent hover:underline hover:bg-accent-soft/80 transition-colors",
        className,
      )}
      title={doc}
    >
      <FileText className="size-3 text-accent shrink-0" aria-hidden />
      <span>{label}</span>
      <ExternalLink className="size-2.5 opacity-70 shrink-0" aria-hidden />
    </a>
  );
}
