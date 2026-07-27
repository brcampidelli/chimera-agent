import { useEffect, useRef, type ReactNode } from "react";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
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

      <div className="flex min-h-0 flex-1">
        {rail}

        {context && (
          <div {...(ignite && { "data-ignite": "context" })} className="flex shrink-0">
            {context}
          </div>
        )}

        <main
          id="main"
          ref={mainRef}
          // -1 so the skip link and the view-change focus move can land here, without adding the
          // region itself to the tab order.
          tabIndex={-1}
          {...(ignite && { "data-ignite": "main" })}
          className="flex min-w-0 flex-1 flex-col focus-visible:outline-none"
        >
          {header}
          {/* Re-keyed per view so React replaces the subtree and the enter animation actually runs.
              Only the incoming screen animates; the outgoing one leaves at once. */}
          <div key={viewKey} className="view-enter flex min-h-0 flex-1 flex-col">
            {children}
          </div>
        </main>

        {inspector && (
          <div {...(ignite && { "data-ignite": "inspector" })} className="flex shrink-0">
            {inspector}
          </div>
        )}
      </div>

      {/* Announces the screen a keyboard or screen-reader user just landed on. Separate from the
          agent's own status region, which is about what the agent is doing, not where you are. */}
      <span className="sr-only" role="status" aria-live="polite">
        {viewLabel}
      </span>

      <AgentStatusBar onOpenUsage={onOpenUsage} />
    </div>
  );
}
