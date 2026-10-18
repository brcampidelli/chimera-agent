import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check, SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { GROUP_KEY, ITEM, toggle, valueLabel, type CategoryOption } from "@/components/catalog/filterLabels";
import {
  filterCount,
  LICENSES,
  RUNS,
  STATES,
  type CatalogFilters,
  type FilterGroup,
} from "@/components/catalog/model";

const LABEL = "px-2 pb-0.5 pt-2 text-xs font-semibold text-muted-foreground";

/** Checkbox groups in one menu. Within a group the ticks are alternatives; across groups, all hold. */
export function FilterMenu({
  filters,
  onChange,
  categories,
}: {
  filters: CatalogFilters;
  onChange: (next: CatalogFilters) => void;
  categories: readonly CategoryOption[];
}) {
  const t = useT();
  const n = filterCount(filters);
  const groups: [FilterGroup, readonly string[]][] = [
    ["cat", categories.map((c) => c.id)],
    ["runs", RUNS],
    ["license", LICENSES],
    ["state", STATES],
  ];
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label={n > 0 ? t("catalogUi.filtersActive", { n }) : t("catalogUi.filters")}
        >
          <SlidersHorizontal aria-hidden className="h-4 w-4" />
          <span className="hidden @xl:inline">{t("catalogUi.filters")}</span>
          {n > 0 && (
            <span aria-hidden className="rounded-chip bg-accent/15 px-1.5 text-xs text-accent-ink">
              {n}
            </span>
          )}
        </Button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 max-h-96 min-w-56 overflow-y-auto p-1 text-foreground">
          {groups.map(([group, values]) =>
            values.length === 0 ? null : (
              <Menu.Group key={group}>
                <Menu.Label className={LABEL}>{t(GROUP_KEY[group])}</Menu.Label>
                {values.map((value) => {
                  const checked = (filters[group] as readonly string[]).includes(value);
                  const count = group === "cat" ? categories.find((c) => c.id === value)?.count : undefined;
                  return (
                    <Menu.CheckboxItem
                      key={value}
                      className={ITEM}
                      checked={checked}
                      // Keep the menu open: ticking three boxes should not take three trips.
                      onSelect={(e) => e.preventDefault()}
                      onCheckedChange={(on) => onChange(toggle(filters, group, value, on === true))}
                    >
                      <span className="flex h-4 w-4 items-center justify-center">
                        <Menu.ItemIndicator>
                          <Check aria-hidden className="h-3.5 w-3.5" />
                        </Menu.ItemIndicator>
                      </span>
                      <span className="flex-1">{valueLabel(t, group, value, categories)}</span>
                      {count !== undefined && <span className="text-xs text-muted-foreground">{count}</span>}
                    </Menu.CheckboxItem>
                  );
                })}
              </Menu.Group>
            ),
          )}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
