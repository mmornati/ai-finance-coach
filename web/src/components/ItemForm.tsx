import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Button, Dialog, DiffView, Field, Input, Notice, Select, Spinner, Textarea } from "./ui";
import { useDryRun, useFilters, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { parseMoney } from "@/lib/format";
import type { EditResult } from "@/api/types";

export type Kind = "liabilities" | "contracts" | "assets" | "members" | "events";
type FT = "text" | "number" | "date" | "select" | "bool" | "textarea" | "list" | "int" | "account";
interface Spec { key: string; label: string; type: FT; options?: [string, string][]; hint?: string; wide?: boolean; when?: (vals: Record<string, string>) => boolean }

const isLease = (v: Record<string, string>) => v.kind === "loa" || v.kind === "lld";
const notLease = (v: Record<string, string>) => !isLease(v);
const isVariable = (v: Record<string, string>) => v["rate.type"] === "variable" || v["rate.type"] === "mixed";

const LIAB_KINDS = ["mortgage", "car_loan", "loa", "lld", "consumer_loan", "bnpl"] as const;
// [the code stored in the contract file, the i18n key of its label]: the code `insurance_other` must not be a key (a `_other` suffix means "plural form")
const CONTRACT_KINDS = [["energy", "energy"], ["telecom", "telecom"], ["insurance_home", "insuranceHome"], ["insurance_car", "insuranceCar"], ["insurance_health", "insuranceHealth"], ["health", "health"], ["streaming", "streaming"], ["software", "software"], ["membership", "membership"], ["insurance_other", "insuranceOther"], ["water", "water"], ["other", "other"]] as const;
const ASSET_KINDS = ["regulated_savings", "savings_account", "employee_savings_plan", "life_insurance_savings", "vehicle", "real_estate", "real_estate_rental", "securities", "pension", "cash", "crypto", "other"] as const;

/** The form fields of each kind of item. Built per render with `t` so the labels follow the language; the keys (`key`) are the memory schema's. */
function buildSpecs(t: TFunction): Record<Kind, Spec[]> {
  return {
  liabilities: [
    { key: "kind", label: t("itemForm.field.kind"), type: "select", options: LIAB_KINDS.map((k): [string, string] => [k, t(`itemForm.option.liabilityKind.${k}`)]) },
    { key: "lender", label: t("itemForm.field.lender"), type: "text" },
    { key: "asset", label: t("itemForm.field.asset"), type: "text" },
    { key: "holder", label: t("itemForm.field.borrower"), type: "text", hint: t("itemForm.field.borrowerHint") },
    { key: "start_date", label: t("itemForm.field.startDate"), type: "date" },
    { key: "first_payment_date", label: t("itemForm.field.firstPaymentDate"), type: "date", hint: t("itemForm.field.firstPaymentDateHint"), when: notLease },
    { key: "end_date", label: t("itemForm.field.endDate"), type: "date" },
    { key: "term_months", label: t("itemForm.field.termMonths"), type: "int", hint: t("itemForm.field.termMonthsHint") },
    { key: "payment_day", label: t("itemForm.field.paymentDay"), type: "int" },
    { key: "principal", label: t("itemForm.field.principal"), type: "number", when: notLease },
    { key: "monthly_payment", label: t("itemForm.field.monthlyPayment"), type: "number", hint: t("itemForm.field.monthlyPaymentHint") },
    { key: "rate.type", label: t("itemForm.field.rateType"), type: "select", options: [["fixed", t("itemForm.option.rateType.fixed")], ["variable", t("itemForm.option.rateType.variable")], ["mixed", t("itemForm.option.rateType.mixed")]], when: notLease },
    { key: "rate.nominal", label: t("itemForm.field.nominalRate"), type: "number", when: notLease },
    { key: "rate.taeg", label: t("itemForm.field.taeg"), type: "number", when: notLease },
    { key: "rate.index", label: t("itemForm.field.rateIndex"), type: "text", hint: t("itemForm.field.rateIndexHint"), when: (v) => notLease(v) && isVariable(v) },
    { key: "rate.margin", label: t("itemForm.field.rateMargin"), type: "number", when: (v) => notLease(v) && isVariable(v) },
    { key: "rate.cap", label: t("itemForm.field.rateCap"), type: "number", when: (v) => notLease(v) && isVariable(v) },
    { key: "insurance.provider", label: t("itemForm.field.insuranceProvider"), type: "text" },
    { key: "insurance.monthly", label: t("itemForm.field.insuranceMonthly"), type: "number", hint: t("itemForm.field.insuranceMonthlyHint") },
    { key: "insurance.rate_pct", label: t("itemForm.field.insuranceRate"), type: "number", hint: t("itemForm.field.insuranceRateHint"), when: notLease },
    { key: "insurance.basis", label: t("itemForm.field.insuranceBasis"), type: "select", options: [["initial", t("itemForm.option.insuranceBasis.initial")], ["outstanding", t("itemForm.option.insuranceBasis.outstanding")]], when: notLease },
    { key: "insurance.delegated", label: t("itemForm.field.insuranceDelegated"), type: "bool", when: notLease },
    { key: "deferral.months", label: t("itemForm.field.deferralMonths"), type: "int", when: notLease },
    { key: "deferral.kind", label: t("itemForm.field.deferralKind"), type: "select", options: [["partial", t("itemForm.option.deferralKind.partial")], ["total", t("itemForm.option.deferralKind.total")]], when: notLease },
    { key: "outstanding", label: t("itemForm.field.outstanding"), type: "number", hint: t("itemForm.field.outstandingHint"), when: notLease },
    { key: "outstanding_as_of", label: t("itemForm.field.outstandingAsOf"), type: "date", when: notLease },
    { key: "debited_account", label: t("itemForm.field.debitedAccount"), type: "account", hint: t("itemForm.field.debitedAccountHint") },
    { key: "payment_match", label: t("itemForm.field.bankLabel"), type: "text", hint: t("itemForm.field.paymentMatchHint") },
    { key: "first_payment", label: t("itemForm.field.firstRent"), type: "number", when: isLease },
    { key: "residual_value", label: t("itemForm.field.residualValue"), type: "number", when: isLease },
    { key: "mileage_limit_km", label: t("itemForm.field.mileageLimit"), type: "int", when: isLease },
    { key: "excess_km_fee", label: t("itemForm.field.excessKmFee"), type: "number", when: isLease },
    { key: "initial_km", label: t("itemForm.field.initialKm"), type: "int", hint: t("itemForm.field.initialKmHint"), when: isLease },
    { key: "notes", label: t("itemForm.field.notes"), type: "textarea", wide: true },
  ],
  contracts: [
    { key: "provider", label: t("itemForm.field.provider"), type: "text" },
    { key: "kind", label: t("itemForm.field.kind"), type: "select", options: CONTRACT_KINDS.map(([code, key]): [string, string] => [code, t(`itemForm.option.contractKind.${key}`)]) },
    { key: "merchant_match", label: t("itemForm.field.bankLabel"), type: "text" },
    { key: "start_date", label: t("itemForm.field.startDate"), type: "date" },
    { key: "renewal", label: t("itemForm.field.renewal"), type: "date" },
    { key: "billing.amount", label: t("itemForm.field.amount"), type: "number" },
    { key: "billing.period", label: t("itemForm.field.billed"), type: "select", options: [["monthly", t("itemForm.option.billingPeriod.monthly")], ["bimonthly", t("itemForm.option.billingPeriod.bimonthly")], ["quarterly", t("itemForm.option.billingPeriod.quarterly")], ["yearly", t("itemForm.option.billingPeriod.yearly")]] },
    { key: "commitment_end", label: t("itemForm.field.commitmentEnd"), type: "date" },
    { key: "notice_period_days", label: t("itemForm.field.noticeDays"), type: "int" },
    { key: "holder", label: t("itemForm.field.holder"), type: "text", hint: t("itemForm.field.holderContractHint") },
    { key: "contract_number", label: t("itemForm.field.contractNumber"), type: "text", hint: t("itemForm.field.contractNumberHint") },
    { key: "usage.frequency", label: t("itemForm.field.usageFrequency"), type: "select", options: [["daily", t("itemForm.option.usageFrequency.daily")], ["weekly", t("itemForm.option.usageFrequency.weekly")], ["monthly", t("itemForm.option.usageFrequency.monthly")], ["rarely", t("itemForm.option.usageFrequency.rarely")], ["never", t("itemForm.option.usageFrequency.never")], ["unknown", t("itemForm.option.usageFrequency.unknown")]] },
    { key: "usage.last_used", label: t("itemForm.field.lastUsed"), type: "date" },
    { key: "usage.note", label: t("itemForm.field.usageNote"), type: "text" },
    { key: "keep", label: t("itemForm.field.decision"), type: "select", options: [["true", t("itemForm.option.keep.true")], ["review", t("itemForm.option.keep.review")], ["false", t("itemForm.option.keep.false")]] },
    { key: "notes", label: t("itemForm.field.notes"), type: "textarea", wide: true },
  ],
  assets: [
    { key: "kind", label: t("itemForm.field.kind"), type: "select", options: ASSET_KINDS.map((k): [string, string] => [k, t(`itemForm.option.assetKind.${k}`)]) },
    { key: "provider", label: t("itemForm.field.provider"), type: "text" },
    { key: "holder", label: t("itemForm.field.holder"), type: "text" },
    { key: "@value", label: t("itemForm.field.currentValue"), type: "number" },
    { key: "as_of", label: t("itemForm.field.valueAsOf"), type: "date" },
    { key: "liquidity", label: t("itemForm.field.liquidity"), type: "select", options: [["immediate", t("itemForm.option.liquidity.immediate")], ["short", t("itemForm.option.liquidity.short")], ["locked", t("itemForm.option.liquidity.locked")]] },
    { key: "connected", label: t("itemForm.field.connected"), type: "bool", wide: true },
    { key: "contribution_monthly", label: t("itemForm.field.contribution"), type: "number" },
    { key: "description", label: t("itemForm.field.descriptionField"), type: "text" },
    { key: "notes", label: t("itemForm.field.notes"), type: "textarea", wide: true },
  ],
  members: [
    { key: "name", label: t("itemForm.field.name"), type: "text", hint: t("itemForm.field.nameHint") },
    { key: "role", label: t("itemForm.field.role"), type: "select", options: [["adult", t("itemForm.option.role.adult")], ["child", t("itemForm.option.role.child")]] },
    { key: "birth_year", label: t("itemForm.field.birthYear"), type: "int" },
    { key: "aliases", label: t("itemForm.field.aliases"), type: "list", hint: t("itemForm.field.aliasesHint") },
  ],
  events: [
    { key: "title", label: t("itemForm.field.title"), type: "text" },
    { key: "start", label: t("itemForm.field.start"), type: "date" },
    { key: "end", label: t("itemForm.field.end"), type: "date" },
    { key: "budget", label: t("itemForm.field.budget"), type: "number" },
    { key: "status", label: t("itemForm.field.status"), type: "select", options: [["planned", t("itemForm.option.eventStatus.planned")], ["ongoing", t("itemForm.option.eventStatus.ongoing")], ["done", t("itemForm.option.eventStatus.done")]] },
    { key: "note", label: t("itemForm.field.note"), type: "textarea", wide: true },
  ],
  };
}

function flatten(o: Record<string, any>, prefix = ""): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(o)) {
    if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v, `${prefix}${k}.`));
    else out[`${prefix}${k}`] = v;
  }
  return out;
}
function nest(flat: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(flat)) {
    const parts = k.split(".");
    let cur = out;
    parts.slice(0, -1).forEach((p) => (cur = cur[p] ??= {}));
    cur[parts.at(-1)!] = v;
  }
  return out;
}

function toStr(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

function convert(spec: Spec, raw: string): unknown {
  const s = raw.trim();
  if (s === "") return null;
  switch (spec.type) {
    case "number": return parseMoney(s.replace(",", "."));
    case "int": return Number.parseInt(s, 10);
    case "list": return s.split(",").map((x) => x.trim()).filter(Boolean);
    case "bool": return s === "true";
    default:
      if (spec.key === "keep") return s === "true" ? true : s === "false" ? false : s;
      return s;
  }
}

/** A validated form for one memory item: the server validates (schema + semantics), previews the diff and records it. */
export function ItemDialog({ kind, id: initialId, initial, onClose }: { kind: Kind; id?: string; initial?: Record<string, any>; onClose: () => void }) {
  const { t } = useTranslation();
  const filters = useFilters();
  const specs = useMemo(() => buildSpecs(t)[kind], [kind, t]);
  const isNew = !initialId;
  const base = useMemo(() => {
    const f = flatten(initial ?? {});
    if (kind === "assets" && initial) f["@value"] = initial.value;
    if (kind === "contracts" && f.keep !== undefined) f.keep = f.keep;
    return f;
  }, [initial, kind]);
  const [id, setId] = useState(initialId ?? "");
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(specs.map((s) => [s.key, toStr(base[s.key])])));
  const valueField = (initial as any)?.value_field ?? (["vehicle", "real_estate", "real_estate_rental", "other"].includes(vals.kind) ? "value" : "balance");

  const fields = useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const s of specs) {
      const cur = vals[s.key] ?? "";
      if (!isNew && cur === toStr(base[s.key])) continue; // only what the user changed
      if (isNew && cur === "") continue;
      const key = s.key === "@value" ? valueField : s.key;
      out[key] = convert(s, cur);
    }
    return nest(out);
  }, [vals, specs, isNew, base, valueField]);
  const idOk = /^[a-z0-9][a-z0-9_-]*$/.test(id);
  const has = Object.keys(fields).length > 0;
  const enabled = idOk && has;
  const path = `/memory/${kind}/${id}`;
  const pv = useDryRun<EditResult>(path, { fields }, enabled, "put");
  const save = useWrite(() => api.put<EditResult>(path, { fields }, { dry_run: false }), { success: t("itemForm.saved"), onSuccess: onClose });
  const missingKind = kind === "liabilities" && isNew && !vals.kind;

  return (
    <Dialog open onClose={onClose} size="lg" title={isNew ? t(`itemForm.new.${kind}`) : t(`itemForm.edit.${kind}`, { id: initialId })} description={t("itemForm.description")}
      footer={<><Button variant="ghost" onClick={onClose}>{t("itemForm.cancel")}</Button><Button variant="primary" busy={save.isPending} disabled={!enabled || !pv.data || !!pv.error || pv.loading || missingKind} onClick={() => save.mutate(undefined as never)}>{t("itemForm.save")}</Button></>}>
      <div className="grid gap-4 sm:grid-cols-2">
        {isNew && <Field label={t("itemForm.id")} hint={t("itemForm.idHint")} error={id && !idOk ? t("itemForm.idError") : undefined}>{(i) => <Input id={i} value={id} onChange={(e) => setId(e.target.value.toLowerCase())} placeholder={kind === "members" ? "anna" : "home-loan"} />}</Field>}
        {specs.filter((s) => !s.when || s.when(vals)).map((s) => (
          <Field key={s.key} label={s.label} hint={s.hint} className={s.wide ? "sm:col-span-2" : undefined}>
            {(i) => {
              const v = vals[s.key] ?? "";
              const set = (x: string) => setVals((c) => ({ ...c, [s.key]: x }));
              if (s.type === "select") return <Select id={i} value={v} onChange={(e) => set(e.target.value)}><option value="">{t("itemForm.none")}</option>{s.options!.map(([a, b]) => <option key={a} value={a}>{b}</option>)}</Select>;
              if (s.type === "account") return <Select id={i} value={v} onChange={(e) => set(e.target.value)}><option value="">{t("itemForm.none")}</option>{filters.data?.accounts.map((a) => <option key={a.uid} value={a.uid}>{a.label}</option>)}</Select>;
              if (s.type === "bool") return <label className="flex min-h-10 items-center gap-2 text-sm"><input id={i} type="checkbox" checked={v === "true"} onChange={(e) => set(e.target.checked ? "true" : "false")} /> {s.label}</label>;
              if (s.type === "textarea") return <Textarea id={i} value={v} onChange={(e) => set(e.target.value)} />;
              return <Input id={i} type={s.type === "date" ? "date" : "text"} inputMode={s.type === "number" || s.type === "int" ? "decimal" : undefined} value={v} onChange={(e) => set(e.target.value)} />;
            }}
          </Field>
        ))}
      </div>
      <div className="mt-4 grid gap-2">
        {missingKind && <Notice tone="warn">{t("itemForm.chooseKind")}</Notice>}
        {pv.loading && <Spinner label={t("itemForm.validating")} />}
        {pv.error && <Notice tone="neg" title={t("itemForm.notValid")}>{pv.error}</Notice>}
        {pv.data && <>{pv.data.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}<DiffView diff={pv.data.diff} empty={t("itemForm.nothingChanges")} /></>}
      </div>
    </Dialog>
  );
}
