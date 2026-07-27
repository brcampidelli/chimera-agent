import * as RadixTooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

/**
 * Wrap the app once, near the root. The provider is what lets tooltips share a delay: after the
 * first one opens, moving along the rail shows the rest instantly instead of re-waiting each time.
 * That grouping is most of why a real tooltip feels better than `title=`.
 */
export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <RadixTooltip.Provider delayDuration={400} skipDelayDuration={300}>
      {children}
    </RadixTooltip.Provider>
  );
}

/**
 * A label for a control that shows only an icon.
 *
 * This replaces `title=`, which looks equivalent and is not: the native tooltip never appears for a
 * keyboard user, has a delay the page cannot control (roughly a second), and is styled by the OS.
 * The icon rail is entirely icons, so that gap covered the app's whole primary navigation.
 *
 * The trigger still needs its own `aria-label`. A tooltip is a visual affordance; it is not a
 * substitute for the control having a name.
 */
export function Tooltip({
  label,
  children,
  side = "right",
}: {
  label: string;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={8}
          className="overlay floating z-50 px-2.5 py-1.5 text-xs"
        >
          {label}
          <RadixTooltip.Arrow className="fill-card" />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
