import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { EmptyState } from "@/components/ui";

export default function NotFound() {
  const { t } = useTranslation();
  return (
    <EmptyState title={t("notFound.title")} action={<Link className="text-accent underline" to="/">{t("notFound.back")}</Link>}>
      {t("notFound.body")}
    </EmptyState>
  );
}
