import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { Plus, Scale, Trash2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, Segmented, Select, Skeleton } from "@/components/ui";
import { useAllocation, useDryRun, useHousehold, usePeople, useTaxonomy, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtMoney, fmtPct, groupLabel } from "@/lib/format";
import { useServerText } from "@/i18n/server";
import type { AllocationRuleResult, EditResult } from "@/api/types";

const METHOD: Record<string, ParseKeys<"household">> = { equal: "whoPays.method.equal", income: "whoPays.method.income", custom: "whoPays.method.custom" };

export default function WhoPays() {
  const { t } = useTranslation("household");
  const [months, setMonths] = useState<"6" | "12" | "24">("12");
  const q = useAllocation(Number(months));
  const [open, setOpen] = useState(false);
  const del = useWrite((id: string) => api.post<EditResult>(`/household/allocations/${id}/delete`, {}), { success: t("whoPays.removed") });
  const people = usePeople();
  return (
    <>
      <PageHeader
        title={t("whoPays.header.title")}
        subtitle={t("whoPays.header.subtitle")}
        actions={
          <>
            <Segmented
              label={t("whoPays.period")}
              value={months}
              onChange={setMonths}
              options={(["6", "12", "24"] as const).map((v) => ({ value: v, label: t("whoPays.months", { count: Number(v) }) }))}
            />
            <Button variant="primary" onClick={() => setOpen(true)}><Plus className="size-4" aria-hidden /> {t("whoPays.addRule")}</Button>
          </>
        }
      />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) =>
          d.rules.length === 0 ? (
            <Card>
              <EmptyState
                icon={<Scale className="size-6" />}
                title={t("whoPays.empty.title")}
                action={<Button variant="primary" onClick={() => setOpen(true)}>{t("whoPays.empty.action")}</Button>}
              >
                {t("whoPays.empty.body")}
              </EmptyState>
            </Card>
          ) : (
            <div className="grid gap-4">
              {d.by_member.length > 0 && (
                <Card title={t("whoPays.total.title")} subtitle={t("whoPays.total.subtitle")}>
                  <ul className="divide-y divide-border">
                    {d.by_member.map((m) => (
                      <li key={m.member} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                        <b>{people.name(m.member)}</b>
                        <span className="num text-muted">
                          <Trans
                            t={t}
                            i18nKey="whoPays.total.line"
                            components={{ owed: <Money v={m.owed} />, paid: <Money v={m.paid} />, net: <Money v={m.net} colored signed className="font-semibold" /> }}
                          />
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              {d.rules.map((r) => (
                <Rule key={r.id} r={r} onDelete={() => confirm(t("whoPays.confirmRemove", { id: r.id })) && del.mutate(r.id)} />
              ))}
              <p className="text-xs text-muted">{t("whoPays.footnote")}</p>
            </div>
          )
        }
      </Async>
      {open && <RuleDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function Rule({ r, onDelete }: { r: AllocationRuleResult; onDelete: () => void }) {
  const { tServerList } = useServerText();
  const { t } = useTranslation("household");
  const people = usePeople();
  return (
    <Card
      title={r.title ?? r.id}
      subtitle={t("whoPays.rule.subtitle", { method: METHOD[r.method] ? t(METHOD[r.method]) : r.method, total: fmtMoney(r.total), count: r.n })}
      action={<Button size="sm" variant="ghost" aria-label={t("whoPays.rule.remove", { id: r.id })} onClick={onDelete}><Trash2 className="size-3.5" /></Button>}
      pad={false}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">{t("whoPays.rule.col.member")}</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">{t("whoPays.rule.col.share")}</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">{t("whoPays.rule.col.owed")}</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">{t("whoPays.rule.col.paid")}</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">{t("whoPays.rule.col.settlement")}</th>
            </tr>
          </thead>
          <tbody>
            {r.members.map((m) => (
              <tr key={m.member} className="border-t border-border">
                <td className="px-4 py-2 font-medium">{people.name(m.member)}</td>
                <td className="num px-2 py-2 text-right">{fmtPct(m.share_pct / 100, 1)}</td>
                <td className="num px-2 py-2 text-right"><Money v={m.owed} /></td>
                <td className="num px-2 py-2 text-right"><Money v={m.paid} /></td>
                <td className="num px-4 py-2 text-right font-semibold"><Money v={m.net} colored signed /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2 px-4 py-3 text-xs text-muted">
        <Badge>{t("whoPays.rule.jointPaid", { amount: fmtMoney(r.joint_paid) })}</Badge>
        <Badge>{t("whoPays.rule.personalPaid", { amount: fmtMoney(r.personal_paid) })}</Badge>
        {parseFloat(r.unattributed) !== 0 && <Badge tone="warn">{t("whoPays.rule.unattributed", { amount: fmtMoney(r.unattributed) })}</Badge>}
        {tServerList(r.notes, r.notes_msg).map((n) => (
          <span key={n}>{n}</span>
        ))}
      </div>
    </Card>
  );
}

function RuleDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation("household");
  const hh = useHousehold();
  const tax = useTaxonomy();
  const [id, setId] = useState("");
  const [title, setTitle] = useState("");
  const [group, setGroup] = useState("housing");
  const [method, setMethod] = useState<"equal" | "income" | "custom">("equal");
  const adults = (hh.data?.members ?? []).filter((m) => m.role === "adult");
  const [shares, setShares] = useState<Record<string, string>>({});
  const body = useMemo(
    () => ({
      title: title || undefined,
      match: { group },
      method,
      shares: method === "custom" ? Object.fromEntries(adults.map((m) => [m.id, Number(shares[m.id] ?? 0)])) : undefined,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [title, group, method, shares, hh.data],
  );
  const ready = /^[a-z0-9][a-z0-9_-]*$/.test(id) && !!group;
  const pv = useDryRun<EditResult>(`/household/allocations/${id}`, body, ready, "put");
  const save = useWrite(() => api.put<EditResult>(`/household/allocations/${id}`, body), { success: t("whoPays.dialog.saved"), onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title={t("whoPays.dialog.title")} size="lg" description={t("whoPays.dialog.description")}
      footer={<><Button onClick={onClose}>{t("whoPays.dialog.cancel")}</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>{t("whoPays.dialog.save")}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("whoPays.dialog.id")} hint={t("whoPays.dialog.idHint")}>{(fid) => <Input id={fid} value={id} onChange={(e) => setId(e.target.value)} placeholder="housing-5050" />}</Field>
        <Field label={t("whoPays.dialog.ruleTitle")}>{(fid) => <Input id={fid} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label={t("whoPays.dialog.group")}>{(fid) => <Select id={fid} value={group} onChange={(e) => setGroup(e.target.value)}>{(tax.data?.groups ?? []).map((g) => <option key={g.id} value={g.id}>{groupLabel(g.id)}</option>)}</Select>}</Field>
        <Field label={t("whoPays.dialog.method")}>
          {(fid) => (
            <Select id={fid} value={method} onChange={(e) => setMethod(e.target.value as typeof method)}>
              <option value="equal">{t("whoPays.dialog.equal")}</option>
              <option value="income">{t("whoPays.dialog.income")}</option>
              <option value="custom">{t("whoPays.dialog.custom")}</option>
            </Select>
          )}
        </Field>
      </div>
      {method === "custom" && (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {adults.map((m) => (
            <Field key={m.id} label={`${m.name} (%)`}>{(fid) => <Input id={fid} value={shares[m.id] ?? ""} onChange={(e) => setShares({ ...shares, [m.id]: e.target.value })} inputMode="decimal" />}</Field>
          ))}
        </div>
      )}
      <div className="mt-3">{pv.error && <Notice tone="neg">{pv.error}</Notice>}{pv.data && <DiffView diff={pv.data.diff} />}</div>
    </Dialog>
  );
}
