import { useState } from "react";
import { AlertTriangle, Bell, BellOff, Check, FileText, FlaskConical, RotateCcw, ShieldCheck, VolumeX, Volume2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, EmptyState, Notice, PageHeader, Segmented, Select, Skeleton } from "@/components/ui";
import { useAlertChannels, useAlertDigest, useAlerts, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AlertChannel, AlertChannelTest, AlertEvent, AlertKindRow } from "@/api/types";

type StatusFilter = "open" | "snoozed" | "acked" | "suppressed";
const STATUS_LABEL: Record<StatusFilter, string> = { open: "Open", snoozed: "Snoozed", acked: "Acknowledged", suppressed: "Suppressed" };
const CHANNEL_LABEL = { macos: "macOS notification", ntfy: "ntfy", email: "E-mail (SMTP)", telegram: "Telegram" } as const;

function EventCard({ e, kinds, act }: { e: AlertEvent; kinds: AlertKindRow[]; act: (what: "ack" | "snooze" | "restore" | "mute", e: AlertEvent) => void }) {
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
              <Badge tone={tone}>{e.severity}</Badge>
              <Badge>{kind?.label ?? e.kind}</Badge>
              {e.status === "new" && <Badge tone="pos">new</Badge>}
              {e.status === "snoozed" && <Badge>snoozed until {fmtDate(e.snoozed_until, "dayMonth")}</Badge>}
              {e.status === "acked" && <Badge>acknowledged</Badge>}
              {e.status === "suppressed" && <Badge title="Below your minimum severity, or its kind is muted: kept here, never sent.">suppressed</Badge>}
              {e.escalations > 0 && <Badge tone="warn" title="Its severity went up since it was first seen">escalated</Badge>}
              {e.resolved && <Badge title="The situation no longer exists">resolved</Badge>}
            </div>
            <p className="mt-1 text-sm text-muted">{e.body}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-faint">
                First seen {fmtDate(e.created.slice(0, 10), "dayMonth")}
                {sentTo.length > 0 && ` · sent to ${sentTo.join(", ")}`}
              </span>
              <span className="ml-auto flex flex-wrap gap-1.5">
                {(e.status === "new" || e.status === "sent") && (
                  <>
                    <Button size="sm" onClick={() => act("ack", e)}><Check className="size-3.5" aria-hidden /> Acknowledge</Button>
                    <Button size="sm" onClick={() => act("snooze", e)}><BellOff className="size-3.5" aria-hidden /> Snooze 7 days</Button>
                  </>
                )}
                {(e.status === "acked" || e.status === "snoozed" || e.status === "suppressed") && (
                  <Button size="sm" onClick={() => act("restore", e)}><RotateCcw className="size-3.5" aria-hidden /> Restore</Button>
                )}
                {!kind?.muted && (e.status === "new" || e.status === "sent") && (
                  <Button size="sm" variant="ghost" onClick={() => act("mute", e)} title={`Stop alerts of this kind (${kind?.label ?? e.kind}) until you unmute it`}><VolumeX className="size-3.5" aria-hidden /> Mute this kind</Button>
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
  const mute = useWrite((v: { kind: string; on: boolean }) => api.post(`/alerts/kinds/${v.kind}/${v.on ? "mute" : "unmute"}`, {}), { success: "Updated" });
  const snooze = useWrite((v: { kind: string; wake: boolean }) => (v.wake ? api.post(`/alerts/kinds/${v.kind}/wake`, {}) : api.post(`/alerts/kinds/${v.kind}/snooze`, { days: 7 })), { success: "Updated" });
  return (
    <Card title="Kinds of alert" subtitle="Mute one you do not want, or hold it for a week. Thresholds and per-kind switches live in config.toml ([alerts]).">
      <ul className="divide-y divide-border text-sm" aria-label="Kinds of alert">
        {kinds.map((k) => (
          <li key={k.kind} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1">{k.label}</span>
            {k.disabled_in_config && <Badge title="[alerts] disabled_kinds in config.toml">off in config</Badge>}
            {k.digest_only && <Badge title="[alerts] digest_only_kinds: shown here and in the weekly summary, never sent one by one">digest only</Badge>}
            {k.muted && <Badge tone="warn">muted</Badge>}
            {k.snoozed_until && <Badge>held until {fmtDate(k.snoozed_until, "dayMonth")}</Badge>}
            <span className="flex gap-1.5">
              {k.muted ? (
                <Button size="sm" aria-label={`Unmute ${k.label}`} onClick={() => mute.mutate({ kind: k.kind, on: false })}><Volume2 className="size-3.5" aria-hidden /> Unmute</Button>
              ) : (
                <Button size="sm" aria-label={`Mute ${k.label}`} onClick={() => mute.mutate({ kind: k.kind, on: true })}><VolumeX className="size-3.5" aria-hidden /> Mute</Button>
              )}
              <Button size="sm" aria-label={k.snoozed_until ? `Wake ${k.label}` : `Hold ${k.label} for 7 days`} onClick={() => snooze.mutate({ kind: k.kind, wake: !!k.snoozed_until })}>
                <BellOff className="size-3.5" aria-hidden /> {k.snoozed_until ? "Wake" : "Hold 7 days"}
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ChannelRow({ c, onTest, busy }: { c: AlertChannel; onTest: (name: AlertChannel["channel"]) => void; busy: boolean }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="font-medium">{CHANNEL_LABEL[c.channel]}</div>
        <div className="truncate text-xs text-faint">{c.target}{c.enabled ? ` · ${c.sent_last_7_days} message(s) in the last 7 days` : ""}</div>
        {c.problems.map((p) => <div key={p} className="text-xs text-warn">{p}</div>)}
      </div>
      <Badge tone={c.ready ? "pos" : c.enabled ? "warn" : "neutral"}>{c.ready ? "ready" : c.enabled ? "not ready" : "off"}</Badge>
      <Button size="sm" busy={busy} onClick={() => onTest(c.channel)} aria-label={`Preview a test message for ${CHANNEL_LABEL[c.channel]} (dry run)`}><FlaskConical className="size-3.5" aria-hidden /> Test (dry run)</Button>
    </li>
  );
}

function ChannelsCard() {
  const q = useAlertChannels();
  const [shown, setShown] = useState<AlertChannelTest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const test = useWrite((name: AlertChannel["channel"]) => api.post<AlertChannelTest>(`/alerts/channels/${name}/test`, {}), {
    invalidate: false,
    onSuccess: (r) => setShown(r),
  });
  return (
    <Card title="Channels" subtitle="Read-only. The in-app feed is always on; every other channel is off until you enable it in config.toml.">
      <Async q={q} skeleton={<Skeleton className="h-40 w-full" />}>
        {(d) => (
          <>
            <ul className="divide-y divide-border text-sm" aria-label="Channels">
              {d.channels.map((c) => (
                <ChannelRow key={c.channel} c={c} busy={busy === c.channel} onTest={(name) => { setBusy(name); test.mutate(name, { onSettled: () => setBusy(null) }); }} />
              ))}
            </ul>
            <div className="mt-3 grid gap-2 text-xs text-faint">
              <p>
                <ShieldCheck className="mr-1 inline size-3.5 align-text-bottom" aria-hidden />
                Messages to ntfy, e-mail and Telegram are <strong>{d.external_detail}</strong>: {d.external_detail === "minimal" ? "only a count (\"2 new alerts, 1 high. Open the app.\"), no amount, name or merchant" : "the kind of alert and an amount rounded to 10 EUR, never a name, merchant, bank or IBAN"}. Minimum severity {d.min_severity}, at most {d.max_per_week} messages a week per channel{d.quiet_hours ? `, quiet hours ${d.quiet_hours}` : ""}.
              </p>
              <p>{d.how_to_enable}</p>
            </div>
          </>
        )}
      </Async>
      <Dialog open={!!shown} onClose={() => setShown(null)} title={shown ? `Test message: ${CHANNEL_LABEL[shown.channel as AlertChannel["channel"]] ?? shown.channel}` : ""} size="lg"
        footer={<Button onClick={() => setShown(null)}>Close</Button>}>
        {shown && (
          <div className="grid gap-3">
            <Notice tone="pos" title="Dry run: nothing was sent">{shown.sample}. This is exactly what a send would contain; secrets are masked.</Notice>
            {!shown.enabled && <Notice tone="info">This channel is disabled in config.toml, so a real test would not send either.</Notice>}
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
  const [open, setOpen] = useState(false);
  const q = useAlertDigest(open);
  return (
    <Card title="Weekly summary" subtitle="Built by the app from your data every week (nothing is written by a model) and put in Insights.">
      <Button size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}><FileText className="size-3.5" aria-hidden /> {open ? "Hide the preview" : "Preview this week's summary"}</Button>
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
  const [status, setStatus] = useState<StatusFilter>("open");
  const [kind, setKind] = useState("");
  const [severity, setSeverity] = useState("");
  const q = useAlerts({ status: status === "open" ? undefined : status, kind: kind || undefined, severity: severity || undefined });
  const [last, setLast] = useState<{ new: number; escalated: number; resolved: number } | null>(null);
  const check = useWrite(() => api.post<{ new: number; escalated: number; resolved: number }>("/alerts/check", {}), { onSuccess: (r) => setLast(r) });
  const ack = useWrite((id: string) => api.post(`/alerts/${id}/ack`, {}), { success: "Acknowledged" });
  const snooze = useWrite((id: string) => api.post(`/alerts/${id}/snooze`, { days: 7 }), { success: "Snoozed for 7 days" });
  const restore = useWrite((id: string) => api.post(`/alerts/${id}/restore`, {}), { success: "Restored" });
  const mute = useWrite((k: string) => api.post(`/alerts/kinds/${k}/mute`, {}), { success: "Kind muted" });
  const act = (what: "ack" | "snooze" | "restore" | "mute", e: AlertEvent) => {
    if (what === "ack") ack.mutate(e.id);
    else if (what === "snooze") snooze.mutate(e.id);
    else if (what === "restore") restore.mutate(e.id);
    else mute.mutate(e.kind);
  };
  return (
    <>
      <PageHeader
        title="Alerts"
        subtitle="Important events: a bank consent about to expire, a sync that keeps failing, an unusual charge, a price rise, a balance that may run short. Acknowledging, snoozing and muting only change what you see here and what gets sent."
        actions={<Button variant="primary" busy={check.isPending} onClick={() => check.mutate(undefined as never)}><Bell className="size-4" aria-hidden /> Check now</Button>}
      />
      <Async q={q} skeleton={<Skeleton className="h-80 w-full" />}>
        {(d) => {
          if (!d.ready) return <Notice tone="warn" title="Alerts are not set up yet">{d.message}</Notice>;
          const items = d.items.filter((e) => (status === "open" ? e.status === "new" || e.status === "sent" : true));
          return (
            <div className="grid gap-4">
              {last && <Notice tone="pos" title="Checked">{last.new} new, {last.escalated} escalated, {last.resolved} resolved. Nothing was sent outside the app.</Notice>}
              {d.enabled === false && <Notice tone="warn">Alerts are switched off in config.toml ([alerts] enabled = false): nothing is evaluated.</Notice>}
              <div className="flex flex-wrap items-center gap-3">
                <Segmented label="Status" value={status} onChange={setStatus} options={(Object.keys(STATUS_LABEL) as StatusFilter[]).map((s) => ({ value: s, label: s === "open" ? `Open ${d.counts.open}` : STATUS_LABEL[s] }))} />
                <label className="flex items-center gap-2 text-[13px] text-muted">Severity
                  <Select aria-label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value)} className="w-32">
                    <option value="">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
                  </Select>
                </label>
                <label className="flex items-center gap-2 text-[13px] text-muted">Kind
                  <Select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)} className="w-52">
                    <option value="">All</option>
                    {d.kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
                  </Select>
                </label>
              </div>
              {items.length === 0 ? (
                <Card><EmptyState icon={<Check className="size-6" />} title={status === "open" ? "No open alert" : `Nothing ${STATUS_LABEL[status].toLowerCase()}`}>{status === "open" ? "New alerts appear after each daily run, or press Check now." : "Nothing in this list."}</EmptyState></Card>
              ) : (
                <ul className="grid gap-3" aria-label="Alerts">{items.map((e) => <EventCard key={e.id} e={e} kinds={d.kinds} act={act} />)}</ul>
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
