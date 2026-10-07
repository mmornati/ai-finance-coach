import { useMemo, useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Plus, Trash2, UserRound } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, Disclosure, EmptyState, Field, Input, Notice, PageHeader, Select, Skeleton } from "@/components/ui";
import { CopyCommand } from "@/components/CopyCommand";
import { useDryRun, useGet, useHousehold, usePeople, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { useUser } from "@/lib/app";
import { fmtDateTime } from "@/lib/format";
import { purposeLabel } from "@/i18n/server";
import i18n from "@/i18n";
import type { AttributionRule, AuditRows, EditResult, HouseholdAccount, HouseholdOverview } from "@/api/types";

const ROLE: Record<string, ParseKeys<"household">> = { adult: "household.role.adult", child: "household.role.child" };

export default function Household() {
  const { t } = useTranslation("household");
  const q = useHousehold();
  return (
    <>
      <PageHeader title={t("household.header.title")} subtitle={t("household.header.subtitle")} />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => (
          <div className="grid gap-4">
            {d.warnings.map((w) => (
              <Notice key={w} tone="warn">{w}</Notice>
            ))}
            <Members d={d} />
            <Accounts d={d} />
            <Rules d={d} />
            <Logins d={d} />
            <Preferences />
            <Audit />
          </div>
        )}
      </Async>
    </>
  );
}

function Members({ d }: { d: HouseholdOverview }) {
  const { t } = useTranslation("household");
  return (
    <Card title={t("household.members.title")} subtitle={t("household.members.subtitle")}>
      {d.members.length === 0 ? (
        <EmptyState icon={<UserRound className="size-6" />} title={t("household.members.empty")}>
          <CopyCommand command="uv run coach memory member add --id anna --name 'Anna Rossi' --role adult" />
        </EmptyState>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th scope="col" className="py-2 pr-3 font-medium">{t("household.members.col.name")}</th>
                <th scope="col" className="px-2 py-2 font-medium">{t("household.members.col.role")}</th>
                <th scope="col" className="px-2 py-2 font-medium">{t("household.members.col.born")}</th>
                <th scope="col" className="px-2 py-2 font-medium">{t("household.members.col.accounts")}</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">{t("household.members.col.transactions")}</th>
              </tr>
            </thead>
            <tbody>
              {d.members.map((m) => (
                <tr key={m.id} className="border-t border-border">
                  <td className="py-2 pr-3 font-medium">
                    {m.name}
                    <span className="ml-2 text-xs font-normal text-faint">{m.id}</span>
                  </td>
                  <td className="px-2 py-2"><Badge tone={m.role === "child" ? "info" : "neutral"}>{ROLE[m.role] ? t(ROLE[m.role]) : m.role}</Badge></td>
                  <td className="num px-2 py-2 text-muted">{m.birth_year ?? "-"}</td>
                  <td className="px-2 py-2 text-muted">{m.accounts.length}</td>
                  <td className="num px-2 py-2 text-right">{m.attributed_transactions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-xs text-muted">
        <Trans
          t={t}
          i18nKey="household.members.footer"
          components={{ code: <code />, memory: <Link to="/memory" className="underline" />, kids: <Link to="/kids" className="underline" /> }}
        />
      </p>
    </Card>
  );
}

function Accounts({ d }: { d: HouseholdOverview }) {
  const { t } = useTranslation("household");
  const save = useWrite((v: { uid: string; owner?: string; purpose?: string }) => api.patch(`/accounts/${v.uid}`, { owner: v.owner, purpose: v.purpose }), {
    success: t("household.accounts.saved"),
  });
  return (
    <Card title={t("household.accounts.title")} subtitle={t("household.accounts.subtitle")} pad={false}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">{t("household.accounts.col.account")}</th>
              <th scope="col" className="px-2 py-2 font-medium">{t("household.accounts.col.owner")}</th>
              <th scope="col" className="px-2 py-2 font-medium">{t("household.accounts.col.purpose")}</th>
              <th scope="col" className="px-4 py-2 font-medium">{t("household.accounts.col.attributed")}</th>
            </tr>
          </thead>
          <tbody>
            {d.accounts.map((a) => (
              <AccountRow key={a.uid} a={a} d={d} onSave={(v) => save.mutate({ uid: a.uid, ...v })} />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function AccountRow({ a, d, onSave }: { a: HouseholdAccount; d: HouseholdOverview; onSave: (v: { owner?: string; purpose?: string }) => void }) {
  const { t } = useTranslation("household");
  const people = usePeople();
  const current = a.joint ? "joint" : (a.owner_member ?? "");
  return (
    <tr className="border-t border-border align-top">
      <td className="px-4 py-2">
        <div className="font-medium">{a.label}</div>
        <div className="text-xs text-muted">{a.bank}</div>
      </td>
      <td className="px-2 py-2">
        <Select aria-label={t("household.accounts.ownerOf", { account: a.label })} value={current} onChange={(e) => e.target.value && onSave({ owner: e.target.value })} className="!min-h-9">
          {!current && <option value="">{a.owner ? t("household.accounts.notMember", { owner: a.owner }) : t("household.accounts.noOwner")}</option>}
          <option value="joint">{t("household.accounts.joint")}</option>
          {d.members.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </Select>
      </td>
      <td className="px-2 py-2">
        <Select aria-label={t("household.accounts.purposeOf", { account: a.label })} value={a.purpose ?? ""} onChange={(e) => e.target.value && onSave({ purpose: e.target.value })} className="!min-h-9">
          {!a.purpose && <option value="">{t("household.accounts.notSet")}</option>}
          {d.purposes.map((p) => (
            <option key={p} value={p}>{purposeLabel(p)}</option>
          ))}
        </Select>
      </td>
      <td className="px-4 py-2 text-xs text-muted">
        {Object.entries(a.attributed)
          .map(([k, n]) => `${people.first(k === "unassigned" ? null : k)} ${n}`)
          .join(" · ") || "-"}
      </td>
    </tr>
  );
}

/** The conditions of a rule in the current language (called at render time, never at import time). */
function describeRule(r: AttributionRule): string {
  const m = r.match;
  const tt = (key: ParseKeys<"household">, value: unknown) => i18n.t(key, { ns: "household", value: String(value) });
  const direction =
    m.direction === "in"
      ? i18n.t("household.rules.match.moneyIn", { ns: "household" })
      : m.direction === "out"
        ? i18n.t("household.rules.match.moneyOut", { ns: "household" })
        : m.direction && tt("household.rules.match.direction", m.direction);
  const bits = [
    m.account && tt("household.rules.match.account", m.account),
    m.card_last4 && tt("household.rules.match.card", m.card_last4),
    m.merchant_key && tt("household.rules.match.merchant", m.merchant_key),
    m.description && tt("household.rules.match.description", m.description),
    direction,
    m.amount_min != null && tt("household.rules.match.atLeast", m.amount_min),
    m.amount_max != null && tt("household.rules.match.atMost", m.amount_max),
  ];
  return bits.filter(Boolean).join(", ");
}

function Rules({ d }: { d: HouseholdOverview }) {
  const { t } = useTranslation("household");
  const people = usePeople();
  const [open, setOpen] = useState(false);
  const del = useWrite((id: string) => api.post<EditResult>(`/household/attribution/rules/${id}/delete`, {}), { success: t("household.rules.removed") });
  return (
    <Card
      title={t("household.rules.title")}
      subtitle={t("household.rules.subtitle")}
      action={
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-3.5" aria-hidden /> {t("household.rules.add")}
        </Button>
      }
    >
      {d.rules.length === 0 ? (
        <p className="text-[13px] text-muted">{t("household.rules.empty")}</p>
      ) : (
        <ul className="divide-y divide-border">
          {d.rules.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0">
                <Trans t={t} i18nKey="household.rules.item" values={{ name: people.name(r.member), conditions: describeRule(r) }} components={{ b: <b /> }} />
                {r.note && <span className="ml-2 text-xs text-faint">{r.note}</span>}
              </span>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("household.rules.remove", { id: r.id })}
                onClick={() => confirm(t("household.rules.confirmRemove", { id: r.id })) && del.mutate(r.id)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">{t("household.rules.manual", { count: d.attribution.manual })}</p>
      {open && <RuleDialog d={d} onClose={() => setOpen(false)} />}
    </Card>
  );
}

function RuleDialog({ d, onClose }: { d: HouseholdOverview; onClose: () => void }) {
  const { t } = useTranslation("household");
  const [id, setId] = useState("");
  const [member, setMember] = useState(d.members.find((m) => m.role === "child")?.id ?? d.members[0]?.id ?? "");
  const [account, setAccount] = useState("");
  const [card, setCard] = useState("");
  const [merchant, setMerchant] = useState("");
  const [desc, setDesc] = useState("");
  const body = useMemo(() => ({ member, match: { account, card_last4: card, merchant_key: merchant, description: desc } }), [member, account, card, merchant, desc]);
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id) && !!member && !!(account || card || merchant || desc);
  const pv = useDryRun<EditResult & { matches: number; would_change: number }>(`/household/attribution/rules/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/attribution/rules/${id}`, body), { success: t("household.ruleDialog.saved"), onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title={t("household.ruleDialog.title")}
      size="lg"
      description={t("household.ruleDialog.description")}
      footer={
        <>
          <Button onClick={onClose}>{t("household.ruleDialog.cancel")}</Button>
          <Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>
            {t("household.ruleDialog.save")}
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("household.ruleDialog.id")} hint={t("household.ruleDialog.idHint")}>
          {(fid) => <Input id={fid} value={id} onChange={(e) => setId(e.target.value)} placeholder="mia-card" />}
        </Field>
        <Field label={t("household.ruleDialog.member")}>
          {(fid) => (
            <Select id={fid} value={member} onChange={(e) => setMember(e.target.value)}>
              {d.members.map((m) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
              <option value="joint">{t("household.ruleDialog.joint")}</option>
            </Select>
          )}
        </Field>
        <Field label={t("household.ruleDialog.account")}>
          {(fid) => (
            <Select id={fid} value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">{t("household.ruleDialog.anyAccount")}</option>
              {d.accounts.map((a) => (
                <option key={a.uid} value={a.uid}>{a.label}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label={t("household.ruleDialog.card")} hint={t("household.ruleDialog.cardHint")}>
          {(fid) => <Input id={fid} value={card} onChange={(e) => setCard(e.target.value)} inputMode="numeric" maxLength={4} placeholder="4242" />}
        </Field>
        <Field label={t("household.ruleDialog.merchantMatch")}>{(fid) => <Input id={fid} value={merchant} onChange={(e) => setMerchant(e.target.value)} placeholder="^SKATE SHOP" />}</Field>
        <Field label={t("household.ruleDialog.descriptionMatch")}>{(fid) => <Input id={fid} value={desc} onChange={(e) => setDesc(e.target.value)} />}</Field>
      </div>
      <div className="mt-4">
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && (
          <>
            <p className="mb-2 text-sm">
              <Trans t={t} i18nKey="household.ruleDialog.preview" count={pv.data.matches} values={{ change: pv.data.would_change }} components={{ b: <b /> }} />
            </p>
            <DiffView diff={pv.data.diff} />
          </>
        )}
      </div>
    </Dialog>
  );
}

function Logins({ d }: { d: HouseholdOverview }) {
  const { t } = useTranslation("household");
  const people = usePeople();
  return (
    <Card title={t("household.logins.title")} subtitle={t("household.logins.subtitle")}>
      {d.users.length === 0 ? (
        <p className="mb-3 text-[13px] text-muted">{t("household.logins.empty")}</p>
      ) : (
        <ul className="mb-3 divide-y divide-border">
          {d.users.map((u) => (
            <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <span>
                <b>{u.id}</b>
                {u.member_id && <span className="text-muted"> · {people.name(u.member_id)}</span>}
              </span>
              <span className="flex gap-1.5">
                <Badge tone={u.role === "child" ? "info" : "neutral"}>{ROLE[u.role] ? t(ROLE[u.role]) : u.role}</Badge>
                {u.disabled && <Badge tone="neg">{t("household.logins.disabled")}</Badge>}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-2">
        <CopyCommand command="uv run coach users add NAME --role child --member mia" />
        <CopyCommand command="uv run coach ui --login-link --user NAME" />
      </div>
    </Card>
  );
}

function Preferences() {
  const { t } = useTranslation("household");
  const { t: tc } = useTranslation();
  const user = useUser();
  const [theme, setTheme] = useState(user?.prefs.theme ?? "");
  const [locale, setLocale] = useState(user?.prefs.locale ?? "");
  const [landing, setLanding] = useState(user?.prefs.landing ?? "");
  const save = useWrite(() => api.put("/me/preferences", { prefs: Object.fromEntries(Object.entries({ theme, locale, landing }).filter(([, v]) => v)) }), {
    success: t("household.prefs.saved"),
  });
  if (!user || user.id === "owner") return null;
  return (
    <Card title={t("household.prefs.title")} subtitle={t("household.prefs.subtitle", { user: user.id })}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={t("household.prefs.theme")}>
          {(id) => (
            <Select id={id} value={theme} onChange={(e) => setTheme(e.target.value)}>
              <option value="">{t("household.prefs.unchanged")}</option>
              <option value="system">{t("household.prefs.system")}</option>
              <option value="light">{t("household.prefs.light")}</option>
              <option value="dark">{t("household.prefs.dark")}</option>
            </Select>
          )}
        </Field>
        <Field label={t("household.prefs.locale")}>
          {(id) => (
            <Select id={id} value={locale} onChange={(e) => setLocale(e.target.value)}>
              <option value="">{t("household.prefs.unchanged")}</option>
              <option value="fr-FR">Français</option>
              <option value="en-GB">English</option>
              <option value="it-IT">Italiano</option>
            </Select>
          )}
        </Field>
        <Field label={t("household.prefs.landing")}>
          {(id) => (
            <Select id={id} value={landing} onChange={(e) => setLanding(e.target.value)}>
              <option value="">{tc("nav.dashboard")}</option>
              <option value="transactions">{tc("nav.transactions")}</option>
              <option value="kids">{tc("nav.kids")}</option>
            </Select>
          )}
        </Field>
      </div>
      <div className="mt-3 flex justify-end">
        <Button variant="primary" onClick={() => save.mutate(undefined as never)} busy={save.isPending}>
          {t("household.prefs.save")}
        </Button>
      </div>
    </Card>
  );
}

function Audit() {
  const { t } = useTranslation("household");
  const [open, setOpen] = useState(false);
  const q = useGet<AuditRows>("/household/audit", { limit: 30 }, { enabled: open });
  return (
    <Card title={t("household.audit.title")} subtitle={t("household.audit.subtitle")}>
      {!open ? (
        <Button size="sm" onClick={() => setOpen(true)}>
          {t("household.audit.show")}
        </Button>
      ) : q.data ? (
        <div className="grid gap-4 text-[13px]">
          <Disclosure summary={t("household.audit.requests")} defaultOpen>
            <ul className="grid gap-0.5 text-muted">
              {q.data.requests.map((r, i) => (
                <li key={i}>
                  {fmtDateTime(r.at)} · {r.actor} · {r.method} {r.path.replace("/api/v1", "")} · {r.status}
                </li>
              ))}
              {q.data.requests.length === 0 && <li>{t("household.audit.requestsEmpty")}</li>}
            </ul>
          </Disclosure>
          <Disclosure summary={t("household.audit.memory")} defaultOpen>
            <ul className="grid gap-0.5 text-muted">
              {q.data.memory.map((m) => (
                <li key={m.id}>
                  {fmtDateTime(m.date)} · {m.source} · {m.subject}
                </li>
              ))}
              {q.data.memory.length === 0 && <li>{t("household.audit.memoryEmpty")}</li>}
            </ul>
          </Disclosure>
        </div>
      ) : (
        <Skeleton className="h-24 w-full" />
      )}
    </Card>
  );
}
