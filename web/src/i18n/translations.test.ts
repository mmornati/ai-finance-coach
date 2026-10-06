import { describe, expect, it } from "vitest";
import { LANGUAGES, FALLBACK_LANGUAGE } from "./languages";

// Every language of the registry must carry exactly the keys of English, in every namespace. Plural forms are compared by their base key,
// with the categories the language needs. Run by `pnpm test`: a forgotten or empty translation fails the build, not the user.

const files = import.meta.glob<Record<string, unknown>>("../locales/*/*.json", { eager: true, import: "default" });

type Bundle = Record<string, string>;
const bundles: Record<string, Record<string, Bundle>> = {}; // language -> namespace -> flattened key -> value
for (const [path, json] of Object.entries(files)) {
  const [, lang, ns] = /\.\.\/locales\/([^/]+)\/([^/]+)\.json$/.exec(path)!;
  (bundles[lang] ??= {})[ns] = flatten(json);
}

function flatten(o: unknown, prefix = ""): Bundle {
  const out: Bundle = {};
  for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object") Object.assign(out, flatten(v, key));
    else out[key] = String(v);
  }
  return out;
}

const PLURAL = /^(.*)_(zero|one|two|few|many|other)$/;
const CLDR = ["zero", "one", "two", "few", "many", "other"];
function split(key: string): { base: string; cat: string | null } {
  const m = PLURAL.exec(key);
  return m ? { base: m[1], cat: m[2] } : { base: key, cat: null };
}
/** the plural base keys of a bundle with their categories, and its plain keys */
function shape(b: Bundle) {
  const plural = new Map<string, Set<string>>();
  const plain = new Set<string>();
  for (const key of Object.keys(b)) {
    const { base, cat } = split(key);
    if (cat === null) plain.add(key);
    else (plural.get(base) ?? plural.set(base, new Set()).get(base)!).add(cat);
  }
  return { plural, plain };
}
const placeholders = (s: string) => [...new Set([...s.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g)].map((m) => m[1]))].sort();
const tags = (s: string) => [...new Set([...s.matchAll(/<\/?\s*([A-Za-z][\w-]*)/g)].map((m) => m[1]))].sort();
/** categories a language needs, from the platform's own rules; "one" and "other" are always required (they exist in every CLDR version) */
const needed = (lang: string) => new Set(["one", "other", ...new Intl.PluralRules(lang).resolvedOptions().pluralCategories]);

const english = bundles[FALLBACK_LANGUAGE];

describe("the translation files", () => {
  it("have a folder for every language of the registry, and only those", () => {
    expect(Object.keys(bundles).sort()).toEqual(LANGUAGES.map((l) => l.code).sort());
  });

  it("have the same namespaces in every language", () => {
    for (const l of LANGUAGES) expect(Object.keys(bundles[l.code] ?? {}).sort(), l.code).toEqual(Object.keys(english).sort());
  });

  it("have a native name and an Intl locale that exist, once per language", () => {
    expect(new Set(LANGUAGES.map((l) => l.code)).size).toBe(LANGUAGES.length);
    for (const l of LANGUAGES) {
      expect(l.name.length).toBeGreaterThan(0);
      expect(Intl.NumberFormat.supportedLocalesOf(l.locale)).toEqual([l.locale]);
    }
  });

  it("English itself has the plural forms English needs", () => {
    for (const [ns, b] of Object.entries(english)) {
      for (const [base, cats] of shape(b).plural) for (const c of needed("en")) expect(cats.has(c), `en/${ns}: ${base}_${c}`).toBe(true);
    }
  });

  for (const lang of LANGUAGES.filter((l) => l.code !== FALLBACK_LANGUAGE)) {
    describe(lang.code, () => {
      for (const [ns, en] of Object.entries(english)) {
        const tr = bundles[lang.code]?.[ns] ?? {};
        const enShape = shape(en);
        const trShape = shape(tr);

        it(`${ns}: has exactly the keys of English`, () => {
          expect([...trShape.plain].sort()).toEqual([...enShape.plain].sort());
          expect([...trShape.plural.keys()].sort()).toEqual([...enShape.plural.keys()].sort());
        });

        it(`${ns}: has the plural forms the language needs`, () => {
          for (const [base, cats] of trShape.plural) {
            for (const c of needed(lang.code)) expect(cats.has(c), `${lang.code}/${ns}: ${base}_${c}`).toBe(true);
            for (const c of cats) expect(CLDR, `${base}_${c}`).toContain(c);
          }
        });

        it(`${ns}: has no empty value`, () => {
          for (const [k, v] of Object.entries(tr)) expect(v.trim(), `${lang.code}/${ns}: ${k}`).not.toBe("");
        });

        it(`${ns}: keeps every {{placeholder}} and every <tag> of English`, () => {
          for (const [key, value] of Object.entries(tr)) {
            const { base, cat } = split(key);
            // a plural form English does not have (fr / it "many") is compared with English "other"
            const source = en[key] ?? en[`${base}_other`] ?? "";
            if (cat === null && en[key] === undefined) continue; // reported by the key test
            expect(placeholders(value), `${lang.code}/${ns}: ${key}`).toEqual(placeholders(source));
            expect(tags(value), `${lang.code}/${ns}: ${key} (tags)`).toEqual(tags(source));
          }
        });
      }
    });
  }
});
