import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  availableSorts,
  badgeOf,
  matchesFilters,
  NO_FILTERS,
  normalize,
  parseFilters,
  parseSort,
  parseView,
  relevance,
  sortItems,
} from "@/components/catalog/model";
import { item, SERVER, UNKNOWN } from "@/components/catalog/fixtures";

const proof = { official: false, vendor_org: null, repo_owner: null, from_anthropic: false, library: false };

describe("badgeOf", () => {
  it("gives the vendor badge only when the pinned repo is owned by the vendor's organisation", () => {
    expect(badgeOf(item({ proof: { ...proof, official: true, vendor_org: "supabase", repo_owner: "Supabase" } }))).toBe(
      "vendor",
    );
  });

  it("refuses the vendor badge on the curated flag alone", () => {
    // The flag is a claim; the owner of the repository is the check.
    expect(badgeOf(item({ proof: { ...proof, official: true, vendor_org: "supabase", repo_owner: "someone" } }))).toBe(null);
    expect(badgeOf(item({ proof: { ...proof, official: true, vendor_org: "supabase", repo_owner: null } }))).toBe(null);
  });

  it("marks Anthropic and the Chimera library, and nothing else", () => {
    expect(badgeOf(item({ proof: { ...proof, from_anthropic: true } }))).toBe("anthropic");
    expect(badgeOf(item({ proof: { ...proof, library: true } }))).toBe("chimera");
    expect(badgeOf(item())).toBe(null);
  });
});

describe("the URL", () => {
  it("drops filter values it does not know rather than emptying the screen", () => {
    const f = parseFilters(new URLSearchParams("runs=text,bogus,text&license=nope&state=active&cat=dev,ai"));
    expect(f).toEqual({ cat: ["dev", "ai"], runs: ["text"], license: [], state: ["active"] });
  });

  it("falls back to Mine and to no sort on values it does not know", () => {
    expect(parseView(new URLSearchParams("view=elsewhere"))).toBe("mine");
    expect(parseView(new URLSearchParams("view=discover"))).toBe("discover");
    expect(parseSort(new URLSearchParams("sort=installs"))).toBe(null);
    expect(parseSort(new URLSearchParams("sort=stars"))).toBe("stars");
  });
});

describe("matchesFilters", () => {
  const unknown = item({ capabilities: UNKNOWN });

  it("lets 'we don't know yet' match only itself", () => {
    // A package we have not downloaded counted zero servers because nobody looked, which is not
    // the same as being instructions only.
    for (const runs of [["text"], ["process"], ["network"], ["inert"]] as const)
      expect(matchesFilters(unknown, { ...NO_FILTERS, runs: [...runs] })).toBe(false);
    expect(matchesFilters(unknown, { ...NO_FILTERS, runs: ["unknown"] })).toBe(true);
    expect(matchesFilters(item(), { ...NO_FILTERS, runs: ["unknown"] })).toBe(false);
  });

  it("keeps an unread licence out of 'other'", () => {
    const unread = item({ license: null });
    expect(matchesFilters(unread, { ...NO_FILTERS, license: ["other"] })).toBe(false);
    expect(matchesFilters(unread, { ...NO_FILTERS, license: ["unread"] })).toBe(true);
  });

  it("ORs within a group and ANDs across groups", () => {
    const server = item({ capabilities: SERVER, category: "databases" });
    expect(matchesFilters(server, { ...NO_FILTERS, runs: ["text", "network"] })).toBe(true);
    expect(matchesFilters(server, { ...NO_FILTERS, runs: ["network"], cat: ["dev"] })).toBe(false);
  });

  it("answers the state filter, with 'none' for not installed", () => {
    expect(matchesFilters(item(), { ...NO_FILTERS, state: ["none"] })).toBe(true);
    expect(matchesFilters(item({ status: "held" }), { ...NO_FILTERS, state: ["held"] })).toBe(true);
    expect(matchesFilters(item({ status: "denied" }), { ...NO_FILTERS, state: ["active", "none"] })).toBe(false);
  });
});

describe("search", () => {
  it("ignores accents and case", () => {
    expect(normalize("Análise ÇÃO")).toBe("analise cao");
    expect(relevance(item({ name: "Análise de dados" }), "ANALISE")).toBe(0);
  });

  it("ranks the name above author and keywords, and those above the description", () => {
    expect(relevance(item({ name: "pdf tools" }), "pdf")).toBe(0);
    expect(relevance(item({ name: "my pdf" }), "pdf")).toBe(1);
    expect(relevance(item({ keywords: ["pdf"] }), "pdf")).toBe(2);
    expect(relevance(item({ description: "reads a PDF" }), "pdf")).toBe(3);
    expect(relevance(item(), "pdf")).toBe(null);
  });
});

describe("sortItems", () => {
  it("puts items without star data at the end, never as zero", () => {
    const zero = item({ name: "zero", stars: 0 });
    const none = item({ name: "aaa-none", stars: null });
    const many = item({ name: "many", stars: 2100 });
    const { ranked, unranked } = sortItems([none, zero, many], "stars", "en");
    expect(ranked.map((i) => i.name)).toEqual(["many", "zero"]);
    expect(unranked.map((i) => i.name)).toEqual(["aaa-none"]);
  });

  it("sorts by the newest date, undated last", () => {
    const old = item({ name: "old", added_at: "2026-01-01" });
    const fresh = item({ name: "fresh", updated_at: "2026-10-01" });
    const undated = item({ name: "undated" });
    const { ranked, unranked } = sortItems([old, undated, fresh], "recent", "en");
    expect(ranked.map((i) => i.name)).toEqual(["fresh", "old"]);
    expect(unranked.map((i) => i.name)).toEqual(["undated"]);
  });

  it("sorts by name in the language's order, and by relevance with a query", () => {
    const names = ["beta", "Alpha", "über"].map((name) => item({ name }));
    expect(sortItems(names, "name", "en").ranked.map((i) => i.name)).toEqual(["Alpha", "beta", "über"]);
    const hits = [item({ name: "notes", description: "pdf" }), item({ name: "pdf" })];
    expect(sortItems(hits, "relevance", "en", "pdf").ranked.map((i) => i.name)).toEqual(["pdf", "notes"]);
  });

  it("offers recent and stars only when some item carries the data", () => {
    expect(availableSorts([item()], false)).toEqual(["name"]);
    expect(availableSorts([item({ stars: 1 }), item({ added_at: "2026-01-01" })], true)).toEqual([
      "relevance",
      "name",
      "recent",
      "stars",
    ]);
  });
});

describe("no install counts", () => {
  it("has no field that counts installs, downloads or users", () => {
    // Nothing on this machine measures one. The day a field like this appears, someone will fill
    // it with an estimate, on the screen where a person decides what to trust.
    const source = readFileSync(join(__dirname, "model.ts"), "utf8");
    const fields = [...source.matchAll(/^\s+(\w+)\??:/gm)].map((m) => m[1].toLowerCase());
    expect(fields.length).toBeGreaterThan(10); // the scan sees the interfaces at all
    expect(fields.filter((f) => /install_?count|installs|downloads|users/.test(f))).toEqual([]);
  });
});
