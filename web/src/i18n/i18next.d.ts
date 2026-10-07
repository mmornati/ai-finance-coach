// Typed keys: t("nav.missing") is a compile error. The English files are the source of truth for the key names.
import type common from "../locales/en/common.json";

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "common";
    resources: { common: typeof common };
  }
}
