import { useEffect, useRef, useState, type ReactNode } from "react";
import * as RadixDialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { EdgeTab, edgeTabId, useRegionEnter } from "@/components/shell/RegionToggle";
import { SideDrawerContext } from "@/components/shell/sideDrawer";
import { Splitter } from "@/components/shell/Splitter";
import { Dock, LayoutDnd } from "@/components/shell/Dock";
import { MaximizedPanel, useEscapeRestores } from "@/components/shell/Maximize";
import { focusRing } from "@/components/ui/focus";
import { useOptionalAgentBatch } from "@/lib/agent-batch";
import { listRunningTurns } from "@/lib/api";
import { useRunSession } from "@/lib/run-session";
import { useAgent } from "@/lib/agent-context";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { cn } from "@/lib/utils";

/** Below this width the side panel is a drawer over the conversation, not a column beside it. */
export const DRAWER_BELOW_PX = 1100;
const DRAWER_QUERY = `(max-width: ${DRAWER_BELOW_PX - 1}px)`;

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
  inspectorFollowsAgent = false,
  quietStatus = false,
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
  /** The inspector describes the agent's turn, so it stands aside while there is no turn to describe:
   *  nothing running and no tool calls to show. A tab on the edge brings it back. */
  inspectorFollowsAgent?: boolean;
  /** The screen has a status bar of its own (the editor's): the agent's bar shows only while
   *  something is running — a turn here or in another conversation, or a run — since that is where
   *  it is watched and stopped from. Idle, its receipt of the last turn is the conversation's
   *  business, not the file's. */
  quietStatus?: boolean;
}) {
  const t = useT();
  const { layout, dispatch: dispatchLayout } = useLayout();
  const showRail = layout.regions.rail.visible;
  // The inspector is the right region wherever a screen has one; a screen without one shows no tab.
  const agent = useAgent();
  const runSession = useRunSession();
  // The same list, key and interval as the bar's own "running elsewhere", so no second poll.
  const otherTurns = useQuery({
    queryKey: ["code-turns-running"],
    queryFn: listRunningTurns,
    refetchInterval: 4000,
    enabled: quietStatus,
  });
  const statusIdle =
    agent.status === "idle" && !agent.busy && !runSession.running && (otherTurns.data ?? []).length === 0;
  // Quiet: the screen has a turn to describe and there is none yet. A failed turn keeps its tool
  // calls, so the panel stays for it — the moment someone wants to see what ran is right after.
  // The batch board moved to the right panel is something to watch too: a quiet column would hide it.
  const batch = useOptionalAgentBatch();
  const batchPanel = layout.panels["agents.batch"];
  const batchOnRight = batch?.run != null && batchPanel.zone === "right" && batchPanel.mode !== "closed";
  const quiet = inspectorFollowsAgent && agent.status === "idle" && agent.tools.length === 0 && !batchOnRight;
  // Opened on purpose while quiet, it stays until the next turn begins and then follows the agent
  // again: a person who looked once has not asked to keep a half-empty column for good.
  const [peek, setPeek] = useState(false);
  useEffect(() => {
    if (!quiet) setPeek(false);
  }, [quiet]);
  const regionVisible = layout.regions.right.visible;
  const showInspector = regionVisible && (!quiet || peek);
  // Narrow windows: the side panel becomes a drawer, a modal dialog over the conversation. Closed by
  // default; its edge tab opens it, and the dialog itself handles Escape, the backdrop and focus
  // (Radix only dismisses the topmost layer, so a dialog opened over it keeps its own Escape).
  const drawer = useMediaQuery(DRAWER_QUERY);
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => {
    if (!drawer) setDrawerOpen(false);
  }, [drawer]);
  const drawerShown = drawer && drawerOpen && regionVisible;
  const openDrawer = () => {
    setDrawerOpen(true);
    if (!regionVisible) dispatchLayout({ type: "set-region", region: "right", visible: true });
  };
  const toggleDrawer = () => (drawerShown ? setDrawerOpen(false) : openDrawer());
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
    <SideDrawerContext.Provider value={{ drawer, open: drawerShown, toggle: toggleDrawer }}>
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

        {inspector && drawer ? (
          <>
            {/* Always here in a drawer layout: it is the way back, and the dialog returns focus to it. */}
            <EdgeTab side="right" onShow={openDrawer} />
            <RadixDialog.Root open={drawerShown} onOpenChange={setDrawerOpen}>
              <RadixDialog.Portal>
                <RadixDialog.Overlay className="backdrop fixed inset-0 z-30 bg-scrim" />
                <RadixDialog.Content
                  aria-describedby={undefined}
                  aria-modal="true"
                  onCloseAutoFocus={(event) => {
                    event.preventDefault();
                    document.getElementById(edgeTabId("right"))?.focus();
                  }}
                  className="floating fixed inset-y-0 right-0 z-40 flex max-w-full rounded-none focus-visible:outline-hidden"
                  style={{ width: layout.regions.right.size ?? undefined }}
                >
                  <RadixDialog.Title className="sr-only">{t("shell.drawer.label")}</RadixDialog.Title>
                  {inspector}
                </RadixDialog.Content>
              </RadixDialog.Portal>
            </RadixDialog.Root>
          </>
        ) : null}
        {inspector && !drawer &&
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
            // The agent's state does not go with it: the status bar below keeps showing it. Quiet,
            // the same tab peeks instead of changing the layout the person chose.
            <EdgeTab side="right" onShow={regionVisible ? () => setPeek(true) : undefined} />
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

      {quietStatus && statusIdle ? null : <AgentStatusBar onOpenUsage={onOpenUsage} viewKey={viewKey} />}
    </div>
    </SideDrawerContext.Provider>
  );
}
