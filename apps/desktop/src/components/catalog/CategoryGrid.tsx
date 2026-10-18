import { useId, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { focusRing, groupFocusInk } from "@/components/ui/focus";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { GRID } from "@/components/catalog/CatalogSection";
import { CATEGORY_IDS, CATEGORY_LABEL_KEY, categoryOfItem, type CategoryId } from "@/components/catalog/categories";
import { CategoryIllustration } from "@/components/catalog/illustrations";
import type { CatalogItem } from "@/components/catalog/model";

/** Tiles drawn before "Show all": four rows of two. */
export const COLLAPSED_TILES = 8;

/** How many of `items` file under each category, in grid order, leaving out the empty ones. */
export function categoryCounts(items: readonly CatalogItem[]): { id: CategoryId; count: number }[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    const id = categoryOfItem(item);
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return CATEGORY_IDS.flatMap((id) => {
    const count = counts.get(id) ?? 0;
    return count > 0 ? [{ id, count }] : [];
  });
}

/**
 * The category grid at the foot of Discover (study 04, §4.1).
 *
 * `items` are the ones the screen would show with every choice applied except the category itself,
 * so a tile's number is what clicking it would list — never a total of the catalogue, and never a
 * count of anything nobody here measured. A category with nothing in it has no tile. Clicking one
 * hands its id to the screen, which puts it in the URL's `cat=`: the same filter the menu ticks, so
 * the chip, the menu and a pasted link all agree. Clicking the pressed tile again clears it.
 *
 * Collapsed it draws eight tiles and "Show all N"; the tiles it adds fade in, and nothing animates
 * height. A pressed tile past the eighth is drawn anyway — the filter in force is never hidden
 * behind a toggle.
 */
export function CategoryGrid({
  items,
  active,
  onSelect,
  className,
}: {
  items: readonly CatalogItem[];
  /** The `cat=` values in force. */
  active: readonly string[];
  onSelect: (id: CategoryId | null) => void;
  className?: string;
}) {
  const { t } = useI18n();
  const headingId = useId();
  const gridId = useId();
  const [open, setOpen] = useState(false);
  const tiles = useMemo(() => categoryCounts(items), [items]);
  if (tiles.length === 0) return null;

  const shown = open ? tiles : tiles.filter((tile, i) => i < COLLAPSED_TILES || active.includes(tile.id));
  return (
    <section aria-labelledby={headingId} className={cn("space-y-3", className)}>
      <h3 id={headingId} className="text-sm font-semibold">
        {t("catalogUi.categories.title")}
      </h3>
      <ul id={gridId} role="list" className={GRID}>
        {shown.map(({ id, count }) => {
          const name = t(CATEGORY_LABEL_KEY[id]);
          const pressed = active.includes(id);
          const revealed = open && tiles.findIndex((tile) => tile.id === id) >= COLLAPSED_TILES;
          return (
            <li key={id} className={revealed ? "fade-enter" : undefined}>
              <button
                type="button"
                aria-pressed={pressed}
                aria-label={`${name}, ${t("catalogUi.count", { n: count })}`}
                onClick={() => onSelect(pressed ? null : id)}
                className={cn(
                  "surface group flex w-full items-center gap-4 p-4 text-left transition-colors duration-1 ease-out hover:bg-surface-hover",
                  pressed && "ring-1 ring-accent/40",
                  focusRing,
                )}
              >
                <CategoryIllustration
                  id={id}
                  className={cn(
                    "transition-colors duration-1 ease-out group-hover:text-accent-ink",
                    groupFocusInk,
                    pressed ? "text-accent-ink" : "text-muted-foreground",
                  )}
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{name}</span>
                  <span className="block text-xs text-muted-foreground">{count}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {tiles.length > COLLAPSED_TILES && (
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={open}
          aria-controls={gridId}
          // The button stays where it is and keeps focus either way, so "Show less" returns a
          // keyboard user exactly where they pressed it.
          onClick={() => setOpen((o) => !o)}
        >
          {open ? t("catalogUi.categories.showLess") : t("catalogUi.categories.showAll", { n: tiles.length })}
          <ChevronDown
            aria-hidden
            className={cn("h-4 w-4 transition-transform duration-2 ease-out", open && "rotate-180")}
          />
        </Button>
      )}
    </section>
  );
}
