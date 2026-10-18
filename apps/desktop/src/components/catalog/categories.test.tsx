/**
 * The category table against the data it has to file. The bundled catalogues are read from the
 * repository itself, not copied into the test: a topic added to `catalog.json` tomorrow that this
 * table does not know fails here, instead of quietly landing in `other` with no tile.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CATEGORY_IDS,
  CATEGORY_LABEL_KEY,
  canonicalCategory,
  categoryOfItem,
  mcpCatalogCategory,
  OTHER_CATEGORY,
} from "@/components/catalog/categories";
import { CategoryIllustration } from "@/components/catalog/illustrations";
import { DICTS, LANGS } from "@/lib/i18n";

const ROOT = join(__dirname, "..", "..", "..", "..", "..");
const read = (...parts: string[]) => readFileSync(join(ROOT, ...parts), "utf8");

/** `topic` of every skill in the bundled third-party catalogue. */
function catalogTopics(): string[] {
  const data = JSON.parse(read("chimera", "skills", "catalog.json")) as { skills: { topic?: string }[] };
  return data.skills.map((s) => s.topic ?? "").filter(Boolean);
}

/** The library's topic vocabulary (`TOPICS = (...)` in skill_md.py) and what its cards use. */
function libraryTopics(): string[] {
  const source = read("chimera", "skills", "skill_md.py");
  const tuple = /^TOPICS = \(([\s\S]*?)^\)/m.exec(source)?.[1] ?? "";
  const vocab = [...tuple.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  const dir = join(ROOT, "skills");
  const used = existsSync(dir)
    ? readdirSync(dir)
        .map((name) => join(dir, name, "SKILL.md"))
        .filter((p) => existsSync(p))
        .flatMap((p) => [.../^topic:\s*(\S+)/m.exec(readFileSync(p, "utf8"))?.slice(1) ?? []])
    : [];
  return [...vocab, ...used];
}

/** Ids of the curated MCP catalogue: the literal ones, and the `db-<x>` the database rows build. */
function mcpIds(): string[] {
  const source = read("chimera", "integrations", "mcp_catalog.py");
  const literal = [...source.matchAll(/^\s+id="([^"]+)"/gm)].map((m) => m[1]);
  const dbs = /_ALCHEMY_DBS[^=]*= \(([\s\S]*?)^\)/m.exec(source)?.[1] ?? "";
  const db = [...dbs.matchAll(/^\s+\("([^"]+)"/gm)].map((m) => `db-${m[1]}`);
  return [...literal, ...db];
}

describe("category ids", () => {
  it("are the 26 the backend and the public catalogues share, in the same order", () => {
    const source = read("chimera", "catalogs", "categories.py");
    const tuple = /CATEGORY_IDS: tuple\[str, \.\.\.\] = \(([\s\S]*?)^\)/m.exec(source)?.[1] ?? "";
    expect([...tuple.matchAll(/"([^"]+)"/g)].map((m) => m[1])).toEqual([...CATEGORY_IDS]);
    expect(CATEGORY_IDS).toHaveLength(26);
  });

  it("each has a label in every language and a drawing", () => {
    for (const id of CATEGORY_IDS) {
      for (const lang of LANGS) expect(DICTS[lang.code][CATEGORY_LABEL_KEY[id]], `${lang.code}:${id}`).toBeTruthy();
      const { container, unmount } = render(<CategoryIllustration id={id} />);
      const svg = container.querySelector("svg");
      expect(svg?.getAttribute("aria-hidden")).toBe("true");
      expect(svg?.getAttribute("stroke")).toBe("currentColor");
      expect(svg?.getAttribute("fill")).toBe("none");
      expect(svg?.childElementCount, id).toBeGreaterThan(1);
      // No colour of its own anywhere inside: the tile decides the ink.
      for (const el of svg?.querySelectorAll("*") ?? []) {
        expect(el.getAttribute("stroke") ?? "currentColor", id).toBe("currentColor");
        expect(el.getAttribute("fill") ?? "none", id).toBe("none");
      }
      unmount();
    }
  });
});

describe("canonicalCategory", () => {
  it("files every topic and category the bundled catalogues carry today — none falls to other", () => {
    const topics = [...new Set([...catalogTopics(), ...libraryTopics()])];
    // The scan sees the data at all: an empty list would pass anything.
    expect(topics.length).toBeGreaterThan(15);
    expect(topics.filter((t) => canonicalCategory(t) === OTHER_CATEGORY)).toEqual([]);
  });

  it("files every curated MCP entry by its id — none falls to other", () => {
    const ids = mcpIds();
    expect(ids).toEqual(expect.arrayContaining(["github", "supabase", "db-postgres"]));
    expect(ids.length).toBeGreaterThan(10);
    expect(ids.filter((id) => mcpCatalogCategory(id) === OTHER_CATEGORY)).toEqual([]);
  });

  it("files the two kinds the owner adds by hand", () => {
    expect(canonicalCategory("mcp")).toBe("apis-integrations");
    expect(canonicalCategory("openapi")).toBe("apis-integrations");
  });

  it("keeps a shared id as itself, folds case, accents and spacing, and sends the unknown to other", () => {
    for (const id of CATEGORY_IDS) expect(canonicalCategory(id)).toBe(id);
    expect(canonicalCategory("Código")).toBe("code");
    expect(canonicalCategory("Code Review")).toBe("pull-requests");
    expect(canonicalCategory("  DATABASES ")).toBe("databases");
    expect(canonicalCategory("something-new")).toBe(OTHER_CATEGORY);
    expect(canonicalCategory("")).toBe(OTHER_CATEGORY);
    expect(canonicalCategory(null)).toBe(OTHER_CATEGORY);
  });

  it("prefers the adapter's own answer to the raw label", () => {
    expect(categoryOfItem({ category: "mcp", canonicalCategory: "databases" })).toBe("databases");
    expect(categoryOfItem({ category: "mcp", canonicalCategory: "not-an-id" })).toBe("apis-integrations");
    expect(categoryOfItem({ category: "github" })).toBe("pull-requests");
  });
});
