// Typed keys: t("nav.missing") is a compile error. The English files are the source of truth for the key names.
import type budgets from "../locales/en/budgets.json";
import type calendar from "../locales/en/calendar.json";
import type categories from "../locales/en/categories.json";
import type coach from "../locales/en/coach.json";
import type common from "../locales/en/common.json";
import type alerts from "../locales/en/alerts.json";
import type connections from "../locales/en/connections.json";
import type household from "../locales/en/household.json";
import type kids from "../locales/en/kids.json";
import type memory from "../locales/en/memory.json";
import type quality from "../locales/en/quality.json";
import type rental from "../locales/en/rental.json";
import type server from "../locales/en/server.json";
import type setup from "../locales/en/setup.json";
import type wealth from "../locales/en/wealth.json";
import type dashboard from "../locales/en/dashboard.json";
import type insights from "../locales/en/insights.json";
import type subscriptions from "../locales/en/subscriptions.json";
import type taxonomy from "../locales/en/taxonomy.json";
import type transactions from "../locales/en/transactions.json";

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
      server: typeof server;
      setup: typeof setup;
      wealth: typeof wealth;
      budgets: typeof budgets;
      calendar: typeof calendar;
      categories: typeof categories;
      coach: typeof coach;
      dashboard: typeof dashboard;
      insights: typeof insights;
      subscriptions: typeof subscriptions;
      taxonomy: typeof taxonomy;
      transactions: typeof transactions;
    };
  }
}
