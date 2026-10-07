import { useMemo, useState } from "react";
import { Link } from "react-router";
import { Plus, Trash2, UserRound } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, Disclosure, EmptyState, Field, Input, Notice, PageHeader, Select, Skeleton } from "@/components/ui";
import { CopyCommand } from "@/components/CopyCommand";
import { useDryRun, useGet, useHousehold, usePeople, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { useUser } from "@/lib/app";
import { fmtDateTime, groupLabel } from "@/lib/format";
import type { AttributionRule, AuditRows, EditResult, HouseholdAccount, HouseholdOverview } from "@/api/types";

export default function Household() {
  const q = useHousehold();
  return (
    <>
      <PageHeader title="Household" subtitle="Who is in the household, who owns each account, and how transactions are attributed to a person. Names stay on this machine." />
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
  return (
    <Card title="Members" subtitle="Declared in memory/household.yaml. A model only ever sees adult-1, kid-1 ... never a name or an alias.">
      {d.members.length === 0 ? (
        <EmptyState icon={<UserRound className="size-6" />} title="No member declared yet">
          <CopyCommand command="uv run coach memory member add --id anna --name 'Anna Rossi' --role adult" />
        </EmptyState>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th scope="col" className="py-2 pr-3 font-medium">Name</th>
                <th scope="col" className="px-2 py-2 font-medium">Role</th>
                <th scope="col" className="px-2 py-2 font-medium">Born</th>
                <th scope="col" className="px-2 py-2 font-medium">Accounts</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">Transactions</th>
              </tr>
            </thead>
            <tbody>
              {d.members.map((m) => (
                <tr key={m.id} className="border-t border-border">
                  <td className="py-2 pr-3 font-medium">
                    {m.name}
                    <span className="ml-2 text-xs font-normal text-faint">{m.id}</span>
                  </td>
                  <td className="px-2 py-2"><Badge tone={m.role === "child" ? "info" : "neutral"}>{m.role}</Badge></td>
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
        Add or change members with <code>coach memory member add</code> or on the <Link to="/memory" className="underline">Memory</Link> page. Children's pocket money is on{" "}
        <Link to="/kids" className="underline">Kids' money</Link>.
      </p>
    </Card>
  );
}

function Accounts({ d }: { d: HouseholdOverview }) {
  const save = useWrite((v: { uid: string; owner?: string; purpose?: string }) => api.patch(`/accounts/${v.uid}`, { owner: v.owner, purpose: v.purpose }), { success: "Account updated" });
  return (
    <Card title="Accounts: owner and purpose" subtitle="The owner is who the account belongs to (joint, or one member). It is the default for every transaction on it." pad={false}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Account</th>
              <th scope="col" className="px-2 py-2 font-medium">Owner</th>
              <th scope="col" className="px-2 py-2 font-medium">Used for</th>
              <th scope="col" className="px-4 py-2 font-medium">Attributed to</th>
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
  const people = usePeople();
  const current = a.joint ? "joint" : (a.owner_member ?? "");
  return (
    <tr className="border-t border-border align-top">
      <td className="px-4 py-2">
        <div className="font-medium">{a.label}</div>
        <div className="text-xs text-muted">{a.bank}</div>
      </td>
      <td className="px-2 py-2">
        <Select aria-label={`Owner of ${a.label}`} value={current} onChange={(e) => e.target.value && onSave({ owner: e.target.value })} className="!min-h-9">
          {!current && <option value="">{a.owner ? `${a.owner} (not a member)` : "No owner"}</option>}
          <option value="joint">Joint</option>
          {d.members.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </Select>
      </td>
      <td className="px-2 py-2">
        <Select aria-label={`Purpose of ${a.label}`} value={a.purpose ?? ""} onChange={(e) => e.target.value && onSave({ purpose: e.target.value })} className="!min-h-9">
          {!a.purpose && <option value="">Not set</option>}
          {d.purposes.map((p) => (
            <option key={p} value={p}>{groupLabel(p)}</option>
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

function describeRule(r: AttributionRule): string {
  const m = r.match;
  const bits = [
    m.account && `account ${m.account}`,
    m.card_last4 && `card ending ${m.card_last4}`,
    m.merchant_key && `merchant /${m.merchant_key}/`,
    m.description && `description /${m.description}/`,
    m.direction && `money ${m.direction}`,
    m.amount_min != null && `at least ${m.amount_min}`,
    m.amount_max != null && `at most ${m.amount_max}`,
  ];
  return bits.filter(Boolean).join(", ");
}

function Rules({ d }: { d: HouseholdOverview }) {
  const people = usePeople();
  const [open, setOpen] = useState(false);
  const del = useWrite((id: string) => api.post<EditResult>(`/household/attribution/rules/${id}/delete`, {}), { success: "Rule removed" });
  return (
    <Card
      title="Attribution rules"
      subtitle="Who a transaction belongs to: a manual reassignment, then the first matching rule, then the account's owner."
      action={
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-3.5" aria-hidden /> Rule
        </Button>
      }
    >
      {d.rules.length === 0 ? (
        <p className="text-[13px] text-muted">No rule: the owner of the account decides. Typical rules: a prepaid card account per child, or a card's last four digits on a shared account.</p>
      ) : (
        <ul className="divide-y divide-border">
          {d.rules.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <span className="min-w-0">
                <b>{people.name(r.member)}</b> when {describeRule(r)}
                {r.note && <span className="ml-2 text-xs text-faint">{r.note}</span>}
              </span>
              <Button size="sm" variant="ghost" aria-label={`Remove rule ${r.id}`} onClick={() => confirm(`Remove the rule "${r.id}"? It is recorded in the memory history.`) && del.mutate(r.id)}>
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">{d.attribution.manual} transaction(s) reassigned by hand (open a transaction to change or undo one).</p>
      {open && <RuleDialog d={d} onClose={() => setOpen(false)} />}
    </Card>
  );
}

function RuleDialog({ d, onClose }: { d: HouseholdOverview; onClose: () => void }) {
  const [id, setId] = useState("");
  const [member, setMember] = useState(d.members.find((m) => m.role === "child")?.id ?? d.members[0]?.id ?? "");
  const [account, setAccount] = useState("");
  const [card, setCard] = useState("");
  const [merchant, setMerchant] = useState("");
  const [desc, setDesc] = useState("");
  const body = useMemo(() => ({ member, match: { account, card_last4: card, merchant_key: merchant, description: desc } }), [member, account, card, merchant, desc]);
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id) && !!member && !!(account || card || merchant || desc);
  const pv = useDryRun<EditResult & { matches: number; would_change: number }>(`/household/attribution/rules/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/attribution/rules/${id}`, body), { success: "Rule saved", onSuccess: onClose });
  return (
    <Dialog
      open
      onClose={onClose}
      title="New attribution rule"
      size="lg"
      description="Every condition you fill must match (AND). The preview shows how many transactions it would attribute."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save rule</Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Rule id" hint="lowercase letters, digits, - or _">{(fid) => <Input id={fid} value={id} onChange={(e) => setId(e.target.value)} placeholder="mia-card" />}</Field>
        <Field label="Attribute to">
          {(fid) => (
            <Select id={fid} value={member} onChange={(e) => setMember(e.target.value)}>
              {d.members.map((m) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
              <option value="joint">Joint</option>
            </Select>
          )}
        </Field>
        <Field label="Account (uid or label)">
          {(fid) => (
            <Select id={fid} value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">Any account</option>
              {d.accounts.map((a) => (
                <option key={a.uid} value={a.uid}>{a.label}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Card's last four digits" hint="only when the bank prints them in the description">
          {(fid) => <Input id={fid} value={card} onChange={(e) => setCard(e.target.value)} inputMode="numeric" maxLength={4} placeholder="4242" />}
        </Field>
        <Field label="Merchant matches (regex)">{(fid) => <Input id={fid} value={merchant} onChange={(e) => setMerchant(e.target.value)} placeholder="^SKATE SHOP" />}</Field>
        <Field label="Description matches (regex)">{(fid) => <Input id={fid} value={desc} onChange={(e) => setDesc(e.target.value)} />}</Field>
      </div>
      <div className="mt-4">
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        {pv.data && (
          <>
            <p className="mb-2 text-sm">
              It matches <b>{pv.data.matches}</b> transaction(s); <b>{pv.data.would_change}</b> would change person.
            </p>
            <DiffView diff={pv.data.diff} />
          </>
        )}
      </div>
    </Dialog>
  );
}

function Logins({ d }: { d: HouseholdOverview }) {
  const people = usePeople();
  return (
    <Card title="Logins" subtitle="Each person can have their own login: an adult sees everything, a child sees only their own money. Logins are created in a terminal, never here.">
      {d.users.length === 0 ? (
        <p className="mb-3 text-[13px] text-muted">No login yet: the app opens with the owner's login (everything).</p>
      ) : (
        <ul className="mb-3 divide-y divide-border">
          {d.users.map((u) => (
            <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <span>
                <b>{u.id}</b>
                {u.member_id && <span className="text-muted"> · {people.name(u.member_id)}</span>}
              </span>
              <span className="flex gap-1.5">
                <Badge tone={u.role === "child" ? "info" : "neutral"}>{u.role}</Badge>
                {u.disabled && <Badge tone="neg">disabled</Badge>}
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
  const user = useUser();
  const [theme, setTheme] = useState(user?.prefs.theme ?? "");
  const [locale, setLocale] = useState(user?.prefs.locale ?? "");
  const [landing, setLanding] = useState(user?.prefs.landing ?? "");
  const save = useWrite(() => api.put("/me/preferences", { prefs: Object.fromEntries(Object.entries({ theme, locale, landing }).filter(([, v]) => v)) }), {
    success: "Preferences saved for this login",
  });
  if (!user || user.id === "owner") return null;
  return (
    <Card title="My preferences" subtitle={`Stored for the login ${user.id}.`}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Theme">
          {(id) => (
            <Select id={id} value={theme} onChange={(e) => setTheme(e.target.value)}>
              <option value="">Unchanged</option>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </Select>
          )}
        </Field>
        <Field label="Dates and numbers">
          {(id) => (
            <Select id={id} value={locale} onChange={(e) => setLocale(e.target.value)}>
              <option value="">Unchanged</option>
              <option value="fr-FR">Français</option>
              <option value="en-GB">English</option>
              <option value="it-IT">Italiano</option>
            </Select>
          )}
        </Field>
        <Field label="Opens on">
          {(id) => (
            <Select id={id} value={landing} onChange={(e) => setLanding(e.target.value)}>
              <option value="">Dashboard</option>
              <option value="transactions">Transactions</option>
              <option value="kids">Kids' money</option>
            </Select>
          )}
        </Field>
      </div>
      <div className="mt-3 flex justify-end">
        <Button variant="primary" onClick={() => save.mutate(undefined as never)} busy={save.isPending}>Save</Button>
      </div>
    </Card>
  );
}

function Audit() {
  const [open, setOpen] = useState(false);
  const q = useGet<AuditRows>("/household/audit", { limit: 30 }, { enabled: open });
  return (
    <Card title="Who changed what" subtitle="The changes made in the web app (method, endpoint, who: never a payload) and the memory history with the source of each change.">
      {!open ? (
        <Button size="sm" onClick={() => setOpen(true)}>Show the audit</Button>
      ) : q.data ? (
        <div className="grid gap-4 text-[13px]">
          <Disclosure summary="Changes made in the web app" defaultOpen>
            <ul className="grid gap-0.5 text-muted">
              {q.data.requests.map((r, i) => (
                <li key={i}>
                  {fmtDateTime(r.at)} · {r.actor} · {r.method} {r.path.replace("/api/v1", "")} · {r.status}
                </li>
              ))}
              {q.data.requests.length === 0 && <li>Nothing yet.</li>}
            </ul>
          </Disclosure>
          <Disclosure summary="Memory changes and their source" defaultOpen>
            <ul className="grid gap-0.5 text-muted">
              {q.data.memory.map((m) => (
                <li key={m.id}>
                  {fmtDateTime(m.date)} · {m.source} · {m.subject}
                </li>
              ))}
              {q.data.memory.length === 0 && <li>No history.</li>}
            </ul>
          </Disclosure>
        </div>
      ) : (
        <Skeleton className="h-24 w-full" />
      )}
    </Card>
  );
}
