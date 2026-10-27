import type { ReactNode } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The conversation header's "⋯" menu: every action the header used to show, in one place.
 *
 *  A Radix menu rather than a `<details>`: it closes on Escape and on an outside click, moves focus
 *  into itself, and is announced as a menu. The height is capped by the `max-h-dialog` token, so a
 *  long list scrolls inside the floating surface instead of pushing the transcript down. */
export function ConversationMenu({ children }: { children: ReactNode }) {
  const t = useT();
  const label = t("code.chat.menu");
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={label}
        title={label}
        data-testid="conversation-menu"
        className={cn(
          "flex items-center rounded-chip border border-hairline px-2 py-1 text-foreground",
          "transition-colors duration-1 ease-out hover:bg-surface-hover",
          focusRing,
        )}
      >
        <MoreHorizontal aria-hidden className="h-4 w-4" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={6}
          className="overlay floating z-50 flex max-h-dialog min-w-56 flex-col gap-0.5 overflow-auto p-1 text-foreground"
        >
          {children}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

const ITEM = cn(
  "flex w-full items-center gap-2 rounded-card px-2.5 py-1.5 text-left text-sm outline-hidden",
  "data-highlighted:bg-surface-hover disabled:opacity-50",
);

/** An action in the menu. `onSelect` keeps the menu's own close behaviour, so a click closes it. */
export function ConversationMenuItem({
  onSelect,
  testId,
  title,
  children,
}: {
  onSelect: () => void;
  testId?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <Menu.Item className={ITEM} onSelect={onSelect} data-testid={testId} title={title}>
      {children}
    </Menu.Item>
  );
}

/** An on/off setting in the menu. The check mark is the state, so it is not carried by colour. */
export function ConversationMenuToggle({
  checked,
  onCheckedChange,
  testId,
  title,
  children,
}: {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  testId?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <Menu.CheckboxItem
      className={ITEM}
      checked={checked}
      onCheckedChange={onCheckedChange}
      data-testid={testId}
      title={title}
    >
      <span aria-hidden className="w-3 text-accent">{checked ? "✓" : ""}</span>
      {children}
    </Menu.CheckboxItem>
  );
}
