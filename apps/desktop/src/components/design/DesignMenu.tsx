import * as Menu from "@radix-ui/react-dropdown-menu";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

import { focusRing } from "@/components/ui/focus";
import { cn } from "@/lib/utils";

const ITEM = cn(
  "flex w-full cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground outline-none",
  "data-[highlighted]:bg-surface-hover data-[disabled]:opacity-40",
);

/**
 * A menu on Radix's dropdown. The trigger is a real button (`aria-haspopup="menu"`, `aria-expanded`),
 * the list is `role="menu"` with `menuitem` children, arrow keys / Home / End move through it, Escape
 * and an outside click close it, and focus goes back to the trigger. The same primitive the catalog's
 * sort and filter menus use, so the canvas does not grow its own keyboard handling.
 *
 * Without `label` the trigger's text is its accessible name, which lets a heading contain the trigger
 * and still be announced as the title.
 */
export function DesignMenu({
  trigger,
  label,
  align = "start",
  items,
  className,
}: {
  trigger: ReactNode;
  label?: string;
  align?: "start" | "center" | "end";
  items: ReactNode;
  className?: string;
}) {
  return (
    <Menu.Root>
      <Menu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex h-8 max-w-full cursor-pointer items-center gap-1 rounded-lg px-1 text-xs text-muted-foreground transition-colors duration-1 ease-out hover:bg-surface-hover",
            focusRing,
            className,
          )}
        >
          {trigger}
          <ChevronDown className="h-3 w-3 shrink-0" aria-hidden="true" />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align={align} sideOffset={4} className="overlay floating z-50 min-w-40 p-1 text-foreground">
          {items}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** One command in a `DesignMenu`. Activating it closes the menu (Radix does that for `onSelect`). */
export function DesignMenuItem({
  onSelect,
  children,
  disabled,
  title,
}: {
  onSelect: () => void;
  children: ReactNode;
  disabled?: boolean;
  /** Tooltip only: the accessible name comes from `children`, so this never renames the item. */
  title?: string;
}) {
  return (
    <Menu.Item className={ITEM} onSelect={onSelect} disabled={disabled} title={title}>
      {children}
    </Menu.Item>
  );
}

/** A thin rule between groups of items. */
export function DesignMenuSeparator() {
  return <Menu.Separator className="my-1 h-px bg-hairline" />;
}
