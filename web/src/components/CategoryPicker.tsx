import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Select } from "./ui";
import { useTaxonomy } from "@/api/hooks";
import { catLabel, groupLabel } from "@/lib/format";

/** One native <select> grouped by taxonomy group: keyboard friendly, accessible, fast on a phone. */
export function CategoryPicker({ id, value, onChange, allowEmpty, emptyLabel, includeGroups, className }: {
  id?: string; value: string; onChange: (v: string) => void; allowEmpty?: boolean; emptyLabel?: string; includeGroups?: boolean; className?: string;
}) {
  const { t } = useTranslation();
  const { data } = useTaxonomy();
  const groups = useMemo(() => data?.groups ?? [], [data]);
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={className}>
      {allowEmpty && <option value="">{emptyLabel ?? t("picker.any")}</option>}
      {groups.map((g) => (
        <optgroup key={g.id} label={groupLabel(g.id)}>
          {includeGroups && <option value={g.id}>{t("picker.allIn", { group: groupLabel(g.id).toLowerCase() })}</option>}
          {g.categories.map((c) => (
            <option key={c.id} value={c.id} title={c.description}>
              {catLabel(c.id)}
            </option>
          ))}
        </optgroup>
      ))}
    </Select>
  );
}
