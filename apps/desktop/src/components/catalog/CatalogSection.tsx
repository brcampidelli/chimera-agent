import { forwardRef, useId, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The grid every section uses: one column in a narrow container, two from `@xl` (36rem). Never
 *  three — a two-line description is unreadable at ~280px. The container is the screen's root, not
 *  the window, because Settings puts its section list beside the content from `lg`. */
export const GRID = "grid gap-3 @xl:grid-cols-2";

/**
 * A titled group of cards: `<section aria-labelledby>` with an `h3`, a count, the grid, and
 * "Show all" when the caller cut the list short. The title is focusable from script so the screen
 * can move focus to it when it switches into or out of the section's full view.
 */
export const CatalogSection = forwardRef<
  HTMLHeadingElement,
  {
    title: string;
    /** The total the section holds — not just the cards drawn. */
    count?: number;
    onShowAll?: () => void;
    children: ReactNode;
    /** After the ranked cards, under a label: items the chosen sort has no data for. */
    tail?: { label: string; children: ReactNode } | null;
  }
>(function CatalogSection({ title, count, onShowAll, children, tail }, ref) {
  const { t } = useI18n();
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-3">
      <div className="flex items-baseline gap-2">
        <h3 ref={ref} id={id} tabIndex={-1} className="text-sm font-semibold outline-hidden">
          {title}
        </h3>
        {count !== undefined && (
          <span className="text-xs text-muted-foreground">{t("catalogUi.count", { n: count })}</span>
        )}
        {onShowAll && (
          <button
            type="button"
            onClick={onShowAll}
            aria-label={t("catalogUi.showAllOf", { section: title })}
            className={cn("ml-auto inline-flex items-center gap-1 rounded-md text-xs text-accent-ink hover:underline", focusRing)}
          >
            {t("catalogUi.showAll")}
            <ArrowRight aria-hidden className="h-3 w-3" />
          </button>
        )}
      </div>
      <ul role="list" className={GRID}>
        {children}
      </ul>
      {tail && (
        <>
          <p className="border-t border-hairline pt-3 text-xs text-muted-foreground">{tail.label}</p>
          <ul role="list" aria-label={tail.label} className={GRID}>
            {tail.children}
          </ul>
        </>
      )}
    </section>
  );
});

/** Six still placeholders. No shimmer: a looping sheen is ambient animation, and under reduced
 *  motion it would be static anyway, so the static one is the design. */
export function CatalogSkeleton({ cards = 6 }: { cards?: number }) {
  const { t } = useI18n();
  return (
    <div>
      <p role="status" className="sr-only">
        {t("catalogUi.loading")}
      </p>
      <ul role="list" aria-busy="true" className={GRID}>
        {Array.from({ length: cards }, (_, i) => (
          <li key={i} aria-hidden className="surface flex gap-3 p-4">
            <span className="h-10 w-10 shrink-0 rounded-lg bg-surface-2" />
            <span className="flex-1 space-y-2">
              <span className="block h-3 w-1/3 rounded-md bg-surface-2" />
              <span className="block h-3 w-full rounded-md bg-surface-2" />
              <span className="block h-3 w-2/3 rounded-md bg-surface-2" />
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
