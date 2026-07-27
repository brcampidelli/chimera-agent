import * as RadixSelect from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

export interface SelectOption<T extends string> {
  value: T;
  label: string;
  /** Optional second line — what this choice actually means. */
  hint?: string;
}

/**
 * A dropdown.
 *
 * A native `<select>` is perfectly good and the app should keep using it for short, plain lists.
 * This exists for the ones where it isn't: the language picker (seven entries, wants typeahead) and
 * the model pickers (long, and each option needs a second line of explanation, which a native
 * `<option>` cannot render).
 */
export function Select<T extends string>({
  value,
  onChange,
  options,
  label,
  placeholder,
  disabled = false,
  className,
}: {
  value: T;
  onChange: (next: T) => void;
  options: readonly SelectOption<T>[];
  /** Required. A dropdown with no name announces only its current value. */
  label: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <RadixSelect.Root value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <RadixSelect.Trigger
        aria-label={label}
        className={cn(
          "field flex items-center justify-between gap-2 px-3 py-1.5 text-sm",
          focusRing,
          disabled && "cursor-not-allowed opacity-50",
          className,
        )}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon>
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>

      <RadixSelect.Portal>
        <RadixSelect.Content
          // "popper" rather than the default: the aligned position can render the list over its own
          // trigger, which hides what you are changing while you change it.
          position="popper"
          sideOffset={6}
          className="overlay floating z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden"
        >
          <RadixSelect.Viewport className="p-1">
            {options.map((opt) => (
              <RadixSelect.Item
                key={opt.value}
                value={opt.value}
                className={cn(
                  "relative flex cursor-default select-none items-start gap-2 rounded-lg px-2 py-1.5 text-sm",
                  "outline-none data-[highlighted]:bg-surface-hover",
                )}
              >
                <span className="mt-0.5 w-4 shrink-0">
                  <RadixSelect.ItemIndicator>
                    <Check className="h-3.5 w-3.5 text-accent" />
                  </RadixSelect.ItemIndicator>
                </span>
                <span className="min-w-0">
                  <RadixSelect.ItemText>{opt.label}</RadixSelect.ItemText>
                  {opt.hint && (
                    <span className="block text-xs text-muted-foreground">{opt.hint}</span>
                  )}
                </span>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}
