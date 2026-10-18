import type { CatalogEntry, LibraryCard, PinnedCatalog, PinnedCatalogItem, SkillBundle, SkillStat } from "@/lib/types";
import { skillId, type SkillSources } from "@/components/skills/skillItems";

/** What a card or an `item=` in the URL stands for, with the raw record its controls act on. */
export type SkillTarget =
  | { kind: "learned"; stat: SkillStat; candidate: boolean }
  | { kind: "library"; card: LibraryCard }
  | { kind: "entry"; entry: CatalogEntry; bundle?: SkillBundle }
  | { kind: "bundle"; bundle: SkillBundle }
  | { kind: "pinned"; item: PinnedCatalogItem; catalog: PinnedCatalog };

/**
 * Every item id the screen can draw, resolved to its record. An installed bundle that came from the
 * third-party catalogue resolves to that entry, so its panel is the one with the portability verdict,
 * the source link and the update check — the same panel whichever tab opened it.
 */
export function targetsOf(src: SkillSources, candidates: ReadonlySet<string>): Map<string, SkillTarget> {
  const out = new Map<string, SkillTarget>();
  const bundleByName = new Map(src.bundles.map((b) => [b.name ?? "", b] as const));
  const entryByName = new Map(src.entries.map((e) => [e.name ?? "", e] as const));
  for (const stat of src.stats)
    out.set(skillId.learned(stat.name), { kind: "learned", stat, candidate: candidates.has(stat.name) });
  for (const card of src.library) out.set(skillId.library(card.name), { kind: "library", card });
  for (const entry of src.entries) {
    const bundle = entry.installed ? bundleByName.get(entry.name ?? "") : undefined;
    out.set(skillId.catalog(entry.name ?? ""), { kind: "entry", entry, bundle });
  }
  for (const bundle of src.bundles) {
    const entry = entryByName.get(bundle.name ?? "");
    out.set(
      skillId.bundle(bundle.name ?? ""),
      entry && bundle.origin !== "upload" ? { kind: "entry", entry, bundle } : { kind: "bundle", bundle },
    );
  }
  if (src.pinned)
    for (const item of src.pinned.items ?? [])
      out.set(skillId.pinned(item.id), { kind: "pinned", item, catalog: src.pinned });
  return out;
}

