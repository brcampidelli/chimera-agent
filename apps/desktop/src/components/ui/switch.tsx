import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

/**
 * An on/off toggle.
 *
 * This existed twice before — near-identical copies in Cron.tsx and Settings.tsx — which is the
 * usual sign that a primitive was missing rather than that two things happened to look alike. Not
 * worth a dependency: it is a button with `role="switch"` and thirty lines of styling.
 */
export function Switch({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Required. A bare toggle announces as "switch, on" with no indication of what is on. */
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-5 w-9 shrink-0 rounded-chip transition-all duration-1 ease-out",
        focusRing,
        checked ? "bg-accent-grad shadow-toggle-on" : "bg-muted shadow-inset",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      {/* translate-x rather than a left offset: transform is compositor-only, so the knob slides
          without forcing layout on every frame. */}
      <span
        aria-hidden
        className={cn(
          "absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm",
          "transition-transform duration-1 ease-out",
          checked ? "translate-x-4" : "translate-x-0",
        )}
      />
    </button>
  );
}
