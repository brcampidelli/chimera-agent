import * as Menu from "@radix-ui/react-dropdown-menu";
import { ArrowUpDown, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { ITEM } from "@/components/catalog/filterLabels";
import type { SortValue } from "@/components/catalog/model";

const SORT_KEY: Record<SortValue, string> = {
  relevance: "catalogUi.sort.relevance",
  name: "catalogUi.sort.name",
  recent: "catalogUi.sort.recent",
  stars: "catalogUi.sort.stars",
};

/** One choice of order. Only the sorts the data can answer are offered — see `availableSorts`. */
export function SortMenu({
  value,
  options,
  onChange,
}: {
  value: SortValue;
  options: readonly SortValue[];
  onChange: (next: SortValue) => void;
}) {
  const t = useT();
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <Button variant="ghost" size="sm" aria-label={t("catalogUi.sort.label", { current: t(SORT_KEY[value]) })}>
          <ArrowUpDown aria-hidden className="h-4 w-4" />
          <span className="hidden @xl:inline">{t(SORT_KEY[value])}</span>
        </Button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-48 p-1 text-foreground">
          <Menu.RadioGroup value={value} onValueChange={(v) => onChange(v as SortValue)}>
            {options.map((option) => (
              <Menu.RadioItem key={option} value={option} className={ITEM}>
                <span className="flex h-4 w-4 items-center justify-center">
                  <Menu.ItemIndicator>
                    <Check aria-hidden className="h-3.5 w-3.5" />
                  </Menu.ItemIndicator>
                </span>
                {t(SORT_KEY[option])}
              </Menu.RadioItem>
            ))}
          </Menu.RadioGroup>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
