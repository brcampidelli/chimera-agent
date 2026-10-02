import { useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Square } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { listRunningTurns, stopCodeTurn, type RunningTurn } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The last part of a folder path, which is how the sidebar names a project. */
function projectName(workspace: string): string {
  return workspace.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || workspace;
}

/**
 * The coding turns running in other conversations, from the status bar, each with its own Stop.
 *
 * The bar describes one turn: the one on screen. With several conversations working at once the others
 * were invisible from here, and stopping one meant opening its conversation first. This lists them from
 * the server (`GET /api/code/turns/running`, the query key the sidebar already polls) and stops one with
 * `POST /api/code/turns/{id}/stop`, the same route the conversation's own Stop uses.
 *
 * Nothing at zero, for the reason PendingApprovals gives about an indicator that is always on screen.
 */
export function RunningElsewhere({ current }: { current: string | null }) {
  const t = useT();
  const qc = useQueryClient();
  const [stopping, setStopping] = useState<ReadonlySet<string>>(() => new Set());
  const running = useQuery({ queryKey: ["code-turns-running"], queryFn: listRunningTurns, refetchInterval: 4000 });
  const others = (running.data ?? []).filter((turn) => turn.turn_id !== current);
  if (others.length === 0) return null;

  async function stop(turn: RunningTurn) {
    setStopping((prev) => new Set(prev).add(turn.turn_id));
    try {
      await stopCodeTurn(turn.turn_id);
    } catch {
      // A 404 is a turn that ended on its own meanwhile; the next poll drops it either way.
    }
    void qc.invalidateQueries({ queryKey: ["code-turns-running"] });
  }

  const label = t("running.elsewhere.label", { n: others.length });
  const item = cn("flex items-center gap-2 rounded-md px-2 py-1 text-xs", "data-highlighted:bg-surface-hover");
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={label}
        title={label}
        className={cn(
          "flex items-center gap-1.5 rounded-chip border border-hairline px-2 py-0.5",
          "transition-colors duration-1 ease-out hover:text-foreground",
          focusRing,
        )}
      >
        {t("running.elsewhere", { n: others.length })}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content side="top" align="end" sideOffset={6} className="overlay floating z-50 min-w-72 p-1 text-foreground">
          <Menu.Label className="px-2 py-1 text-xs font-medium text-muted-foreground">{label}</Menu.Label>
          {others.map((turn) => {
            const busy = stopping.has(turn.turn_id);
            return (
              <div key={turn.turn_id} className={item}>
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{projectName(turn.workspace) || "—"}</span>
                  {" · "}
                  <span className="text-muted-foreground">{turn.message}</span>
                </span>
                <Menu.Item
                  className={cn(
                    "flex items-center gap-1 rounded-chip border border-hairline px-1.5 py-0.5 outline-hidden",
                    "data-highlighted:bg-surface-hover disabled:opacity-50",
                  )}
                  disabled={busy}
                  aria-label={`${t("running.elsewhere.stop")}: ${turn.message}`}
                  onSelect={(e) => {
                    e.preventDefault();
                    void stop(turn);
                  }}
                >
                  <Square className="h-3 w-3" aria-hidden />
                  {busy ? t("running.elsewhere.stopping") : t("composer.stop")}
                </Menu.Item>
              </div>
            );
          })}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
