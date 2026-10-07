import { allOfGroupLabel, catLabel, fmtDate, fmtMoney, fmtMonth } from "./format";

/** The server writes insight texts in plain English with machine tokens in them (category ids, ISO dates and months,
 *  "12.50 EUR"). This only re-renders those tokens for the reader (labels, formatted dates and money): presentation, no figure
 *  is computed or changed. */
export function humanize(text: string, categories?: Set<string>): string {
  let out = text;
  out = out.replace(/\bgroup:([a-z_]+)\b/g, (_, g: string) => allOfGroupLabel(g));
  out = out.replace(/\b[a-z_]+\.[a-z_]+\b/g, (m) => (!categories || categories.has(m) ? catLabel(m) : m));
  out = out.replace(/(-?\d+\.\d{2}) EUR\b/g, (_, n: string) => fmtMoney(n));
  out = out.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (m) => fmtDate(m, "medium"));
  out = out.replace(/\b(\d{4}-(?:0[1-9]|1[0-2]))\b(?!-\d)/g, (m) => fmtMonth(m, "long"));
  return out;
}
