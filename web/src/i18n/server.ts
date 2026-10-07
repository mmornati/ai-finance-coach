// Text that comes from the server, translated by the web app (i18n step 4; the convention is in docs/i18n.md, "Server text").
//
// The API keeps its English sentence and sends, next to it, a CODE and RAW parameters: `{ code, params, text }` (built by
// `coach.i18n_msg.server_msg` in Python). The code is a key of the `server` namespace (src/locales/<lang>/server.json); a param is
// formatted from its NAME (`*_date`, `*_month`, `*_amount`, `*_pct`, `*_num`, `*_category`, `*_group`, `count`); an unknown code shows the English
// `text`. A fixed vocabulary (an alert kind, a balance type, a setup step...) is a code the payload already carries: `serverLabel(family, code,
// english)` looks up `labels.<family>.<code>`. An API error is translated by its `code` (`error.<code>`), else its own message.
import { useTranslation } from "react-i18next";
import i18n from "i18next";
import { ApiError } from "@/lib/api";
import { catLabel, fmtDate, fmtMoney, fmtMonth, fmtNumber, fmtPct, groupLabel } from "@/lib/format";

export type ServerParam = string | number | null;
/** A sentence of the server: translated by `code` when the web knows it, else `text` (English). */
export interface ServerMsg {
  code: string;
  params?: Record<string, ServerParam>;
  text: string;
}

/** The fixed vocabularies the server sends as codes (keys `labels.<family>.<code>` of server.json). */
export type LabelFamily = "alertKind" | "subsGroup" | "balanceType" | "setupStep" | "onboardingStep" | "forecast" | "forecastFlag" | "accountPurpose" | "cadence" | "explainStep"
  | "loanField" | "loanOption" | "loanVerdict" | "rentalDocument" | "rentalDocumentFrom";

// untyped access: the codes come from the server at run time, so they cannot be checked against the key types
const T = i18n as unknown as { exists: (k: string, o: object) => boolean; t: (k: string, o: object) => string };
/** The key, or its plural forms (`key_one` / `key_other`), is in the server namespace. */
function has(key: string): boolean {
  return T.exists(key, { ns: "server" }) || T.exists(`${key}_other`, { ns: "server" });
}
function tr(key: string, vars: Record<string, unknown> = {}): string {
  return T.t(key, { ns: "server", ...vars });
}

/** A param's display value, from its name: `*_date` -> date, `*_month` -> month, `*_amount` -> money, `*_pct` -> percent,
 *  `*_category` / `*_group` -> the category's name, `cadence` -> its label (`labels.cadence.<code>`); `count` stays a number (it picks the
 *  plural form); anything else as it is (a key may format a number itself: `{{excess_km, number}}`). */
export function formatParam(name: string, v: ServerParam): string | number {
  if (v === null || v === undefined) return "–";
  if (name === "count") return v;
  if (name === "cadence") return serverLabel("cadence", String(v), String(v));
  if (name.endsWith("_date")) return fmtDate(String(v));
  if (name.endsWith("_month")) return fmtMonth(String(v), "long");
  if (name.endsWith("_amount")) return fmtMoney(v);
  if (name.endsWith("_pct")) return fmtPct(Number(v));
  if (name.endsWith("_num")) {
    // a plain decimal in the reader's format, with the decimals the server sent ("2.40" stays two decimals, 2.4 one)
    const s = String(v);
    const n = Number(s);
    return Number.isFinite(n) ? fmtNumber(n, s.includes(".") ? s.length - s.indexOf(".") - 1 : 0) : s;
  }
  if (name.endsWith("_category")) return catLabel(String(v));
  if (name.endsWith("_group")) return groupLabel(String(v));
  return v;
}

export function formatParams(params: Record<string, ServerParam> | undefined): Record<string, string | number> {
  return Object.fromEntries(Object.entries(params ?? {}).map(([k, v]) => [k, formatParam(k, v)]));
}

/** The sentence in the interface language: `server:<code>` with the formatted params, or the English text for an unknown code. */
export function tServer(msg: ServerMsg | null | undefined, fallback = ""): string {
  if (!msg) return fallback;
  if (!msg.code || !has(msg.code)) return msg.text ?? fallback;
  return tr(msg.code, formatParams(msg.params));
}

/** The web knows this server code (a key of the `server` namespace, or its plural forms). */
export function knowsCode(code: string | null | undefined): boolean {
  return !!code && has(code);
}

/** The title and the body of an insight card or an alert event: each translated from its `*_msg` when the web knows the code, else the
 *  English (through `legacy`). A body sent with a `disclaimer` key (its English already ends with the text) gets the text of that key in the
 *  interface language (`useDisclaimers()`: the wording lives only in src/coach/disclaimers.py). */
export function cardText(
  item: { title: string; body: string; title_msg?: ServerMsg | null; body_msg?: ServerMsg | null },
  opts: { legacy?: (s: string) => string; disclaimer?: unknown; disclaimers?: Record<string, string | undefined> } = {},
): { title: string; body: string } {
  const title = tServerOr(item.title_msg, item.title, opts.legacy);
  let body = tServerOr(item.body_msg, item.body, opts.legacy);
  if (knowsCode(item.body_msg?.code) && typeof opts.disclaimer === "string") {
    const legal = opts.disclaimers?.[opts.disclaimer];
    // never without it: until the text in the interface language is loaded, the English body (it ends with the English text)
    body = legal ? `${body} ${legal}` : opts.legacy ? opts.legacy(item.body) : item.body;
  }
  return { title, body };
}

/** A sentence that may come with its message: the translation when the web knows the code, else the English `text` passed through
 *  `legacy` (e.g. the `humanize` re-rendering of the ids, dates and amounts of an older English-only text). */
export function tServerOr(msg: ServerMsg | null | undefined, text: string, legacy?: (s: string) => string): string {
  if (msg && msg.code && has(msg.code)) return tServer(msg, text);
  return legacy ? legacy(text) : text;
}

/** A list of English sentences and their `<field>_msg` siblings (same order; an entry may be null, the server may be older and send
 *  none): each sentence in the interface language, the English where there is no message. Used for the coverage notes. */
export function tServerList(texts: readonly string[] | null | undefined, msgs?: readonly (ServerMsg | null)[] | null): string[] {
  return (texts ?? []).map((text, i) => tServer(msgs?.[i], text));
}

/** A label of a fixed vocabulary: `labels.<family>.<code>`, else the English label the server sent, else the code itself. */
export function serverLabel(family: LabelFamily, code: string | null | undefined, english?: string | null): string {
  if (!code) return english ?? "";
  const key = `labels.${family}.${code}`;
  return has(key) ? tr(key) : (english ?? code);
}

/** A flag: a code, or `code:value` when it carries one value (`accounts_without_balance:2`); the value is `{{value}}` and, when it is a
 *  number, `{{count}}` (plural forms). An unknown flag is shown as it is. */
export function flagLabel(family: LabelFamily, flag: string): string {
  const i = flag.indexOf(":");
  const code = i < 0 ? flag : flag.slice(0, i);
  const value = i < 0 ? undefined : flag.slice(i + 1);
  const key = `labels.${family}.${code}`;
  if (!has(key)) return flag;
  const n = value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : undefined;
  return tr(key, { value, ...(n !== undefined ? { count: n } : {}) });
}

/** An account purpose (main, cards, rental, kids, savings); any other value title-cased. */
export function purposeLabel(purpose: string): string {
  return serverLabel("accountPurpose", purpose, groupLabel(purpose));
}

/** The kind of an asset, a loan or a contract of the memory (`employee_savings_plan`, `mortgage`, `insurance_home`): the item form's own
 *  option labels (common namespace), else the code title-cased. Also what the net worth's generic names (loans/networth.py) say. */
export function holdingKindLabel(kind: string | null | undefined): string {
  if (!kind) return "";
  const camel = kind.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
  for (const key of [`itemForm.option.assetKind.${kind}`, `itemForm.option.liabilityKind.${kind}`, `itemForm.option.contractKind.${camel}`]) {
    if (T.exists(key, { ns: "common" })) return T.t(key, { ns: "common" });
  }
  return groupLabel(kind);
}

/** The forecast line of an account, or of the whole household (`account: null`). */
export function forecastLabel(f: { account: string | null; label: string }): string {
  return f.account === null ? serverLabel("forecast", "household", f.label) : f.label;
}

/** What to tell the person about a failed request: the translation of its `code` (`error.<code>`) for the generic errors,
 *  else the server's own message (it may explain a domain rule), else `fallback`. */
export function errorText(e: unknown, fallback = ""): string {
  if (e instanceof ApiError) return has(`error.${e.code}`) ? tr(`error.${e.code}`) : e.message || fallback;
  if (e instanceof Error) return e.message || fallback;
  return fallback;
}

/** The same helpers, re-rendering the component when the language changes. */
export function useServerText() {
  const { i18n: inst } = useTranslation("server");
  return { tServer, tServerOr, cardText, tServerList, serverLabel, flagLabel, errorText, forecastLabel, language: inst.resolvedLanguage ?? inst.language };
}
