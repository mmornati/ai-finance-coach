import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Fingerprint, Trash2 } from "lucide-react";
import { Badge, Button, Card, Input } from "./ui";
import { useGet, useSession, useWrite } from "@/api/hooks";
import { api } from "@/lib/api";
import { enrolPasskey, passkeysSupported } from "@/lib/webauthn";
import { fmtDateTime } from "@/lib/format";
import type { PasskeyList } from "@/api/types";

/** The passkeys of THIS login (E16): enrol the device at hand, remove a lost one. Shown only when the server enables passkeys. */
export function PasskeysCard() {
  const { t } = useTranslation();
  const session = useSession();
  const enabled = session.data?.auth?.passkeys ?? false;
  const q = useGet<PasskeyList>("/session/passkeys", undefined, { enabled });
  const [label, setLabel] = useState("");
  const add = useWrite((l: string) => enrolPasskey(l), { success: t("passkeys.added"), onSuccess: () => setLabel("") });
  const remove = useWrite((id: string) => api.post("/session/passkeys/delete", { id }), { success: t("passkeys.removed") });
  if (!enabled) return null;
  const supported = passkeysSupported();
  return (
    <Card title={t("passkeys.title")} subtitle={t("passkeys.subtitle")}>
      {q.data && q.data.passkeys.length > 0 ? (
        <ul className="mb-3 divide-y divide-border">
          {q.data.passkeys.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <span className="flex items-center gap-2">
                <Fingerprint className="size-4 text-accent" aria-hidden />
                <b>{p.label}</b>
                <span className="text-xs text-muted">
                  {t("passkeys.created", { date: fmtDateTime(p.created_at) })}
                  {" · "}
                  {p.last_used_at ? t("passkeys.lastUsed", { date: fmtDateTime(p.last_used_at) }) : t("passkeys.never")}
                </span>
                {p.backed_up && <Badge tone="info">{t("passkeys.synced")}</Badge>}
              </span>
              <Button size="sm" variant="ghost" onClick={() => remove.mutate(p.id)} busy={remove.isPending} aria-label={t("passkeys.remove", { label: p.label })}>
                <Trash2 className="size-4" aria-hidden /> {t("passkeys.removeShort")}
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-3 text-[13px] text-muted">{t("passkeys.empty")}</p>
      )}
      {supported ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate(label.trim());
          }}
        >
          <Input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={40} placeholder={t("passkeys.labelPlaceholder")} aria-label={t("passkeys.label")} className="!w-56" />
          <Button type="submit" variant="primary" busy={add.isPending}>
            <Fingerprint className="size-4" aria-hidden /> {t("passkeys.add")}
          </Button>
        </form>
      ) : (
        <p className="text-[13px] text-muted">{t("passkeys.unsupported")}</p>
      )}
    </Card>
  );
}
