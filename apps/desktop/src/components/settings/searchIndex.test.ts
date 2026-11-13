import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { SEARCH_INDEX, type SearchEntry } from "@/components/settings/searchIndex";
import { DICTS } from "@/lib/i18n";

/**
 * The Settings search finds every row Settings draws.
 *
 * The index is written by hand, which is how it came to cover 56 of roughly 110 rows: a row added
 * without an entry is simply unfindable, and a search that finds nothing looks the same as a setting
 * that does not exist. The row labels are read out of the screen's own source, so adding one without
 * an entry fails here.
 */

const COMPONENTS = join(__dirname, "..");

/** Settings.tsx and every non-test file of this folder: where Settings draws its rows. */
function settingsSources(): string[] {
  const files = [join(COMPONENTS, "Settings.tsx")];
  for (const entry of readdirSync(__dirname)) {
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && entry !== "searchIndex.ts") {
      files.push(join(__dirname, entry));
    }
  }
  return files.map((file) => readFileSync(file, "utf8"));
}

/** Every non-test source under components/: a card the search points at may be drawn by another file. */
function allComponentSources(dir: string = COMPONENTS, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) allComponentSources(full, out);
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(readFileSync(full, "utf8"));
  }
  return out;
}

/** The `settings.row.*` keys that label a `<Row>` in these sources. */
export function rowLabelKeys(sources: readonly string[]): Set<string> {
  const keys = new Set<string>();
  for (const source of sources) {
    for (const match of source.matchAll(/<Row\b[^>]{0,400}?\blabel=\{t\(\s*"(settings\.row\.[\w.]+)"/g)) {
      keys.add(match[1]);
    }
  }
  return keys;
}

/** The row labels a given index leaves out. */
export function uncovered(keys: ReadonlySet<string>, index: readonly Pick<SearchEntry, "label">[]): string[] {
  const covered = new Set(index.map((entry) => entry.label));
  return [...keys].filter((key) => !covered.has(key)).sort();
}

describe("the Settings search index", () => {
  it("really finds the rows in the sources", () => {
    // A regex that quietly stopped matching would make the coverage test below pass on nothing.
    const keys = rowLabelKeys(settingsSources());
    expect(keys.size).toBeGreaterThan(90);
    expect(keys.has("settings.row.defaultModel")).toBe(true);
  });

  it("has an entry for every row Settings draws", () => {
    const missing = uncovered(rowLabelKeys(settingsSources()), SEARCH_INDEX);
    expect(missing, `rows with no entry in settings/searchIndex.ts:\n${missing.join("\n")}`).toEqual([]);
  });

  it("fails when a row is added without an entry", () => {
    // The same check against a source that gained a row, and against the index as it stood.
    const withNewRow = `${settingsSources().join("\n")}\n<Row label={t("settings.row.aBrandNewRow")} hint="x">`;
    expect(uncovered(rowLabelKeys([withNewRow]), SEARCH_INDEX)).toContain("settings.row.aBrandNewRow");
  });

  it("covers every tab", () => {
    const tabs = new Set(SEARCH_INDEX.map((entry) => entry.tab));
    for (const tab of [
      "general",
      "appearance",
      "skills",
      "connectors",
      "plugins",
      "connections",
      "usage",
      "security",
      "decisions",
    ]) {
      expect(tabs.has(tab as SearchEntry["tab"]), tab).toBe(true);
    }
  });

  it("names only keys the dictionary has, and anchors that Settings draws", () => {
    const sources = allComponentSources().join("\n");
    for (const entry of SEARCH_INDEX) {
      for (const key of [entry.label, entry.hint, entry.terms]) {
        if (key) expect(DICTS.en[key], `${key} is not in the dictionary`).toBeTruthy();
      }
      if (entry.anchor) {
        // A messaging card's id is built from the platform at run time, so its prefix is what to find.
        const stem = entry.anchor.replace(/-(discord|telegram)$/, "-");
        expect(
          sources.includes(entry.anchor) || sources.includes(stem),
          `anchor ${entry.anchor} is not drawn by Settings`,
        ).toBe(true);
      }
    }
  });

  it("marks an Advanced result as such, so the closed block is opened first", () => {
    const advancedCards = SEARCH_INDEX.filter((entry) => entry.anchor?.startsWith("settings-card-pools"));
    expect(advancedCards.length).toBeGreaterThan(0);
    for (const entry of advancedCards) expect(entry.advanced).toBe(true);
  });
});
