import { useLayoutEffect, useRef, useState, type AnimationEvent } from "react";
import { ChevronLeft, ChevronRight, PanelLeftClose, PanelRightClose } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import type { HideableRegion } from "@/lib/layout/model";
import { cn } from "@/lib/utils";

/**
 * Hiding a side region, and the tab that brings it back.
 *
 * Phase 1 of the dynamic screen. A hidden region leaves a narrow tab on the edge it was on, so the way
 * back is where the thing went, and needs no shortcut remembered. The hidden-panels tray in the status
 * bar and the command palette are the other two ways, and ⌘B / ⌘⌥B the fourth.
 *
 * Focus is handed to the tab when its region is hidden: the button that was focused has just been
 * removed from the page, and a keyboard user left on `<body>` starts again from the top.
 */
type Side = Exclude<HideableRegion, "bottom">;

const HIDE_KEY: Record<Side, string> = {
  left: "layout.hide.left",
  right: "layout.hide.right",
  rail: "layout.hide.rail",
};
const SHOW_KEY: Record<Side, string> = {
  left: "layout.show.left",
  right: "layout.show.right",
  rail: "layout.show.rail",
};

/** The DOM id of a region's edge tab, so hiding can move focus onto it. */
export function edgeTabId(side: Side): string {
  return `edge-tab-${side}`;
}

/** Hide the region and put focus on the tab that now stands in for it. */
export function useHideRegion(): (side: Side) => void {
  const { dispatch } = useLayout();
  return (side) => {
    if (!dispatch({ type: "set-region", region: side, visible: false })) return;
    // After React has drawn the tab: it does not exist until the region is gone.
    requestAnimationFrame(() => document.getElementById(edgeTabId(side))?.focus());
  };
}

export function HideRegionButton({ side, className }: { side: Side; className?: string }) {
  const t = useT();
  const hide = useHideRegion();
  const Icon = side === "right" ? PanelRightClose : PanelLeftClose;
  const label = t(HIDE_KEY[side]);
  return (
    <Tooltip label={label}>
      <button
        type="button"
        aria-label={label}
        onClick={() => hide(side)}
        className={cn(
          "rounded-md p-1 text-muted-foreground transition-colors duration-1 ease-out",
          "hover:bg-surface-hover hover:text-foreground",
          focusRing,
          className,
        )}
      >
        <Icon className="h-4 w-4" aria-hidden />
      </button>
    </Tooltip>
  );
}

/** The narrow tab a hidden region leaves on its edge. Clicking it shows the region again. */
export function EdgeTab({ side }: { side: Side }) {
  const t = useT();
  const { dispatch } = useLayout();
  const label = t(SHOW_KEY[side]);
  // The chevron points the way the region will come in from.
  const Icon = side === "right" ? ChevronLeft : ChevronRight;
  return (
    <Tooltip label={label}>
      <button
        type="button"
        id={edgeTabId(side)}
        aria-label={label}
        onClick={() => dispatch({ type: "set-region", region: side, visible: true })}
        className={cn(
          "flex h-14 w-3.5 shrink-0 items-center justify-center self-center border-hairline bg-card",
          "text-muted-foreground transition-colors duration-1 ease-out hover:bg-surface-hover hover:text-foreground",
          side === "right" ? "rounded-l-md border-y border-l" : "rounded-r-md border-y border-r",
          focusRing,
        )}
      >
        <Icon className="h-3 w-3" aria-hidden />
      </button>
    </Tooltip>
  );
}

/**
 * The enter animation for a region that was hidden and is shown again, and only then.
 *
 * Owned by the region's PARENT, which stays mounted while the region comes and goes, so it can tell
 * "shown again" from "the screen just opened" (which arrives with the view's own animation). Set in a
 * layout effect, before paint, so the region is never drawn for one frame without the class.
 */
export function useRegionEnter(visible: boolean, from: "left" | "right") {
  const previous = useRef(visible);
  const [entering, setEntering] = useState(false);
  useLayoutEffect(() => {
    if (visible && !previous.current) setEntering(true);
    previous.current = visible;
  }, [visible]);
  return {
    className: entering ? (from === "left" ? "region-enter-left" : "region-enter-right") : undefined,
    // Only the region's own animation ends it: the event bubbles, and the tool rows inside the right
    // panel run their own, which would otherwise cut the slide off halfway.
    onAnimationEnd: (e: AnimationEvent<HTMLElement>) => {
      if (e.target === e.currentTarget) setEntering(false);
    },
  };
}
