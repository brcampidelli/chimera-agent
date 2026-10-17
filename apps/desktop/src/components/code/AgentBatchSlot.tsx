import { useCallback } from "react";
import { useDndContext, useDroppable } from "@dnd-kit/core";
import { Boxes, X } from "lucide-react";

import { batchSummary } from "@/components/Agents";
import { PanelFrame, type DockUnit } from "@/components/shell/Dock";
import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { batchCounts, useAgentBatch, useOptionalAgentBatch, type BatchStart } from "@/lib/agent-batch";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { cn } from "@/lib/utils";

const PANEL = "agents.batch" as const;

/** The batch board as a lone dock unit: the frame every panel has, with nothing grouped into it. */
const UNIT: DockUnit = { key: PANEL, members: [PANEL], anchor: null };

/**
 * Start a confirmed batch, and show its board if the last one was closed.
 *
 * Confirming a batch is asking to watch it, so a board the person closed on the previous batch opens
 * again. One they minimised or moved stays minimised or moved: that is where they put it, and the
 * header still says where the batch is.
 */
export function useStartAgentBatch(): (req: BatchStart) => void {
  const { start } = useAgentBatch();
  const { layout, dispatch } = useLayout();
  const closed = layout.panels[PANEL].mode === "closed";
  return useCallback(
    (req: BatchStart) => {
      start(req);
      if (closed) dispatch({ type: "set-mode", panel: PANEL, mode: "open" });
    },
    [start, dispatch, closed],
  );
}

/**
 * Where the batch board sits in the main column, under the conversation.
 *
 * - Its home is here. Drawn in the frame every dock panel has, it minimises to its header (which keeps
 *   a one-line summary), closes, and moves to a dock and back.
 * - Closed while a batch exists, it leaves a chip here that brings it back, with the same cards: the
 *   batch lives in `AgentBatchProvider`, so closing hid a view and stopped nothing. Once the batch has
 *   ended, the chip also lets its results go.
 * - Moved to a dock, nothing is drawn here except, while it is being dragged, the place to drop it home.
 *
 * `movable: false` is for a conversation in a window of its own: that window has no docks, so the board
 * always draws here, whatever the person's layout says about the main window.
 */
export function AgentBatchSlot({ movable = true }: { movable?: boolean }) {
  const api = useOptionalAgentBatch();
  const { layout } = useLayout();
  const state = layout.panels[PANEL];
  if (!api?.run) return null;
  if (state.mode === "closed") return <BatchChip />;
  if (state.zone === "center" || !movable) return <PanelFrame unit={UNIT} zone="center" movable={movable} />;
  return <HomeDrop />;
}

/** The way back to a closed board: "Batch: 6 running". */
function BatchChip() {
  const t = useT();
  const api = useAgentBatch();
  const { dispatch } = useLayout();
  const run = api.run;
  if (!run) return null;
  const summary = batchSummary(batchCounts(run), t);
  const chip = cn(
    "flex items-center gap-1.5 rounded-chip border border-hairline px-2 py-0.5 text-xs text-muted-foreground",
    "transition-colors duration-1 ease-out hover:text-foreground",
    focusRing,
  );
  return (
    <div data-batch-chip className="flex shrink-0 items-center justify-end gap-1 border-t border-hairline px-3 py-1.5">
      <button
        type="button"
        // The name carries the sentence and contains the visible words, so a voice user can say them.
        aria-label={t("agents.chip.show", { summary })}
        onClick={() => dispatch({ type: "set-mode", panel: PANEL, mode: "open" })}
        className={chip}
      >
        <Boxes className="h-3 w-3" aria-hidden />
        {t("agents.chip", { summary })}
      </button>
      {/* A running batch cannot be dismissed: that would be the batch running with nothing on screen. */}
      {run.running ? null : (
        <Tooltip label={t("agents.chip.dismiss")}>
          <button type="button" aria-label={t("agents.chip.dismiss")} onClick={api.dismiss} className={chip}>
            <X className="h-3 w-3" aria-hidden />
          </button>
        </Tooltip>
      )}
    </div>
  );
}

/** While the board is dragged from a dock, the main column shows where it can be dropped back home. */
function HomeDrop() {
  const t = useT();
  const { active } = useDndContext();
  const dragging = active?.id === PANEL;
  const { setNodeRef, isOver } = useDroppable({ id: "zone:center", disabled: !dragging });
  if (!dragging) return null;
  return (
    <div ref={setNodeRef} className={cn("shrink-0 border-t border-hairline p-3", isOver && "bg-accent/10")}>
      <p className="rounded-chip border border-dashed border-hairline px-3 py-2 text-center text-xs text-muted-foreground">
        {t("layout.dropHere")}
      </p>
    </div>
  );
}
