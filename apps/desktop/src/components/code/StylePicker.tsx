import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import type { TFunc } from "@/lib/i18n";
import { useT } from "@/lib/i18n";
import type { OutputStyle } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The styles in the order they are offered. The default first: it is the prompt that was shipping. */
export const OUTPUT_STYLES: readonly OutputStyle[] = ["default", "concise", "explanatory"];

/** A style's name in the reader's language. Written out rather than built as `code.style.${s}`: the
 *  dead-key gate reads the source for literal keys, and an interpolated one is a key it cannot see. */
export function styleLabel(t: TFunc, style: OutputStyle): string {
  return style === "concise"
    ? t("code.style.concise")
    : style === "explanatory"
      ? t("code.style.explanatory")
      : t("code.style.default");
}

/**
 * How this conversation's answers are written (study 29, P4.5): default, concise or explanatory.
 *
 * Per conversation, beside the model picker and for the same reason: a style quietly carried over
 * from last week is a decision nobody remembers making, so a new conversation starts on the default.
 * The default sends no field at all, which is what keeps it byte-identical to the turn before styles
 * existed. The tooltip says the part that matters most: this is wording, and nothing the agent may
 * do depends on it — the server keeps it out of everything that decides reach.
 */
export function StylePicker({
  value,
  onChange,
  disabled,
}: {
  value: OutputStyle;
  onChange: (style: OutputStyle) => void;
  disabled?: boolean;
}) {
  const t = useT();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wider text-muted-foreground">{t("code.style.label")}</span>
      {/* On the group, so focusing any of its buttons shows it: React's focus events bubble, and a
          keyboard user reaches the sentence the same way a pointer does. */}
      <Tooltip label={t("code.style.hint")} side="top">
      <div className="flex overflow-hidden rounded-chip border border-border" role="group" aria-label={t("code.style.label")}>
        {OUTPUT_STYLES.map((style) => (
          <button
            key={style}
            type="button"
            aria-pressed={value === style}
            disabled={disabled}
            onClick={() => onChange(style)}
            className={cn(
              "px-2.5 py-1 text-xs transition-colors duration-1 ease-out disabled:opacity-50",
              focusRing,
              value === style ? "bg-accent/20 text-accent-ink" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {styleLabel(t, style)}
          </button>
        ))}
      </div>
      </Tooltip>
    </div>
  );
}
