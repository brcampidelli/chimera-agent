import { useEffect, useRef, type ReactNode } from "react";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { EdgeTab, useRegionEnter } from "@/components/shell/RegionToggle";
import { Splitter } from "@/components/shell/Splitter";
import { Dock, LayoutDnd } from "@/components/shell/Dock";
import { MaximizedPanel, useEscapeRestores } from "@/components/shell/Maximize";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { cn } from "@/lib/utils";

/**
 * The application frame.
 *
 * One layout, filled by slots, rather than each screen inventing its own. The old shell hardcoded
 * the sessions list and the activity panel to the chat view, so every other screen was a lonely
 * centred column — and Code and Agents opted out of even that. Fifteen screens with four different
 * layouts is what made the app read as a menu of features instead of one workspace.
 *
 * The pattern a user learns once and then knows everywhere:
 *
 *   left = what exists · centre = what I'm doing · right = what the agent is doing right now
 */
export function AppShell({
  rail,
  context,
  inspector,
  header,
  children,
  ignite = false,
  /** Identifies the current screen. Changing it moves focus and announces the new view. */
  viewKey,
  viewLabel,
  onOpenUsage,
}: {
  rail: ReactNode;
  context?: ReactNode;
  inspector?: ReactNode;
  header?: ReactNode;
  children: ReactNode;
  ignite?: boolean;
  viewKey: string;
  viewLabel: string;
  onOpenUsage?: () => void;
}) {
  const t = useT();
  const { layout } = useLayout();
  const showRail = layout.regions.rail.visible;
  // The inspector is the right region wherever a screen has one; a screen without one shows no tab.
  const showInspector = layout.regions.right.visible;
  const inspectorEnter = useRegionEnter(showInspector, "right");
  // A screen's own left sidebar (the editor's) follows the left region like the conversation list
  // does (phase 6). The Code screen draws its list itself and passes no context, so no tab doubles.
  const showContext = layout.regions.left.visible;
  const contextEnter = useRegionEnter(showContext, "left");
  // Escape restores whatever is maximised (phase 5), from anywhere on screen.
  useEscapeRestores();
  const mainRef = useRef<HTMLElement>(null);
  const first = useRef(true);

  useEffect(() => {
    // Focus followed nothing on a view change: it stayed on the rail button, so a keyboard user
    // re-tabbed from the top of the app every single time. Moving it into the new region is also
    // what makes the announcement below land in the right place.
    if (first.current) {
      first.current = false; // don't steal focus on the very first paint
      return;
    }
    mainRef.current?.focus();
  }, [viewKey]);

  return (
    <div className={cn("relative flex h-full flex-col", ignite && "ignite")}>
      <div
        aria-hidden
        {...(ignite && { "data-ignite": "wash" })}
        className="ambient-wash pointer-events-none fixed inset-0 -z-10"
      />

      {/* First focusable thing on the page. Without it, reaching the content past a fifteen-item
          rail costs fifteen Tab presses. */}
      <a
        href="#main"
        className={cn(
          "sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50",
          "floating px-3 py-1.5 text-sm",
          focusRing,
        )}
      >
        {t("a11y.skipToContent")}
      </a>

      {/* One space to drag panels in (phase 4): the Code screen's left dock, the right panel, and the
          bottom dock below the row. */}
      <LayoutDnd>
      <div className="relative flex min-h-0 flex-1">
        {/* A maximised dock panel fills the row, over what is there (phase 5). */}
        <MaximizedPanel />
        {/* Hidden, the rail leaves a tab on the edge. Every destination stays reachable meanwhile:
            ⌘1–⌘5 and the command palette do not go through the rail. */}
        {showRail ? rail : <EdgeTab side="rail" />}

        {context &&
          (showContext ? (
            <div
              {...(ignite && { "data-ignite": "context" })}
              className={cn("flex shrink-0", contextEnter.className)}
              onAnimationEnd={contextEnter.onAnimationEnd}
            >
              {context}
            </div>
          ) : (
            <EdgeTab side="left" />
          ))}

        <main
          id="main"
          ref={mainRef}
          // -1 so the skip link and the view-change focus move can land here, without adding the
          // region itself to the tab order.
          tabIndex={-1}
          {...(ignite && { "data-ignite": "main" })}
          className="flex min-w-0 flex-1 flex-col focus-visible:outline-hidden"
        >
          {header}
          {/* Re-keyed per view so React replaces the subtree and the enter animation actually runs.
              Only the incoming screen animates; the outgoing one leaves at once. */}
          <div key={viewKey} className="view-enter flex min-h-0 flex-1 flex-col">
            {children}
          </div>
        </main>

        {inspector &&
          (showInspector ? (
            <>
              <Splitter region="right" grows="left" />
              <div
                {...(ignite && { "data-ignite": "inspector" })}
                className={cn("flex shrink-0", inspectorEnter.className)}
                // The width is the layout's (phase 2); the panel inside fills it.
                style={{ width: layout.regions.right.size ?? undefined }}
                onAnimationEnd={inspectorEnter.onAnimationEnd}
              >
                {inspector}
              </div>
            </>
          ) : (
            // The agent's state does not go with it: the status bar below keeps showing it.
            <EdgeTab side="right" />
          ))}
      </div>

      {/* Panels moved to the bottom (phase 4). Nothing at all while none is there, except while one is
          being dragged, when it shows where it can land. */}
      {layout.regions.bottom.visible ? (
        <Dock
          zone="bottom"
          className="shrink-0 border-t border-hairline"
        />
      ) : null}
      </LayoutDnd>

      {/* Announces the screen a keyboard or screen-reader user just landed on. Separate from the
          agent's own status region, which is about what the agent is doing, not where you are. */}
      <span className="sr-only" role="status" aria-live="polite">
        {viewLabel}
      </span>

      <AgentStatusBar onOpenUsage={onOpenUsage} />
    </div>
  );
}
