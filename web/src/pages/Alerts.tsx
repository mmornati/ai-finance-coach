import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { AlertTriangle, Bell, BellOff, Check, FileText, FlaskConical, RotateCcw, ShieldCheck, VolumeX, Volume2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, EmptyState, Notice, PageHeader, Segmented, Select, Skeleton } from "@/components/ui";
import { useAlertChannels, useAlertDigest, useAlerts, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { serverLabel } from "@/i18n/server";
import { cn } from "@/lib/utils";
import type { AlertChannel, AlertChannelTest, AlertEvent, AlertKindRow } from "@/api/types";

type StatusFilter = "open" | "snoozed" | "acked" | "suppressed";
const STATUS_LABEL: Record<StatusFilter, ParseKeys<"alerts">> = { open: "status.open", snoozed: "status.snoozed", acked: "status.acked", suppressed: "status.suppressed" };
const EMPTY_TITLE: Record<Exclude<StatusFilter, "open">, ParseKeys<"alerts">> = { snoozed: "empty.snoozed", acked: "empty.acked", suppressed: "empty.suppressed" };
const SEVERITY: Record<string, ParseKeys<"alerts">> = { high: "severity.high", medium: "severity.medium", low: "severity.low" };
// product names stay as they are; the two descriptive ones are translated
const CHANNEL_LABEL: Record<AlertChannel["channel"], ParseKeys<"alerts"> | null> = { macos: "channels.macos", ntfy: null, email: "channels.email", telegram: null };
function useChannelLabel() {
  const { t } = useTranslation("alerts");
  return (channel: string) => {
    const key = CHANNEL_LABEL[channel as AlertChannel["channel"]];
    if (key) return t(key);
    return channel === "ntfy" ? "ntfy" : channel === "telegram" ? "Telegram" : channel;
  };
}

function EventCard({ e, kinds, act }: { e: AlertEvent; kinds: AlertKindRow[]; act: (what: "ack" | "snooze" | "restore" | "mute", e: AlertEvent) => void }) {
  const { t } = useTranslation("alerts");
  const tone = e.severity === "high" ? "neg" : e.severity === "medium" ? "warn" : "neutral";
  const sentTo = Object.keys(e.channels_sent);
  const kind = kinds.find((k) => k.kind === e.kind);
  return (
    <li>
      <Card>
        <div className="flex gap-3">
          <div className={cn("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full", e.severity === "high" ? "bg-neg-soft text-neg" : e.severity === "medium" ? "bg-warn-soft text-warn" : "bg-surface-2 text-muted")}>
            <AlertTriangle className="size-4" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[15px] font-semibold">{e.title}</h3>
              <Badge tone={tone}>{SEVERITY[e.severity] ? t(SEVERITY[e.severity]) : e.severity}</Badge>
              <Badge>{serverLabel("alertKind", e.kind, kind?.label)}</Badge>
              {e.status === "new" && <Badge tone="pos">{t("event.new")}</Badge>}
              {e.status === "snoozed" && <Badge>{t("event.snoozedUntil", { date: fmtDate(e.snoozed_until, "dayMonth") })}</Badge>}
              {e.status === "acked" && <Badge>{t("event.acknowledged")}</Badge>}
              {e.status === "suppressed" && <Badge title={t("event.suppressedTitle")}>{t("event.suppressed")}</Badge>}
              {e.escalations > 0 && <Badge tone="warn" title={t("event.escalatedTitle")}>{t("event.escalated")}</Badge>}
              {e.resolved && <Badge title={t("event.resolvedTitle")}>{t("event.resolved")}</Badge>}
            </div>
            <p className="mt-1 text-sm text-muted">{e.body}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-faint">
                {sentTo.length > 0 ? t("event.firstSeenSent", { date: fmtDate(e.created.slice(0, 10), "dayMonth"), channels: sentTo.join(", ") }) : t("event.firstSeen", { date: fmtDate(e.created.slice(0, 10), "dayMonth") })}
              </span>
              <span className="ml-auto flex flex-wrap gap-1.5">
                {(e.status === "new" || e.status === "sent") && (
                  <>
                    <Button size="sm" onClick={() => act("ack", e)}><Check className="size-3.5" aria-hidden /> {t("event.acknowledge")}</Button>
                    <Button size="sm" onClick={() => act("snooze", e)}><BellOff className="size-3.5" aria-hidden /> {t("event.snooze")}</Button>
                  </>
                )}
                {(e.status === "acked" || e.status === "snoozed" || e.status === "suppressed") && (
                  <Button size="sm" onClick={() => act("restore", e)}><RotateCcw className="size-3.5" aria-hidden /> {t("event.restore")}</Button>
                )}
                {!kind?.muted && (e.status === "new" || e.status === "sent") && (
                  <Button size="sm" variant="ghost" onClick={() => act("mute", e)} title={t("event.muteKindTitle", { kind: serverLabel("alertKind", e.kind, kind?.label) })}><VolumeX className="size-3.5" aria-hidden /> {t("event.muteKind")}</Button>
                )}
              </span>
            </div>
          </div>
        </div>
      </Card>
    </li>
  );
}

function KindsCard({ kinds }: { kinds: AlertKindRow[] }) {
  const { t } = useTranslation("alerts");
  const mute = useWrite((v: { kind: string; on: boolean }) => api.post(`/alerts/kinds/${v.kind}/${v.on ? "mute" : "unmute"}`, {}), { success: t("toast.updated") });
  const snooze = useWrite((v: { kind: string; wake: boolean }) => (v.wake ? api.post(`/alerts/kinds/${v.kind}/wake`, {}) : api.post(`/alerts/kinds/${v.kind}/snooze`, { days: 7 })), { success: t("toast.updated") });
  return (
    <Card title={t("kinds.title")} subtitle={t("kinds.subtitle")}>
      <ul className="divide-y divide-border text-sm" aria-label={t("kinds.title")}>
        {kinds.map((k) => (
          <li key={k.kind} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1">{serverLabel("alertKind", k.kind, k.label)}</span>
            {k.disabled_in_config && <Badge title={t("kinds.offInConfigTitle")}>{t("kinds.offInConfig")}</Badge>}
            {k.digest_only && <Badge title={t("kinds.digestOnlyTitle")}>{t("kinds.digestOnly")}</Badge>}
            {k.muted && <Badge tone="warn">{t("kinds.muted")}</Badge>}
            {k.snoozed_until && <Badge>{t("kinds.heldUntil", { date: fmtDate(k.snoozed_until, "dayMonth") })}</Badge>}
            <span className="flex gap-1.5">
              {k.muted ? (
                <Button size="sm" aria-label={t("kinds.unmuteAria", { kind: serverLabel("alertKind", k.kind, k.label) })} onClick={() => mute.mutate({ kind: k.kind, on: false })}><Volume2 className="size-3.5" aria-hidden /> {t("kinds.unmute")}</Button>
              ) : (
                <Button size="sm" aria-label={t("kinds.muteAria", { kind: serverLabel("alertKind", k.kind, k.label) })} onClick={() => mute.mutate({ kind: k.kind, on: true })}><VolumeX className="size-3.5" aria-hidden /> {t("kinds.mute")}</Button>
              )}
              <Button size="sm" aria-label={k.snoozed_until ? t("kinds.wakeAria", { kind: serverLabel("alertKind", k.kind, k.label) }) : t("kinds.holdAria", { kind: serverLabel("alertKind", k.kind, k.label) })} onClick={() => snooze.mutate({ kind: k.kind, wake: !!k.snoozed_until })}>
                <BellOff className="size-3.5" aria-hidden /> {k.snoozed_until ? t("kinds.wake") : t("kinds.hold")}
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ChannelRow({ c, onTest, busy }: { c: AlertChannel; onTest: (name: AlertChannel["channel"]) => void; busy: boolean }) {
  const { t } = useTranslation("alerts");
  const channelLabel = useChannelLabel();
  return (
    <li className="flex flex-wrap items-center gap-2 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="font-medium">{channelLabel(c.channel)}</div>
        <div className="truncate text-xs text-faint">{c.target}{c.enabled ? ` · ${t("channels.sentLastWeek", { count: c.sent_last_7_days })}` : ""}</div>
        {c.problems.map((p) => <div key={p} className="text-xs text-warn">{p}</div>)}
      </div>
      <Badge tone={c.ready ? "pos" : c.enabled ? "warn" : "neutral"}>{c.ready ? t("channels.ready") : c.enabled ? t("channels.notReady") : t("channels.off")}</Badge>
      <Button size="sm" busy={busy} onClick={() => onTest(c.channel)} aria-label={t("channels.testAria", { channel: channelLabel(c.channel) })}><FlaskConical className="size-3.5" aria-hidden /> {t("channels.test")}</Button>
    </li>
  );
}

function ChannelsCard() {
  const { t } = useTranslation("alerts");
  const channelLabel = useChannelLabel();
  const q = useAlertChannels();
  const [shown, setShown] = useState<AlertChannelTest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const test = useWrite((name: AlertChannel["channel"]) => api.post<AlertChannelTest>(`/alerts/channels/${name}/test`, {}), {
    invalidate: false,
    onSuccess: (r) => setShown(r),
  });
  return (
    <Card title={t("channels.title")} subtitle={t("channels.subtitle")}>
      <Async q={q} skeleton={<Skeleton className="h-40 w-full" />}>
        {(d) => (
          <>
            <ul className="divide-y divide-border text-sm" aria-label={t("channels.list")}>
              {d.channels.map((c) => (
                <ChannelRow key={c.channel} c={c} busy={busy === c.channel} onTest={(name) => { setBusy(name); test.mutate(name, { onSettled: () => setBusy(null) }); }} />
              ))}
            </ul>
            <div className="mt-3 grid gap-2 text-xs text-faint">
              <p>
                <ShieldCheck className="mr-1 inline size-3.5 align-text-bottom" aria-hidden />
                <Trans t={t} i18nKey={d.external_detail === "minimal" ? "channels.privacyMinimal" : "channels.privacyOther"} values={{ detail: d.external_detail }} components={{ strong: <strong /> }} />{" "}
                {t(d.quiet_hours ? "channels.limitsQuiet" : "channels.limits", { severity: SEVERITY[d.min_severity] ? t(SEVERITY[d.min_severity]) : d.min_severity, max: d.max_per_week, quiet: d.quiet_hours })}
              </p>
              <p>{d.how_to_enable}</p>
            </div>
          </>
        )}
      </Async>
      <Dialog open={!!shown} onClose={() => setShown(null)} title={shown ? t("channels.testTitle", { channel: channelLabel(shown.channel) }) : ""} size="lg"
        footer={<Button onClick={() => setShown(null)}>{t("channels.close")}</Button>}>
        {shown && (
          <div className="grid gap-3">
            <Notice tone="pos" title={t("channels.dryRun")}>{t("channels.dryRunBody", { sample: shown.sample })}</Notice>
            {!shown.enabled && <Notice tone="info">{t("channels.disabledInConfig")}</Notice>}
            {shown.problems.length > 0 && <Notice tone="warn">{shown.problems.join("; ")}</Notice>}
            {shown.note && <Notice tone="info">{shown.note}</Notice>}
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-2 p-3 text-xs" data-testid="test-output">{shown.text}</pre>
          </div>
        )}
      </Dialog>
    </Card>
  );
}

function DigestCard() {
  const { t } = useTranslation("alerts");
  const [open, setOpen] = useState(false);
  const q = useAlertDigest(open);
  return (
    <Card title={t("digest.title")} subtitle={t("digest.subtitle")}>
      <Button size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}><FileText className="size-3.5" aria-hidden /> {open ? t("digest.hide") : t("digest.show")}</Button>
      {open && (
        <div className="mt-3">
          <Async q={q} skeleton={<Skeleton className="h-48 w-full" />}>
            {(d) => <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-2 p-3 text-xs" data-testid="digest-preview">{d.markdown}</pre>}
          </Async>
        </div>
      )}
    </Card>
  );
}

export default function Alerts() {
  const { t } = useTranslation("alerts");
  const [status, setStatus] = useState<StatusFilter>("open");
  const [kind, setKind] = useState("");
  const [severity, setSeverity] = useState("");
  const q = useAlerts({ status: status === "open" ? undefined : status, kind: kind || undefined, severity: severity || undefined });
  const [last, setLast] = useState<{ new: number; escalated: number; resolved: number } | null>(null);
  const check = useWrite(() => api.post<{ new: number; escalated: number; resolved: number }>("/alerts/check", {}), { onSuccess: (r) => setLast(r) });
  const ack = useWrite((id: string) => api.post(`/alerts/${id}/ack`, {}), { success: t("toast.acknowledged") });
  const snooze = useWrite((id: string) => api.post(`/alerts/${id}/snooze`, { days: 7 }), { success: t("toast.snoozed") });
  const restore = useWrite((id: string) => api.post(`/alerts/${id}/restore`, {}), { success: t("toast.restored") });
  const mute = useWrite((k: string) => api.post(`/alerts/kinds/${k}/mute`, {}), { success: t("toast.kindMuted") });
  const act = (what: "ack" | "snooze" | "restore" | "mute", e: AlertEvent) => {
    if (what === "ack") ack.mutate(e.id);
    else if (what === "snooze") snooze.mutate(e.id);
    else if (what === "restore") restore.mutate(e.id);
    else mute.mutate(e.kind);
  };
  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={<Button variant="primary" busy={check.isPending} onClick={() => check.mutate(undefined as never)}><Bell className="size-4" aria-hidden /> {t("checkNow")}</Button>}
      />
      <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
        {(d) => {
          if (!d.ready) return <Notice tone="warn" title={t("notReady")}>{d.message}</Notice>;
          const items = d.items.filter((e) => (status === "open" ? e.status === "new" || e.status === "sent" : true));
          return (
            <div className="grid gap-4">
              {last && <Notice tone="pos" title={t("checked")}>{t("checkedBody", { new: last.new, escalated: last.escalated, resolved: last.resolved })}</Notice>}
              {d.enabled === false && <Notice tone="warn">{t("disabled")}</Notice>}
              <div className="flex flex-wrap items-center gap-3">
                <Segmented label={t("filter.status")} value={status} onChange={setStatus} options={(Object.keys(STATUS_LABEL) as StatusFilter[]).map((s) => ({ value: s, label: s === "open" ? t("filter.openCount", { count: d.counts.open }) : t(STATUS_LABEL[s]) }))} />
                <label className="flex items-center gap-2 text-[13px] text-muted">{t("filter.severity")}
                  <Select aria-label={t("filter.severity")} value={severity} onChange={(e) => setSeverity(e.target.value)} className="w-32">
                    <option value="">{t("filter.all")}</option><option value="high">{t("severityOption.high")}</option><option value="medium">{t("severityOption.medium")}</option><option value="low">{t("severityOption.low")}</option>
                  </Select>
                </label>
                <label className="flex items-center gap-2 text-[13px] text-muted">{t("filter.kind")}
                  <Select aria-label={t("filter.kind")} value={kind} onChange={(e) => setKind(e.target.value)} className="w-52">
                    <option value="">{t("filter.all")}</option>
                    {d.kinds.map((k) => <option key={k.kind} value={k.kind}>{serverLabel("alertKind", k.kind, k.label)}</option>)}
                  </Select>
                </label>
              </div>
              {items.length === 0 ? (
                <Card><EmptyState icon={<Check className="size-6" />} title={status === "open" ? t("empty.openTitle") : t(EMPTY_TITLE[status])}>{status === "open" ? t("empty.openBody") : t("empty.body")}</EmptyState></Card>
              ) : (
                <ul className="grid gap-3" aria-label={t("list")}>{items.map((e) => <EventCard key={e.id} e={e} kinds={d.kinds} act={act} />)}</ul>
              )}
              <div className="grid items-start gap-4 lg:grid-cols-2">
                <ChannelsCard />
                <div className="grid gap-4">
                  <DigestCard />
                  <KindsCard kinds={d.kinds} />
                </div>
              </div>
            </div>
          );
        }}
      </Async>
    </>
  );
}
