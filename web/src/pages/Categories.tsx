import { useMemo, useState } from "react";
import { Link } from "react-router";
import { Trans, useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { Async, Badge, Button, Card, Dialog, DiffView, EmptyState, Field, Input, Money, Notice, PageHeader, Segmented, Select, Skeleton } from "@/components/ui";
import { ShareBar } from "@/components/charts";
import { useDryRun, useScoped, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { catLabel, fmtMoney, fmtMonth, groupLabel, parseMoney } from "@/lib/format";
import type { CategoryOverview, EditResult } from "@/api/types";
import { tServerList } from "@/i18n/server";

export default function Categories() {
  const q = useScoped<CategoryOverview>("/categories");
  const [view, setView] = useState<"groups" | "flat">("groups");
  const [text, setText] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const { t } = useTranslation("categories");
  return (
    <>
      <PageHeader
        title={t("overview.title")}
        subtitle={t("overview.subtitle")}
        actions={
          <div className="flex items-center gap-2">
            <Segmented
              label={t("overview.view")}
              value={view}
              onChange={setView}
              options={[
                { value: "groups", label: t("overview.grouped") },
                { value: "flat", label: t("overview.ranked") },
              ]}
            />
            <Button variant="secondary" onClick={() => setShowAdd(true)}>
              <Plus className="size-4" /> {t("overview.addCategory")}
            </Button>
          </div>
        }
      />
      <Async q={q} skeleton={<Skeleton className="h-96 w-full" />}>
        {(d) => {
          const rows = d.categories.filter((c) => !text || catLabel(c.category).toLowerCase().includes(text.toLowerCase()) || c.category.includes(text.toLowerCase()));
          const max = Math.max(...rows.map((r) => parseMoney(r.monthly_avg) ?? 0), 1);
          const groups = groupRows(rows, d.groups);
          const existingGroups = d.groups.map((g) => g.group);
          return (
            <div className="grid gap-4">
              <Card>
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <div className="text-xs font-medium text-muted">{t("overview.usualHousehold")}</div>
                    <div className="num text-2xl font-semibold">{d.household_monthly_avg ? fmtMoney(d.household_monthly_avg) : "–"}</div>
                    <div className="text-xs text-faint">{d.household_months.length ? t("overview.averageOf", { count: d.household_months.length, from: fmtMonth(d.household_months[0]), to: fmtMonth(d.household_months.at(-1)) }) : t("overview.noMonth")}</div>
                  </div>
                  <Input aria-label={t("overview.filter")} type="search" placeholder={t("overview.filter")} value={text} onChange={(e) => setText(e.target.value)} className="max-w-60" />
                </div>
                {tServerList(d.coverage.notes, d.coverage.notes_msg).map((n) => <Notice key={n} tone="warn" className="mt-3">{n}</Notice>)}
              </Card>
              {rows.length === 0 && <Card><EmptyState title={t("overview.empty")} /></Card>}
              {view === "flat" ? (
                <Card pad={false}><CatTable rows={rows} max={max} /></Card>
              ) : (
                groups.map((g) => (
                  <Card key={g.group} title={<Link className="hover:underline" to={`/categories/${g.group}`}>{groupLabel(g.group)}</Link>} action={<span className="num text-sm text-muted"><Trans t={t} i18nKey="overview.usualMonth" components={{ b: <b className="text-text" />, amount: g.monthly_avg ? <Money v={g.monthly_avg} round /> : <>–</> }} />{g.low_confidence && <Badge tone="warn" className="ml-2">{t("overview.lowConfidence")}</Badge>}</span>} pad={false}>
                    <CatTable rows={g.rows} max={max} />
                  </Card>
                ))
              )}
              {d.unavailable.length > 0 && <Notice title={t("overview.unavailableTitle")}>{t("overview.unavailable", { list: d.unavailable.map((u) => catLabel(u.category)).join(", ") })}</Notice>}
              {showAdd && (
                <AddCategoryDialog
                  open={showAdd}
                  onClose={() => setShowAdd(false)}
                  existingGroups={existingGroups}
                />
              )}
            </div>
          );
        }}
      </Async>
    </>
  );
}

function AddCategoryDialog({
  open,
  onClose,
  existingGroups,
}: {
  open: boolean;
  onClose: () => void;
  existingGroups: string[];
}) {
  const { t } = useTranslation("categories");
  const defaultGroup = existingGroups[0] || "daily";
  const [selectedGroup, setSelectedGroup] = useState(defaultGroup);
  const [customGroup, setCustomGroup] = useState("");
  const [leaf, setLeaf] = useState("");
  const [description, setDescription] = useState("");

  const group = selectedGroup === "__new__" ? customGroup.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_") : selectedGroup;
  const cleanLeaf = leaf.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
  const cid = group && cleanLeaf ? `${group}.${cleanLeaf}` : "";

  const ready = Boolean(cid && description.trim());
  const body = useMemo(
    () => ({ cid, description: description.trim() }),
    [cid, description],
  );

  const pv = useDryRun<EditResult>("/meta/taxonomy/categories", body, ready);

  const add = useWrite(
    () => api.post<EditResult>("/meta/taxonomy/categories", body, { dry_run: false }),
    {
      success: t("add.success"),
      onSuccess: () => {
        onClose();
        setLeaf("");
        setDescription("");
        setCustomGroup("");
      },
    },
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready || pv.error || pv.loading) return;
    add.mutate(undefined as never);
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("add.title")}
      description={t("add.description")}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t("add.cancel")}
          </Button>
          <Button
            variant="primary"
            disabled={!ready || Boolean(pv.error) || pv.loading}
            busy={add.isPending}
            onClick={handleSubmit}
          >
            {t("add.submit")}
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="grid gap-4">
        {pv.error && <Notice tone="neg">{pv.error}</Notice>}
        <Field label={t("add.groupLabel")} hint={t("add.groupHint")}>
          {(id) => (
            <Select
              id={id}
              value={selectedGroup}
              onChange={(e) => setSelectedGroup(e.target.value)}
            >
              {existingGroups.map((g) => (
                <option key={g} value={g}>
                  {groupLabel(g)} ({g})
                </option>
              ))}
              <option value="__new__">{t("add.newGroup")}</option>
            </Select>
          )}
        </Field>
        {selectedGroup === "__new__" && (
          <Field label={t("add.customGroupName")}>
            {(id) => (
              <Input
                id={id}
                value={customGroup}
                onChange={(e) => setCustomGroup(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))}
                placeholder="e.g. pets"
              />
            )}
          </Field>
        )}
        <Field label={t("add.leafLabel")} hint={t("add.leafHint")}>
          {(id) => (
            <Input
              id={id}
              value={leaf}
              onChange={(e) => setLeaf(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))}
              placeholder="e.g. vet_expenses"
            />
          )}
        </Field>
        <Field label={t("add.descLabel")} hint={t("add.descHint")}>
          {(id) => (
            <Input
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Veterinary visits, medications, pet care"
            />
          )}
        </Field>
        {cid && (
          <div className="rounded-lg border border-border bg-surface-2 p-3 text-xs text-muted">
            <span className="font-medium text-text">{t("add.previewId")}:</span> <code className="font-semibold text-accent">{cid}</code>
          </div>
        )}
        {pv.data?.diff && (
          <div className="grid gap-1">
            <span className="text-xs font-medium text-muted">{t("add.diffPreview")}</span>
            <DiffView diff={pv.data.diff} />
          </div>
        )}
      </form>
    </Dialog>
  );
}

/** Rows grouped as the API groups them; every group figure (usual month, this / last month) is the analytics' own. */
function groupRows(rows: CategoryOverview["categories"], groups: CategoryOverview["groups"]) {
  return groups
    .map((g) => ({ ...g, rows: rows.filter((r) => r.group === g.group) }))
    .filter((g) => g.rows.length > 0);
}

function CatTable({ rows, max }: { rows: CategoryOverview["categories"]; max: number }) {
  const { t } = useTranslation("categories");
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">{t("table.category")}</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">{t("table.usualMonth")}</th>
            <th scope="col" className="px-2 py-2 text-right font-medium">{t("table.thisMonth")}</th>
            <th scope="col" className="px-4 py-2 text-right font-medium">{t("table.lastMonth")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.category} className="border-t border-border">
              <td className="px-4 py-2">
                <Link to={`/categories/${r.category}`} className="font-medium hover:underline">{catLabel(r.category)}</Link>
                {r.low_confidence && <Badge tone="warn" className="ml-2" title={t("table.lowConfidenceTitle")}>{t("overview.lowConfidence")}</Badge>}
                {r.lumpy && <Badge className="ml-2" title={t("table.lumpyTitle")}>{t("table.lumpy")}</Badge>}
                <div className="mt-1 max-w-56"><ShareBar value={parseMoney(r.monthly_avg) ?? 0} max={max} /></div>
              </td>
              <td className="num px-2 py-2 text-right font-medium">{r.monthly_avg ? fmtMoney(r.monthly_avg, { round: true }) : "–"}<div className="text-[11px] font-normal text-faint">{r.n_months ? t("table.months", { n: r.n_months }) : t("table.noFairAverage")}</div></td>
              <td className="num px-2 py-2 text-right">{fmtMoney(r.this_month, { round: true })}</td>
              <td className="num px-4 py-2 text-right text-muted">{fmtMoney(r.last_month, { round: true })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
