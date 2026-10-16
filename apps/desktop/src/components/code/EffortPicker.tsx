import { useQuery } from "@tanstack/react-query";

import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { getModels } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The variants a slug may carry, as the server splits them (`chimera.providers.catalog.split_effort`).
 *  "" is no suffix: the model reasons as its route does by default. */
export const EFFORTS = ["", "low", "medium", "high"] as const;
export type Effort = (typeof EFFORTS)[number];

/** `["openrouter/x/y", "high"]` for `openrouter/x/y#high`. Only the three known efforts are split,
 *  the same rule as the server, so a slug that happens to hold `#` is never rewritten here. */
export function splitEffort(slug: string): [string, Effort] {
  const at = slug.lastIndexOf("#");
  if (at > 0) {
    const tail = slug.slice(at + 1);
    if (tail === "low" || tail === "medium" || tail === "high") return [slug.slice(0, at), tail];
  }
  return [slug, ""];
}

/** The slug with `effort` as its variant ("" removes it). */
export function withEffort(slug: string, effort: Effort): string {
  const [base] = splitEffort(slug);
  return effort ? `${base}#${effort}` : base;
}

/** Written out, not built as `code.effort.${e}`: the dead-key gate reads literal keys only. */
function effortLabel(t: TFunc, effort: Effort): string {
  return effort === "low"
    ? t("code.effort.low")
    : effort === "medium"
      ? t("code.effort.medium")
      : effort === "high"
        ? t("code.effort.high")
        : t("code.effort.default");
}

/**
 * How hard the conversation's model reasons: its own default, low, medium or high (P2-15).
 *
 * Shown only when there is something true to offer: a model the provider's index says takes a
 * reasoning parameter (`reasoning === true`), or a slug that already carries a variant — so a pick
 * made earlier can always be taken back. Nothing is shown for the install default ("" model), whose
 * capabilities this screen does not know, nor for `reasoning: null`, which is "not said", not "yes".
 *
 * The variant is part of the slug the conversation pins (`model_pinned`), so it is kept with the
 * conversation like the model is. A route that cannot take it answers at its own default and the
 * receipt names the model without the suffix — the hint says so rather than promising the effort.
 */
export function EffortPicker({
  value,
  onChange,
  disabled,
}: {
  /** The conversation's model slug, with or without a variant. */
  value: string;
  onChange: (slug: string) => void;
  disabled?: boolean;
}) {
  const t = useT();
  // The cache the model dialog fills; never fetched from here (`enabled: false`), like the picker's.
  const listing = useQuery({ queryKey: ["models", ""], queryFn: () => getModels(), enabled: false });
  const [base, effort] = splitEffort(value);
  const reasons = listing.data?.models?.find((m) => m.slug === base)?.reasoning === true;
  if (!base || (!reasons && !effort)) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wider text-muted-foreground">{t("code.effort.label")}</span>
      <Tooltip label={t("code.effort.hint")} side="top">
        <div className="flex overflow-hidden rounded-chip border border-border" role="group" aria-label={t("code.effort.label")}>
          {EFFORTS.map((option) => (
            <button
              key={option || "default"}
              type="button"
              aria-pressed={effort === option}
              disabled={disabled}
              onClick={() => onChange(withEffort(value, option))}
              className={cn(
                "px-2.5 py-1 text-xs transition-colors duration-1 ease-out disabled:opacity-50",
                focusRing,
                effort === option ? "bg-accent/20 text-accent-ink" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {effortLabel(t, option)}
            </button>
          ))}
        </div>
      </Tooltip>
    </div>
  );
}
