import { useEffect, useRef, useState } from "react";
import { PanelRight } from "lucide-react";

import { DesignCanvas } from "@/components/design/DesignCanvas";
import { DesignEmpty } from "@/components/design/DesignEmpty";
import { DesignInspector } from "@/components/design/DesignInspector";
import { DesignToolbar } from "@/components/design/DesignToolbar";
import { ShortcutsHelp } from "@/components/design/ShortcutsHelp";
import type { Dispatch } from "@/components/design/useCanvasPointer";
import { focusRing } from "@/components/ui/focus";
import { newArtboard, newPage, type ArtboardPreset } from "@/lib/design/defaults";
import { alignOps, deleteOps, duplicateOps } from "@/lib/design/edits";
import { fitViewport, layoutArtboards, boundsOf, zoomAt, type AlignMode, type Viewport } from "@/lib/design/geometry";
import { afterRedo, afterUndo, canRedo, canUndo, emptyHistory, peekRedo, peekUndo, record, type History } from "@/lib/design/history";
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import { applyOps, DesignOpError, type ApplyResult } from "@/lib/design/ops";
import type { DesignCommand, DesignTool } from "@/lib/design/tools";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface DesignWorkspaceProps {
  document: DesignDocument;
  /**
   * Called with every batch that was applied locally, and its result. The owner keeps
   * `result.document` as the new `document` (synchronously — the next edit is applied on top of
   * it) and may send `ops` to the server. Undo and redo arrive here as ordinary batches.
   */
  onOps: (ops: DesignOperation[], result: ApplyResult) => void;
  /** Name stamped on new comments. */
  author?: string;
  /** Below this container width the inspector folds into a toggle. */
  compactBelow?: number;
}

const ZOOM_STEP = 1.25;

/**
 * Chimera Design's editing surface: toolbar, canvas, inspector, shortcut sheet and a live region,
 * around a document someone else owns. Not mounted anywhere yet — the Code screen's viewer will
 * host it and wire `onOps` to the design API, and the agent's edits will arrive the same way.
 */
export function DesignWorkspace({ document: doc, onOps, author, compactBelow = 720 }: DesignWorkspaceProps) {
  const t = useT();
  const rootRef = useRef<HTMLDivElement>(null);
  const docRef = useRef(doc);
  docRef.current = doc;
  const [pageId, setPageId] = useState<string | undefined>(doc.pages[0]?.id);
  const [selection, setSelection] = useState<string[]>([]);
  const [tool, setTool] = useState<DesignTool>("select");
  const [viewport, setViewport] = useState<Viewport>({ x: 40, y: 40, zoom: 0.5 });
  const [history, setHistory] = useState<History>(emptyHistory);
  const historyRef = useRef(history);
  historyRef.current = history;
  const [helpOpen, setHelpOpen] = useState(false);
  const [width, setWidth] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [message, setMessage] = useState("");

  const page = doc.pages.find((p) => p.id === pageId) ?? doc.pages[0];
  const liveSelection = selection.filter((id) => doc.nodes[id] !== undefined);
  const compact = width > 0 && width < compactBelow;

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  function announce(text: string): void {
    // Re-set even when the text repeats ("Moved", "Moved"), so the region speaks again.
    setMessage("");
    queueMicrotask(() => setMessage(text));
  }

  function apply(ops: DesignOperation[]): ApplyResult | undefined {
    try {
      const result = applyOps(docRef.current, ops);
      docRef.current = result.document;
      onOps(ops, result);
      return result;
    } catch (error) {
      if (!(error instanceof DesignOpError)) throw error;
      announce(t("design.error.refused"));
      return undefined;
    }
  }

  const dispatch: Dispatch = (ops, meta) => {
    const result = apply(ops);
    if (!result) return false;
    const next = record(historyRef.current, { forward: ops, inverse: result.inverse, label: meta.label, coalesceKey: meta.coalesceKey, at: Date.now() });
    historyRef.current = next;
    setHistory(next);
    if (!meta.coalesceKey) announce(t(meta.label));
    return true;
  };

  function step(direction: "undo" | "redo"): void {
    const h = historyRef.current;
    const entry = direction === "undo" ? peekUndo(h) : peekRedo(h);
    if (!entry) return announce(t(direction === "undo" ? "design.announce.nothingToUndo" : "design.announce.nothingToRedo"));
    const result = apply(entry.inverse);
    if (!result) return;
    const next = direction === "undo" ? afterUndo(h, result.inverse) : afterRedo(h, result.inverse);
    historyRef.current = next;
    setHistory(next);
    announce(t(direction === "undo" ? "design.announce.undone" : "design.announce.redone", { action: t(entry.label) }));
  }

  function fit(target: DesignDocument = docRef.current, id = page?.id): void {
    const el = rootRef.current;
    const p = target.pages.find((x) => x.id === id);
    const bounds = boundsOf([...layoutArtboards(p).values()]);
    if (!bounds) return;
    setViewport(fitViewport(bounds, el?.clientWidth ?? 0, el?.clientHeight ?? 0));
  }

  function zoomBy(factor: number): void {
    const el = rootRef.current;
    const centre = { x: (el?.clientWidth ?? 0) / 2, y: (el?.clientHeight ?? 0) / 2 };
    setViewport((vp) => zoomAt(vp, factor, centre));
  }

  function onCommand(command: DesignCommand): void {
    const d = docRef.current;
    switch (command) {
      case "undo":
      case "redo":
        return step(command);
      case "duplicate": {
        const { ops, ids } = duplicateOps(d, liveSelection);
        if (ops.length && dispatch(ops, { label: "design.did.duplicate" })) setSelection(ids);
        return;
      }
      case "delete": {
        const ops = deleteOps(d, liveSelection);
        if (ops.length && dispatch(ops, { label: "design.did.delete" })) setSelection([]);
        return;
      }
      case "zoomIn":
        return zoomBy(ZOOM_STEP);
      case "zoomOut":
        return zoomBy(1 / ZOOM_STEP);
      case "zoomFit":
        return fit();
      case "help":
        return setHelpOpen(true);
    }
  }

  function onAlign(mode: AlignMode): void {
    const ops = alignOps(docRef.current, liveSelection, mode);
    if (ops.length) dispatch(ops, { label: "design.did.align" });
  }

  function addArtboard(preset: ArtboardPreset): void {
    const d = docRef.current;
    const ops: DesignOperation[] = [];
    let target = page;
    if (!target) {
      target = newPage(t("design.page.default"));
      ops.push({ type: "page.add", page: target });
    }
    const name = t("design.artboard.default", { n: target.artboards.length + 1 });
    ops.push({ type: "artboard.add", pageId: target.id, artboard: newArtboard(d, preset, name) });
    if (dispatch(ops, { label: "design.did.artboard" })) {
      setPageId(target.id);
      fit(docRef.current, target.id);
    }
  }

  const empty = !page || page.artboards.length === 0;
  const inspector = <DesignInspector doc={doc} pageId={page?.id} selection={liveSelection} dispatch={dispatch} />;

  return (
    <div ref={rootRef} className="flex h-full min-h-0 w-full flex-col bg-background">
      <div className="flex items-center gap-1">
        <div className="min-w-0 flex-1">
          <DesignToolbar
            tool={tool}
            onToolChange={setTool}
            onCommand={onCommand}
            onAlign={onAlign}
            zoom={viewport.zoom}
            canUndo={canUndo(history)}
            canRedo={canRedo(history)}
            hasSelection={liveSelection.length > 0}
          />
        </div>
        {doc.pages.length > 1 && (
          <select
            aria-label={t("design.page.label")}
            value={page?.id}
            onChange={(e) => {
              setPageId(e.target.value);
              setSelection([]);
            }}
            className="field mr-2 h-8 max-w-40 px-2 text-sm"
          >
            {doc.pages.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        {compact && !empty && (
          <button
            type="button"
            aria-expanded={inspectorOpen}
            aria-controls="design-inspector"
            aria-label={t("design.inspector.toggle")}
            onClick={() => setInspectorOpen((open) => !open)}
            className={cn("mr-2 inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors duration-1 ease-out hover:bg-surface-hover", inspectorOpen && "bg-accent/15 text-accent-ink", focusRing)}
          >
            <PanelRight className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>
      <div className="relative flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          {empty ? (
            <DesignEmpty onAdd={addArtboard} />
          ) : (
            <DesignCanvas
              document={doc}
              pageId={page.id}
              selection={liveSelection}
              onSelectionChange={setSelection}
              tool={tool}
              onToolChange={setTool}
              viewport={viewport}
              onViewportChange={setViewport}
              dispatch={dispatch}
              onCommand={onCommand}
              announce={announce}
              author={author ?? t("design.comment.you")}
            />
          )}
        </div>
        {!empty && (!compact || inspectorOpen) && (
          <aside
            id="design-inspector"
            className={cn(
              "w-72 shrink-0 overflow-y-auto border-l border-hairline bg-card",
              compact && "absolute inset-y-0 right-0 z-20 shadow-elev-lg",
            )}
          >
            {inspector}
          </aside>
        )}
      </div>
      <div role="status" aria-live="polite" className="sr-only">
        {message}
      </div>
      <ShortcutsHelp open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}
