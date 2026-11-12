import { useEffect, useRef } from "react";

import { focusRing } from "@/components/ui/focus";
import { cn } from "@/lib/utils";

/** What a "select all" box shows for `picked` of `total`: nothing, some, or everything. */
export function triState(picked: number, total: number): boolean | "mixed" {
  if (total === 0 || picked === 0) return false;
  return picked >= total ? true : "mixed";
}

/**
 * A checkbox that can also say "some".
 *
 * A real `<input type="checkbox">`, so the keyboard (Tab, Space) and the accessibility tree come for
 * free. The "some" state is the DOM's `indeterminate`, which has no HTML attribute and can only be
 * set from script — hence the effect — and is announced as `aria-checked="mixed"`. Clicking a mixed
 * box selects everything: the common reading of a header box, and the one that never needs a second
 * click to undo a half-selection by accident.
 */
export function Checkbox({
  checked,
  onChange,
  label,
  disabled = false,
  className,
}: {
  checked: boolean | "mixed";
  onChange: (next: boolean) => void;
  /** Required: a bare box in a column of boxes announces as "checkbox" and nothing else. */
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const mixed = checked === "mixed";
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked === true}
      aria-checked={mixed ? "mixed" : checked}
      aria-label={label}
      disabled={disabled}
      onChange={() => onChange(!(checked === true))}
      className={cn("h-3.5 w-3.5 shrink-0 cursor-pointer accent-accent", focusRing, className)}
    />
  );
}
