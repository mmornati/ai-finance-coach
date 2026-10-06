import { useMemo, useState } from "react";
import { Plus, Scale, Trash2 } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, Segmented, Select, Skeleton } from "@/components/ui";
import { useAllocation, useDryRun, useHousehold, usePeople, useTaxonomy, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { fmtMoney, fmtPct, groupLabel } from "@/lib/format";
import type { AllocationRuleResult, EditResult } from "@/api/types";

const METHOD: Record<string, string> = { equal: "shared equally", income: "in proportion to income", custom: "by the percentages you set" };

export default function WhoPays() {
  const [months, setMonths] = useState<"6" | "12" | "24">("12");
  const q = useAllocation(Number(months));
  const [open, setOpen] = useState(false);
  const del = useWrite((id: string) => api.post<EditResult>(`/household/allocations/${id}/delete`, {}), { success: "Allocation rule removed" });
  const people = usePeople();
  return (
    <>
      <PageHeader
        title="Who pays what"
        subtitle="Shared costs (house, cars, insurance) split by the rules you set. A cost paid from the joint account settles nothing; what a person paid from their own money is compared with their fair share."
        actions={
          <>
            <Segmented label="Period" value={months} onChange={setMonths} options={[{ value: "6", label: "6 months" }, { value: "12", label: "12 months" }, { value: "24", label: "24 months" }]} />
            <Button variant="primary" onClick={() => setOpen(true)}><Plus className="size-4" aria-hidden /> Rule</Button>
          </>
        }
      />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) =>
          d.rules.length === 0 ? (
            <Card>
              <EmptyState icon={<Scale className="size-6" />} title="No allocation rule yet" action={<Button variant="primary" onClick={() => setOpen(true)}>Add a rule</Button>}>
                A rule says which costs it covers (a category, a group, a tag, a merchant), how they are shared (50/50, by income, custom percentages) and between whom.
              </EmptyState>
            </Card>
          ) : (
            <div className="grid gap-4">
              {d.by_member.length > 0 && (
                <Card title="All rules together" subtitle="Positive: the others owe this person. Negative: this person owes.">
                  <ul className="divide-y divide-border">
                    {d.by_member.map((m) => (
                      <li key={m.member} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                        <b>{people.name(m.member)}</b>
                        <span className="num text-muted">owed share <Money v={m.owed} /> · paid <Money v={m.paid} /> · settlement <Money v={m.net} colored signed className="font-semibold" /></span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              {d.rules.map((r) => (
                <Rule key={r.id} r={r} onDelete={() => confirm(`Remove the allocation rule "${r.id}"?`) && del.mutate(r.id)} />
              ))}
              <p className="text-xs text-muted">A split of the past, computed by code from the transactions attributed to each person. It moves no money and is not legal or tax advice on a couple's finances.</p>
            </div>
          )
        }
      </Async>
      {open && <RuleDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function Rule({ r, onDelete }: { r: AllocationRuleResult; onDelete: () => void }) {
  const people = usePeople();
  return (
    <Card
      title={r.title ?? r.id}
      subtitle={`${METHOD[r.method] ?? r.method} · ${fmtMoney(r.total)} over ${r.n} payment(s)`}
      action={<Button size="sm" variant="ghost" aria-label={`Remove allocation ${r.id}`} onClick={onDelete}><Trash2 className="size-3.5" /></Button>}
      pad={false}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Member</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Share</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Fair share of the cost</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Paid personally</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Settlement</th>
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
        <Badge>joint account {fmtMoney(r.joint_paid)}</Badge>
        <Badge>paid personally {fmtMoney(r.personal_paid)}</Badge>
        {parseFloat(r.unattributed) !== 0 && <Badge tone="warn">outside the rule {fmtMoney(r.unattributed)}</Badge>}
        {r.notes.map((n) => (
          <span key={n}>{n}</span>
        ))}
      </div>
    </Card>
  );
}

function RuleDialog({ onClose }: { onClose: () => void }) {
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
  const save = useWrite(() => api.put<EditResult>(`/household/allocations/${id}`, body), { success: "Allocation rule saved", onSuccess: onClose });
  return (
    <Dialog open onClose={onClose} title="New allocation rule" size="lg" description="Shared between every adult unless you pick custom percentages. The first rule that matches a payment takes it."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready || !!pv.error} busy={save.isPending} onClick={() => save.mutate(undefined as never)}>Save rule</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Id" hint="lowercase letters, digits, - or _">{(fid) => <Input id={fid} value={id} onChange={(e) => setId(e.target.value)} placeholder="housing-5050" />}</Field>
        <Field label="Title (optional)">{(fid) => <Input id={fid} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label="Costs covered (category group)">{(fid) => <Select id={fid} value={group} onChange={(e) => setGroup(e.target.value)}>{(tax.data?.groups ?? []).map((g) => <option key={g.id} value={g.id}>{groupLabel(g.id)}</option>)}</Select>}</Field>
        <Field label="Shared">{(fid) => <Select id={fid} value={method} onChange={(e) => setMethod(e.target.value as typeof method)}><option value="equal">Equally</option><option value="income">In proportion to income</option><option value="custom">Custom percentages</option></Select>}</Field>
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
