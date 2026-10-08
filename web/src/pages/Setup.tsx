import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys, TFunction } from "i18next";
import { Link } from "react-router";
import { Check, Circle, CircleDot, Sparkles } from "lucide-react";
import { Async, Badge, Button, Card, DiffView, Field, Input, Notice, PageHeader, ProgressBar, Select, Skeleton, Spinner } from "@/components/ui";
import { CopyCommand } from "@/components/CopyCommand";
import { useDryRun, useGet, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fieldLabel, holdingKindLabel, serverLabel, tServer, useServerText } from "@/i18n/server";
import type { EditResult, Onboarding, OnboardingStep, ServerMsg, WizardStatus } from "@/api/types";

const LINKS: Record<OnboardingStep["id"], { to: string; label: ParseKeys<"setup"> }> = {
  household: { to: "/memory?tab=household", label: "links.household" },
  accounts: { to: "/connections", label: "links.accounts" },
  loans: { to: "/memory?tab=items", label: "links.loans" },
  contracts: { to: "/memory?tab=items", label: "links.contracts" },
  preferences: { to: "/memory", label: "links.preferences" },
  budgets: { to: "/budgets", label: "links.budgets" },
  questions: { to: "/memory?tab=questions", label: "links.questions" },
};
const ICON = { done: <Check className="size-4 text-pos" aria-hidden />, partial: <CircleDot className="size-4 text-warn" aria-hidden />, todo: <Circle className="size-4 text-faint" aria-hidden /> };
const TONE = { done: "pos", partial: "warn", todo: "neutral" } as const;

/** The names of memory fields (`rate.nominal`, `owner`) in the interface language. */
const fields = (names: string[]) => names.map(fieldLabel).join(", ");

function missingLines(s: OnboardingStep, t: TFunction<"setup">): string[] {
  const out: string[] = [];
  (s.missing ?? []).forEach((m, i) => out.push(typeof m === "string" ? tServer(s.missing_msg?.[i], m) : t("missing.account", { account: m.label ?? m.account, fields: fields(m.missing) })));
  for (const l of s.liabilities ?? []) if (l.missing.length) out.push(t("missing.liability", { id: l.id, kind: holdingKindLabel(l.kind), fields: fields(l.missing) }));
  if (s.loan_payments_without_file?.length) out.push(t("missing.loanPayments", { count: s.loan_payments_without_file.length }));
  if (s.recurring_without_contract?.length) out.push(t("missing.recurring", { count: s.recurring_without_contract.length }));
  for (const c of s.contracts_with_empty_fields ?? []) out.push(t("missing.contract", { id: c.id, fields: fields(c.missing) }));
  return out;
}

/** A wizard detail: "finish '<step>' first" names the step by its label in the interface language (`need` is its id). */
function wizardDetail(msg: ServerMsg | null | undefined, detail: string): string {
  if (msg?.code === "wizard.finishFirst" && msg.params?.need) {
    return tServer({ ...msg, params: { ...msg.params, step: serverLabel("setupStep", String(msg.params.need), String(msg.params.step ?? "")) } }, detail);
  }
  return tServer(msg, detail);
}

const WIZARD_TONE = { done: "pos", partial: "warn", todo: "neutral", skipped: "neutral", blocked: "neutral" } as const;

/** E13-2: where the first-run wizard stands. Read-only on purpose: its steps (Enable Banking, a bank link, a sync, a model run) ask for a typed
 *  consent in a terminal before anything leaves this machine, so the page shows the state and the command, and starts nothing. */
function FirstRun() {
  const { t } = useTranslation("setup");
  useServerText();
  const q = useGet<WizardStatus>("/setup/wizard");
  const d = q.data;
  if (!d || !Array.isArray(d.steps) || d.progress.next_step === null) return null;
  const next = d.steps.find((s) => s.id === d.progress.next_step);
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[15px] font-semibold">{t("firstRun.title")}</h2>
        <div className="min-w-48 flex-1"><ProgressBar label={t("firstRun.progress")} value={d.progress.done} max={d.progress.total} tone="info" /></div>
        <div className="text-sm text-muted" data-testid="wizard-progress">{t("firstRun.stepsDone", { done: d.progress.done, total: d.progress.total })}</div>
      </div>
      <ul className="mt-2 grid gap-1 text-[13px]" aria-label={t("firstRun.steps")}>
        {d.steps.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-2">
            <Badge tone={WIZARD_TONE[s.status]}>{t(`status.${s.status}`)}</Badge>
            <span>{serverLabel("setupStep", s.id, s.title)}{s.optional ? t("firstRun.optional") : ""}</span>
            {s.detail && <span className="text-muted">{wizardDetail(s.detail_msg, s.detail)}</span>}
          </li>
        ))}
      </ul>
      {next && (
        <div className="mt-3">
          <div className="mb-1 text-xs text-muted">{t("firstRun.next")}</div>
          <CopyCommand command={d.command} />
          {d.container && <div className="mt-2 text-xs text-muted">{t("firstRun.docker")}</div>}
          {next.id === "enablebanking" && (
            <div className="mt-2">
              <div className="mb-1 text-xs text-muted">{d.container ? t("firstRun.enableBankingDocker") : t("firstRun.enableBanking")}</div>
              <CopyCommand command={d.container ? d.commands.enablebanking : "uv run coach setup enablebanking"} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Setup() {
  const { t } = useTranslation("setup");
  const q = useGet<Onboarding>("/onboarding");
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="mb-4"><FirstRun /></div>
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            <Card>
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-48 flex-1"><ProgressBar label={t("progress")} value={d.progress.done} max={d.progress.total} tone="info" /></div>
                <div className="text-sm text-muted" data-testid="setup-progress">{t("stepsDone", { done: d.progress.done, total: d.progress.total })}</div>
                <Link to="/coach" className="inline-flex items-center gap-1 text-sm text-accent hover:underline"><Sparkles className="size-3.5" aria-hidden /> {t("askCoach")}</Link>
              </div>
              <p className="mt-2 text-xs text-faint"><Trans t={t} i18nKey="proposalsHint" components={{ code: <code /> }} /></p>
            </Card>
            {d.steps.map((s) => <StepCard key={s.id} s={s} cmd={d.next_actions.filter((a) => a.step === s.id)} declared={d.declared} />)}
          </div>
        )}
      </Async>
    </>
  );
}

function StepCard({ s, cmd, declared }: { s: OnboardingStep; cmd: Onboarding["next_actions"]; declared?: Record<ListKey, string[]> }) {
  const { t } = useTranslation("setup");
  useServerText();
  const lines = missingLines(s, t);
  const link = LINKS[s.id];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        {ICON[s.status]}
        <h2 className="text-[15px] font-semibold">{serverLabel("onboardingStep", s.id, s.heading)}</h2>
        <Badge tone={TONE[s.status]}>{t(`status.${s.status}`)}</Badge>
        <Link to={link.to} className="ml-auto text-sm text-accent hover:underline">{t(link.label)}</Link>
      </div>
      {lines.length > 0 && <ul className="mt-2 list-disc pl-5 text-[13px] text-muted">{lines.slice(0, 8).map((l, i) => <li key={i}>{l}</li>)}{lines.length > 8 && <li>{t("missing.more", { count: lines.length - 8 })}</li>}</ul>}
      {s.id === "household" && s.status !== "done" && <HouseholdForm have={s.have} declared={declared} />}
      {s.id === "preferences" && s.status !== "done" && <PreferencesForm />}
      {s.status !== "done" && cmd.length > 0 && s.id !== "household" && s.id !== "preferences" && (
        <div className="mt-3 grid gap-1.5">{cmd.slice(0, 1).map((c) => <div key={c.command}><div className="mb-1 text-xs text-muted">{tServer(c.do_msg, c.do)}</div><CopyCommand command={c.command} /></div>)}</div>
      )}
    </Card>
  );
}

const split = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

function Preview({ pv, onWrite, busy, blocked }: { pv: { data: EditResult | null; error: string | null; loading: boolean }; onWrite: () => void; busy: boolean; blocked?: boolean }) {
  const { t } = useTranslation("setup");
  return (
    <div className="mt-3 grid gap-2">
      {pv.loading ? <Spinner /> : pv.error ? <Notice tone="neg">{pv.error}</Notice> : pv.data && <DiffView diff={pv.data.diff} empty={t("preview.nothing")} />}
      <div className="flex justify-end"><Button variant="primary" busy={busy} disabled={!pv.data || !pv.data.changed || blocked} onClick={onWrite}>{t("preview.write")}</Button></div>
    </div>
  );
}

const LIST_KEYS = ["employers", "places", "schools"] as const;
type ListKey = (typeof LIST_KEYS)[number];

function HouseholdForm({ have, declared }: { have: Record<string, unknown>; declared?: Record<ListKey, string[]> }) {
  const { t } = useTranslation("setup");
  const [country, setCountry] = useState("");
  const [adds, setAdds] = useState<Record<ListKey, string>>({ employers: "", places: "", schools: "" });
  const [drops, setDrops] = useState<Record<ListKey, string[]>>({ employers: [], places: [], schools: [] });
  const [confirm, setConfirm] = useState(false);
  const keys = LIST_KEYS;
  const body: Record<string, unknown> = { ...(country ? { country } : {}) };
  for (const k of keys) {
    const add = split(adds[k]);
    if (add.length || drops[k].length) body[k] = { add, remove: drops[k] };
  }
  const removing = keys.some((k) => drops[k].length > 0);
  if (removing && confirm) body.confirm_removal = true;
  const filled = Object.keys(body).filter((k) => k !== "confirm_removal").length > 0;
  const pv = useDryRun<EditResult & { warning?: string }>("/onboarding/household", body, filled, "put");
  const reset = () => { setCountry(""); setAdds({ employers: "", places: "", schools: "" }); setDrops({ employers: [], places: [], schools: [] }); setConfirm(false); };
  const write = useWrite(() => api.put<EditResult>("/onboarding/household", body), { success: t("household.updated"), onSuccess: reset });
  const toggle = (k: ListKey, v: string) => setDrops({ ...drops, [k]: drops[k].includes(v) ? drops[k].filter((x) => x !== v) : [...drops[k], v] });
  return (
    <div className="mt-3 grid gap-3 rounded-lg border border-border p-3">
      <p className="text-xs text-muted"><Trans t={t} i18nKey="household.intro" values={{ employers: String(have.employers ?? 0), places: String(have.places ?? 0), schools: String(have.schools ?? 0) }} components={{ b: <b /> }} /></p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("household.country")}>{(id) => <Select id={id} value={country} onChange={(e) => setCountry(e.target.value)}><option value="">{t("household.notChanged")}</option><option value="FR">{t("household.france")}</option><option value="IT">{t("household.italy")}</option></Select>}</Field>
        {keys.map((k) => (
          <div key={k} className="grid gap-1.5">
            <Field label={t(`household.add.${k}`)} hint={t("household.commaSeparated")}>{(id) => <Input id={id} value={adds[k]} onChange={(e) => setAdds({ ...adds, [k]: e.target.value })} />}</Field>
            {(declared?.[k] ?? []).length > 0 && (
              <ul className="flex flex-wrap gap-1.5" aria-label={t(`household.declared.${k}`)}>
                {declared![k].map((v) => (
                  <li key={v}><button type="button" aria-pressed={drops[k].includes(v)} aria-label={drops[k].includes(v) ? t("household.keep", { value: v }) : t("household.remove", { value: v })} onClick={() => toggle(k, v)}
                    className={`rounded-full border px-2 py-0.5 text-xs ${drops[k].includes(v) ? "border-neg text-neg line-through" : "border-border-strong"}`}>{v} {drops[k].includes(v) ? "↺" : "×"}</button></li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      {removing && (
        <Notice tone="warn" title={t("household.removingTitle")}>
          {t("household.removingBody")} <label className="mt-1 flex items-center gap-2"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> {t("household.understand")}</label>
        </Notice>
      )}
      {filled && <Preview pv={pv} busy={write.isPending} blocked={removing && !confirm} onWrite={() => write.mutate(undefined as never)} />}
    </div>
  );
}

function PreferencesForm() {
  const { t } = useTranslation("setup");
  const [f, setF] = useState({ language: "", tone: "", goals: "", avoid: "" });
  const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim()));
  const filled = Object.keys(body).length > 0;
  const pv = useDryRun<EditResult>("/onboarding/preferences", body, filled);
  const write = useWrite(() => api.post<EditResult>("/onboarding/preferences", body), { success: t("preferences.saved"), onSuccess: () => setF({ language: "", tone: "", goals: "", avoid: "" }) });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="mt-3 grid gap-3 rounded-lg border border-border p-3">
      <p className="text-xs text-muted"><Trans t={t} i18nKey="preferences.intro" components={{ code: <code /> }} /></p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("preferences.language")}>{(id) => <Input id={id} value={f.language} onChange={set("language")} placeholder={t("preferences.languagePlaceholder")} />}</Field>
        <Field label={t("preferences.tone")}>{(id) => <Input id={id} value={f.tone} onChange={set("tone")} placeholder={t("preferences.tonePlaceholder")} />}</Field>
        <Field label={t("preferences.goals")}>{(id) => <Input id={id} value={f.goals} onChange={set("goals")} />}</Field>
        <Field label={t("preferences.avoid")}>{(id) => <Input id={id} value={f.avoid} onChange={set("avoid")} />}</Field>
      </div>
      {filled && <Preview pv={pv} busy={write.isPending} onWrite={() => write.mutate(undefined as never)} />}
    </div>
  );
}
