import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

/**
 * A modal dialog.
 *
 * Radix, not hand-rolled. A correct focus trap is the one primitive that genuinely earns a
 * dependency: restoring focus to whatever opened the dialog, containing Tab and Shift+Tab, locking
 * scroll without shifting the page, marking the rest of the document inert, and handling Escape and
 * outside-click — that is a couple of hundred lines and everybody's first version has a hole in it.
 *
 * Everything visual stays here, so the design tokens keep working and there is no vendor styling to
 * fight. Motion comes from `.overlay` / `.backdrop` in motion.css, which Radix drives through its
 * own `data-state` attributes — the same names our usePresence hook uses.
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Required: a dialog with no accessible name is announced as just "dialog". */
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  /**
   * Where focus was before this opened.
   *
   * Radix restores focus to its own `<Dialog.Trigger>`, and this dialog is opened from app state
   * instead — a menu item, a keyboard shortcut, a row action. With no Trigger to return to, Radix
   * drops focus on `<body>`, which puts a keyboard user back at the top of the document every time
   * they dismiss something. Remembering the element ourselves is the whole fix.
   */
  const t = useT();
  const restoreTo = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (open) restoreTo.current = document.activeElement as HTMLElement | null;
  }, [open]);

  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="backdrop fixed inset-0 z-50 bg-scrim" />
        <RadixDialog.Content
          onCloseAutoFocus={(e) => {
            const target = restoreTo.current;
            // Only take over when the element is still in the document — if the dialog deleted the
            // row that opened it, Radix's own fallback is the better answer.
            if (!target || !target.isConnected) return;
            e.preventDefault();
            target.focus();
          }}
          className={cn(
            "overlay floating fixed left-1/2 top-1/2 z-50 w-[min(32rem,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 p-5",
            className,
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <RadixDialog.Title className="text-lg font-semibold">
                {title}
              </RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="mt-1 text-sm text-muted-foreground">
                  {description}
                </RadixDialog.Description>
              ) : (
                // Radix warns when Content has no Description. An explicitly empty one says "this
                // dialog is its title" rather than leaving a warning nobody reads.
                <RadixDialog.Description className="sr-only">
                  {title}
                </RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close
              className={cn(
                "-mr-1 -mt-1 rounded-lg p-1.5 text-muted-foreground",
                "transition-colors duration-1 ease-out hover:text-foreground",
                focusRing,
              )}
            >
              <X className="h-4 w-4" />
              {/* The only name this control has: it renders as an X. It was hardcoded English, so
                  every dialog in the app announced "Close" to a screen reader reading Portuguese
                  — the app's own default language. Invisible on screen, which is why it lasted. */}
              <span className="sr-only">{t("common.close")}</span>
            </RadixDialog.Close>
          </div>

          {/* A flex column, so a dialog that bounds its own height can hand that bound DOWN to the
              part meant to scroll. `flex-1` grows this body; `min-h-0` lets it shrink below its
              content; being a flex container itself is what makes `flex-1` work on the child. A
              plain block here breaks the chain silently: the child sizes to its content, the scroll
              region never bounds, and the footer leaves the screen while the card still measures
              85vh. Inert for a dialog that sets no height — the body just wraps its content. */}
          <div className="mt-4 flex min-h-0 flex-1 flex-col">{children}</div>
          {footer && (
            <div className="mt-5 flex justify-end gap-2">{footer}</div>
          )}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
