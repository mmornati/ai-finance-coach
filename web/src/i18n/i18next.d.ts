// Typed keys: t("nav.missing") is a compile error. The English files are the source of truth for the key names.
import type common from "../locales/en/common.json";
import type alerts from "../locales/en/alerts.json";
import type connections from "../locales/en/connections.json";
import type household from "../locales/en/household.json";
import type kids from "../locales/en/kids.json";
import type memory from "../locales/en/memory.json";
import type quality from "../locales/en/quality.json";
import type rental from "../locales/en/rental.json";
import type setup from "../locales/en/setup.json";
import type wealth from "../locales/en/wealth.json";

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "common";
    resources: {
      common: typeof common;
      alerts: typeof alerts;
      connections: typeof connections;
      household: typeof household;
      kids: typeof kids;
      memory: typeof memory;
      quality: typeof quality;
      rental: typeof rental;
      setup: typeof setup;
      wealth: typeof wealth;
    };
  }
}
