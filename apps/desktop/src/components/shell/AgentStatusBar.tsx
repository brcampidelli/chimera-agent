import { Square } from "lucide-react";

import { BrandMark } from "@/components/BrandMark";
import { ServerBadge } from "@/components/ServerBadge";
import { VersionBadge } from "@/components/VersionBadge";
import { focusRing } from "@/components/ui/focus";
import { useAgent } from "@/lib/agent-context";
import { useRunSession } from "@/lib/run-session";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The agent's presence, on every screen.
 *
 * Three things it fixes at once:
 *   · The agent used to vanish when you left the chat view. Now it is always visible.
 *   · Stop was reachable only from the composer, so navigating away mid-run stranded you with a
 *     running agent and no way to halt it. Stop is global here.
 *   · The cost of the last turn is one click from the full breakdown, which is what makes it safe
 *     to move the usage screen out of the primary navigation.
 *
 * It also absorbs the floating VersionBadge, so the app has one fewer thing hovering over content.
 */
export function AgentStatusBar({ onOpenUsage }: { onOpenUsage?: () => void }) {
  const t = useT();
  const { status, tools, report, busy, stop } = useAgent();
  const run = useRunSession();
  // A live run outranks the chat turn as the subject of this bar. Both can be going at once, but
  // only one of them is the thing you might have walked away from — a chat turn finishes in
  // seconds and you are looking at it; a run takes minutes and is the reason this bar exists.
  const idle = status === "idle" && !run.running;
  // Not `.at(-1)`: the project targets ES2020, and widening lib for one call is a
  // disproportionate change.
  const lastTool = tools.length > 0 ? tools[tools.length - 1] : undefined;

  return (
    <footer
      className={cn(
        "flex h-8 shrink-0 items-center gap-3 border-t border-hairline bg-card/40 px-3",
        "text-xs text-muted-foreground",
      )}
    >
      {/* The mark breathes while the agent works. This is the only ambient animation in the app
          outside the launch sequence, and it is the one that says "still going". */}
      <BrandMark className={cn("h-4 w-4", !idle && "pulse-glow")} aria-hidden alt="" />

      {/* The single announcement channel for agent state. Transitions only — announcing the
          streaming text itself would re-read the whole growing answer on every token. */}
      <span role="status" aria-live="polite" className="font-medium">
        {run.running ? t("runs.running") : t(`activity.${status}`)}
      </span>

      {run.running ? (
        // Which run. Without the task text the bar says something is happening but not what, which
        // is exactly the state you are in after navigating away and forgetting.
        <>
          <Separator />
          <span className="truncate">{run.task}</span>
        </>
      ) : (
        lastTool && (
          <>
            <Separator />
            <span className="truncate font-mono">
              {lastTool.name} {lastTool.ok ? "✓" : "✗"}
            </span>
          </>
        )
      )}

      {/* The turn's cost belongs to the chat turn that produced it. Showing it beside a run's
          status would read as that run's cost, which it is not. */}
      {!run.running && report && (
        <>
          <Separator />
          {/* Prompt + completion, matching what the Activity panel shows for the same turn. */}
          <span>{(report.prompt_tokens + report.completion_tokens).toLocaleString()} tok</span>
          <button
            type="button"
            onClick={onOpenUsage}
            disabled={!onOpenUsage}
            className={cn(
              "rounded px-1 transition-colors duration-1 ease-out",
              onOpenUsage && "hover:text-foreground",
              focusRing,
            )}
          >
            {/* A ceiling is shown only beside a cost that is KNOWN. When the turn ran on a model
                with no list price the numerator is unknown, and pairing an unknown with a
                denominator invites reading it as small — the same reason `Usage.tsx` prints "—" for
                an unpriced group instead of $0.0000. So the unavailable case keeps saying only
                that, with no limit beside it.

                Four decimals on BOTH sides, matching `SpendBudget.blocked()`: when the cap does
                bite, the sentence in the answer and the number in this bar have to be the same
                number, not two roundings of it. */}
            {report.usd === null || report.usd === undefined
              ? t("activity.costUnavailable")
              : report.max_usd
                ? t("activity.costOfCap", {
                    spent: `$${report.usd.toFixed(4)}`,
                    cap: `$${report.max_usd.toFixed(4)}`,
                  })
                : `~ $${report.usd.toFixed(4)}`}
          </button>
        </>
      )}

      <div className="flex-1" />

      {(busy || run.running) && (
        <button
          type="button"
          // Stops whatever this bar is currently reporting on. The run takes precedence for the
          // same reason it does above: it is the one you can leave behind.
          onClick={run.running ? run.stop : stop}
          disabled={run.running && (!run.runId || run.stopping)}
          className={cn(
            "disabled:opacity-50",
            "flex items-center gap-1.5 rounded-chip border border-hairline px-2 py-0.5",
            "transition-colors duration-1 ease-out hover:text-foreground",
            focusRing,
          )}
        >
          <Square className="h-3 w-3" />
          {t("composer.stop")}
        </button>
      )}

      <ServerBadge />
      <VersionBadge />
    </footer>
  );
}

function Separator() {
  return (
    <span aria-hidden className="text-muted-foreground/40">
      ·
    </span>
  );
}
