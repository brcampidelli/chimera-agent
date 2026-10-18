import type { TFunc } from "@/lib/i18n";
import type { CatalogFilters, FilterGroup } from "@/components/catalog/model";

export const ITEM =
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-sm outline-hidden data-highlighted:bg-surface-hover";

/** Literal keys, so the dictionary's reachability check can see each one. */
export const GROUP_KEY: Record<FilterGroup, string> = {
  cat: "catalogUi.group.cat",
  runs: "catalogUi.group.runs",
  license: "catalogUi.group.license",
  state: "catalogUi.group.state",
};
const VALUE_KEY: Record<string, string> = {
  "runs:text": "catalogUi.runs.text",
  "runs:process": "catalogUi.runs.process",
  "runs:network": "catalogUi.runs.network",
  "runs:inert": "catalogUi.runs.inert",
  "runs:unknown": "catalogUi.runs.unknown",
  "license:permissive": "catalogUi.license.permissive",
  "license:other": "catalogUi.license.other",
  "license:unread": "catalogUi.license.unread",
  "state:none": "catalogUi.state.none",
  "state:pending": "catalogUi.state.pending",
  "state:active": "catalogUi.state.active",
  "state:held": "catalogUi.state.held",
  "state:inactive": "catalogUi.state.inactive",
};

export interface CategoryOption {
  id: string;
  label: string;
  count: number;
}

export function valueLabel(
  t: TFunc,
  group: FilterGroup,
  value: string,
  categories: readonly CategoryOption[],
): string {
  if (group === "cat") return categories.find((c) => c.id === value)?.label ?? value;
  return t(VALUE_KEY[`${group}:${value}`] ?? value);
}

/** The filters with one value ticked or unticked. */
export function toggle(filters: CatalogFilters, group: FilterGroup, value: string, on: boolean): CatalogFilters {
  const current = filters[group] as readonly string[];
  const next = on ? [...current.filter((v) => v !== value), value] : current.filter((v) => v !== value);
  return { ...filters, [group]: next };
}

