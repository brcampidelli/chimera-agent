import { X } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { GROUP_KEY, toggle, valueLabel, type CategoryOption } from "@/components/catalog/filterLabels";
import { NO_FILTERS, type CatalogFilters, type FilterGroup } from "@/components/catalog/model";

/** The active filters, each removable on its own, and "Clear". Renders nothing with none active. */
export function FilterChips({
  filters,
  onChange,
  categories,
}: {
  filters: CatalogFilters;
  onChange: (next: CatalogFilters) => void;
  categories: readonly CategoryOption[];
}) {
  const t = useT();
  const chips: { group: FilterGroup; value: string }[] = [];
  for (const group of ["cat", "runs", "license", "state"] as const)
    for (const value of filters[group] as readonly string[]) chips.push({ group, value });
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-muted-foreground">{t("catalogUi.activeFilters")}</span>
      {chips.map(({ group, value }) => {
        const text = `${t(GROUP_KEY[group])}: ${valueLabel(t, group, value, categories)}`;
        return (
          <span
            key={`${group}:${value}`}
            className="inline-flex items-center gap-1 rounded-chip bg-surface-2 py-0.5 pl-2 pr-0.5 text-muted-foreground ring-1 ring-hairline"
          >
            {text}
            <button
              type="button"
              aria-label={t("catalogUi.removeFilter", { filter: text })}
              onClick={() => onChange(toggle(filters, group, value, false))}
              className={cn("rounded-chip p-0.5 hover:bg-surface-hover hover:text-foreground", focusRing)}
            >
              <X aria-hidden className="h-3 w-3" />
            </button>
          </span>
        );
      })}
      <button
        type="button"
        onClick={() => onChange(NO_FILTERS)}
        className={cn("rounded-md px-1 text-accent-ink hover:underline", focusRing)}
      >
        {t("catalogUi.clearFilters")}
      </button>
    </div>
  );
}
