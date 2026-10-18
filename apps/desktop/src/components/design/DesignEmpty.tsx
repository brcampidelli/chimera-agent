import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { focusRing } from "@/components/ui/focus";
import { ARTBOARD_PRESETS, type ArtboardPreset } from "@/lib/design/defaults";
import { useNum, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The canvas before it has an artboard. One explicit action, with the size chosen first — a
 * blank canvas that only says "nothing here" leaves the person guessing what to press.
 */
export function DesignEmpty({ onAdd }: { onAdd: (preset: ArtboardPreset) => void }) {
  const t = useT();
  const num = useNum();
  const [chosen, setChosen] = useState<ArtboardPreset>(ARTBOARD_PRESETS[0]);
  return (
    <div className="flex h-full w-full items-center justify-center bg-surface-2 p-6">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <h2 className="text-xl font-semibold text-foreground">{t("design.empty.title")}</h2>
        <p className="text-sm text-muted-foreground">{t("design.empty.body")}</p>
        <fieldset className="flex flex-wrap justify-center gap-2">
          <legend className="sr-only">{t("design.empty.size")}</legend>
          {ARTBOARD_PRESETS.map((preset) => {
            const on = preset.id === chosen.id;
            return (
              <button
                key={preset.id}
                type="button"
                aria-pressed={on}
                onClick={() => setChosen(preset)}
                className={cn(
                  "flex flex-col items-center rounded-card border px-3 py-2 text-sm transition-colors duration-1 ease-out",
                  on ? "border-accent bg-accent/10" : "border-hairline bg-card hover:bg-surface-hover",
                  focusRing,
                )}
              >
                <span className="font-medium text-foreground">{t(preset.labelKey)}</span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {`${num(preset.width)} × ${num(preset.height)}`}
                </span>
              </button>
            );
          })}
        </fieldset>
        <Button type="button" onClick={() => onAdd(chosen)}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t("design.empty.add")}
        </Button>
      </div>
    </div>
  );
}
