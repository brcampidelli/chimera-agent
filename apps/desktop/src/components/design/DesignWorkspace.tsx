import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { PanelRight } from "lucide-react";

import { DesignCanvas } from "@/components/design/DesignCanvas";
import { DesignPresentation } from "@/components/design/DesignPresentation";
import { DesignEmpty } from "@/components/design/DesignEmpty";
import { DesignInspector } from "@/components/design/DesignInspector";
import type { HtmlElementSelection } from "@/components/design/HtmlArtboardFrame";
import { DesignToolbar } from "@/components/design/DesignToolbar";
import { ShortcutsHelp } from "@/components/design/ShortcutsHelp";
import type { Dispatch } from "@/components/design/useCanvasPointer";
import { focusRing } from "@/components/ui/focus";
import { Tabs } from "@/components/ui/tabs";
import { newArtboard, newPage, type ArtboardPreset } from "@/lib/design/defaults";
import { alignOps, deleteOps, duplicateOps } from "@/lib/design/edits";
import { fitViewport, layoutArtboards, boundsOf, zoomAt, type AlignMode, type Viewport } from "@/lib/design/geometry";
import { afterRedo, afterUndo, canRedo, canUndo, emptyHistory, peekRedo, peekUndo, record, type History } from "@/lib/design/history";
import { isIndexPage, placeOf, type DesignDocument, type DesignOperation } from "@/lib/design/model";
import { applyOps, DesignOpError, type ApplyResult } from "@/lib/design/ops";
import type { DesignCommand, DesignTool } from "@/lib/design/tools";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Where a jump goes: the element if it still exists, otherwise the point on its artboard. */
interface JumpTarget {
  ids: string[];
  artboardId?: string;
  x?: number;
  y?: number;
}

export interface DesignWorkspaceProps {
  document: DesignDocument;
  assetData?: ReadonlyMap<string, string>;
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
  /**
   * The undo history, when the owner keeps it (controlled). The Code screen does, because edits
   * that did not start on this canvas (an approved proposal, the agent's save, a restored version)
   * belong on the same stack. Absent, the workspace keeps its own.
   */
  history?: History;
  onHistoryChange?: (next: History) => void;
  /** Step a server-side entry (`HistoryEntry.revisions`): restore that revision on the server.
   *  Resolves false when it could not, and the history is left as it was. */
  onRevisionStep?: (revision: number) => Promise<boolean>;
  /** Told whenever the selection changes, e.g. so "Refine selection" knows what is selected. */
  onSelectionChange?: (ids: string[]) => void;
  onHtmlSelectionChange?: (selection: (HtmlElementSelection & { artboardId: string }) | null) => void;
  /** Select these elements (and show their page) whenever `key` changes: "jump to element". */
  select?: { ids: string[]; artboardId?: string; x?: number; y?: number; key: number };
  /** Show this artboard (and its page) whenever `key` changes: "open screen" from outside the canvas. */
  openArtboard?: { id: string; key: number };
  /** Extra tabs beside the inspector (the design panel). They show even before the first artboard,
   *  because that is when a brief is written. */
  panels?: readonly WorkspacePanel[];
}

export interface WorkspacePanel {
  id: string;
  label: string;
  content: ReactNode;
}

const ZOOM_STEP = 1.25;

/**
 * The comment filter labels are spelled out one by one, not built from the filter name. The
 * reachability test (`i18n.reachable.test.ts`) only sees a key that appears literally in the source,
 * so `t(`design.comment.${filter}`)` would report these keys as dead.
 */
const COMMENT_FILTER_LABELS = [
  { filter: "open", label: "design.comment.open" },
  { filter: "resolved", label: "design.comment.resolved" },
  { filter: "all", label: "design.comment.all" },
] as const;

/**
 * Chimera Design's editing surface: toolbar, canvas, inspector, shortcut sheet and a live region,
 * around a document someone else owns. The Code screen's viewer hosts it (`DesignPane`), wires
 * `onOps` to autosave and keeps the history, so the agent's edits are steps on the same stack.
 */
export function DesignWorkspace({
  document: doc,
  assetData = new Map(),
  onOps,
  author,
  compactBelow = 720,
  history: controlledHistory,
  onHistoryChange,
  onRevisionStep,
  onSelectionChange,
  select,
  openArtboard,
  panels,
  onHtmlSelectionChange,
}: DesignWorkspaceProps) {
  const t = useT();
  const rootRef = useRef<HTMLDivElement>(null);
  const docRef = useRef(doc);
  docRef.current = doc;
  const [pageId, setPageId] = useState<string | undefined>(doc.pages[0]?.id);
  const [artboardId, setArtboardId] = useState<string | undefined>(doc.pages[0]?.artboards[0]?.id);
  const [selection, setSelectionState] = useState<string[]>([]);
  const [htmlSelection, setHtmlSelectionState] = useState<(HtmlElementSelection & { artboardId: string }) | null>(null);
  const [htmlNotice, setHtmlNotice] = useState(false);
  const htmlSelectionRef = useRef(onHtmlSelectionChange);
  htmlSelectionRef.current = onHtmlSelectionChange;
  const setHtmlSelection = (value: (HtmlElementSelection & { artboardId: string }) | null): void => {
    setHtmlSelectionState(value);
    setHtmlNotice(false);
    htmlSelectionRef.current?.(value);
  };
  const onSelectionRef = useRef(onSelectionChange);
  onSelectionRef.current = onSelectionChange;
  function setSelection(ids: string[]): void {
    setSelectionState(ids);
    if (ids.length) setHtmlSelection(null);
    onSelectionRef.current?.(ids);
  }
  const [tool, setTool] = useState<DesignTool>("select");
  const [viewport, setViewport] = useState<Viewport>({ x: 40, y: 40, zoom: 0.5 });
  const [ownHistory, setOwnHistory] = useState<History>(emptyHistory);
  const history = controlledHistory ?? ownHistory;
  const historyRef = useRef(history);
  historyRef.current = history;
  function setHistory(next: History): void {
    historyRef.current = next;
    if (onHistoryChange) onHistoryChange(next);
    else setOwnHistory(next);
  }
  const [panel, setPanel] = useState<string>("inspector");
  const [helpOpen, setHelpOpen] = useState(false);
  const [width, setWidth] = useState(0);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [presenting, setPresenting] = useState(false);
  const [presentIndex, setPresentIndex] = useState(0);
  const [deviceWidth, setDeviceWidth] = useState<number | null>(null);
  const [drawStyle, setDrawStyle] = useState<"pencil" | "marker">("pencil");
  const [commentsVisible, setCommentsVisible] = useState(true);
  const [commentFilter, setCommentFilter] = useState<"open" | "resolved" | "all">("open");

  const page = doc.pages.find((p) => p.id === pageId) ?? doc.pages[0];
  const currentArtboardIndex = page?.artboards.findIndex((board) => board.id === artboardId) ?? -1;
  function navigateToArtboard(id: string): void {
    const source = doc.pages.flatMap((item) => item.artboards).find((item) => item.id === id);
    const sourcePage = doc.pages.find((item) => item.artboards.some((entry) => entry.id === source?.id));
    if (!source || !sourcePage) return;
    setPageId(sourcePage.id);
    setArtboardId(source.id);
    setHtmlSelection(null);
    setSelection([]);
    fit(docRef.current, sourcePage.id);
  }

  function activateArtboard(id: string): void {
    if (id === artboardId) return;
    setArtboardId(id);
    setHtmlSelection(null);
  }

  function selectArtboard(id: string): void {
    navigateToArtboard(id);
  }
  useEffect(() => {
    if (htmlSelection && !doc.pages.some((item) => item.artboards.some((board) => board.id === htmlSelection.artboardId && board.html != null))) {
      setHtmlSelection(null);
    }
  }, [doc, htmlSelection]);
  const liveSelection = selection.filter((id) => doc.nodes[id] !== undefined);
  const compact = width > 0 && width < compactBelow;

  // "Jump to element": select it and show its page. A plain function, not state, so a jump from
  // a comment and a jump requested by the parent cannot shadow each other.
  function jumpTo(target: JumpTarget): void {
    const ids = target.ids.filter((id) => docRef.current.nodes[id] !== undefined);
    const where = ids[0] === undefined ? undefined : placeOf(docRef.current, ids[0]);
    const pageForComment = target.artboardId
      ? docRef.current.pages.find((candidate) => candidate.artboards.some((board) => board.id === target.artboardId))
      : undefined;
    if (where) setPageId(where.page.id);
    else if (pageForComment) setPageId(pageForComment.id);
    const board = where?.artboard ?? pageForComment?.artboards.find((candidate) => candidate.id === target.artboardId);
    const root = rootRef.current;
    if (board && root) {
      const ownerPage = where?.page ?? pageForComment;
      const origin = ownerPage ? layoutArtboards(ownerPage).get(board.id) : undefined;
      const node = ids[0] === undefined ? undefined : docRef.current.nodes[ids[0]];
      if (origin) {
        const centreX = origin.x + (node?.frame.x ?? target.x ?? 0) + (node?.frame.width ?? 0) / 2;
        const centreY = origin.y + (node?.frame.y ?? target.y ?? 0) + (node?.frame.height ?? 0) / 2;
        if (root.clientWidth > 0 && root.clientHeight > 0) {
          setViewport((current) => ({ ...current, x: root.clientWidth / 2 - centreX * current.zoom, y: root.clientHeight / 2 - centreY * current.zoom }));
        }
      }
    }
    setSelection(ids);
    if (ids.length) announce(t("design.announce.selected", { name: docRef.current.nodes[ids[0]].name }));
    else announce(t("design.announce.none"));
  }

  useEffect(() => {
    if (select) jumpTo(select);
    // Only on a new request: `key` is what says one was made.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [select?.key]);

  useEffect(() => {
    if (openArtboard) navigateToArtboard(openArtboard.id);
    // Only on a new request: `key` is what says one was made.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openArtboard?.key]);

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
    setHistory(record(historyRef.current, { forward: ops, inverse: result.inverse, label: meta.label, coalesceKey: meta.coalesceKey, at: Date.now() }));
    if (!meta.coalesceKey) announce(t(meta.label));
    return true;
  };

  function step(direction: "undo" | "redo"): void {
    const h = historyRef.current;
    const entry = direction === "undo" ? peekUndo(h) : peekRedo(h);
    if (!entry) return announce(t(direction === "undo" ? "design.announce.nothingToUndo" : "design.announce.nothingToRedo"));
    if (entry.revisions) {
      // An agent edit or a restore, made on the server: undone by restoring the revision it started
      // from. The owner of the document does that (`onRevisionStep`); without one, it is refused.
      const target = entry.revisions.from;
      void (onRevisionStep?.(target) ?? Promise.resolve(false)).then((ok) => {
        if (!ok) return announce(t("design.error.stepFailed"));
        const now = historyRef.current;
        setHistory(direction === "undo" ? afterUndo(now, []) : afterRedo(now, []));
        announce(t(direction === "undo" ? "design.announce.undone" : "design.announce.redone", { action: t(entry.label) }));
      });
      return;
    }
    const result = apply(entry.inverse);
    if (!result) return;
    setHistory(direction === "undo" ? afterUndo(h, result.inverse) : afterRedo(h, result.inverse));
    announce(t(direction === "undo" ? "design.announce.undone" : "design.announce.redone", { action: t(entry.label) }));
  }

  function fit(target: DesignDocument = docRef.current, id = page?.id): void {
    const el = rootRef.current;
    const p = target.pages.find((x) => x.id === id);
    const bounds = boundsOf(p ? [...layoutArtboards(p).values()] : []);
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

  function addPage(): void {
    const p = newPage(t("design.page.numbered", { n: docRef.current.pages.length + 1 }));
    if (dispatch([{ type: "page.add", page: p }], { label: "design.page.new" })) {
      setPageId(p.id);
      setSelection([]);
    }
  }

  const presentationBoards = page?.artboards ?? [];
  const closePresentation = useCallback(() => setPresenting(false), []);

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
  const comments = doc.comments ?? [];
  const visibleComments = comments.filter((comment) => commentFilter === "all" || comment.resolved === (commentFilter === "resolved"));
  const commentPanel: WorkspacePanel = {
    id: "comments",
    label: t("design.comment.panel"),
    content: (
      <section className="flex flex-col gap-3 p-3" aria-label={t("design.comment.panel")}>
        <div className="flex gap-1" role="group" aria-label={t("design.comment.filter")}>
          {COMMENT_FILTER_LABELS.map(({ filter, label }) => (
            <button key={filter} type="button" aria-pressed={commentFilter === filter} onClick={() => setCommentFilter(filter)} className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-surface-hover aria-pressed:bg-accent/15 aria-pressed:text-accent-ink">
              {t(label)}
            </button>
          ))}
        </div>
        {visibleComments.length === 0 ? <p className="text-sm text-muted-foreground">{t("design.comment.empty")}</p> : visibleComments.map((comment) => (
          <button key={comment.id} type="button" onClick={() => {
            jumpTo({ ids: comment.nodeId ? [comment.nodeId] : [], artboardId: comment.artboardId, x: comment.x, y: comment.y });
            setPanel("inspector");
            setInspectorOpen(true);
            setTool("select");
          }} className={cn("flex flex-col items-start gap-1 rounded-lg border border-hairline p-2 text-left hover:bg-surface-hover", focusRing, comment.resolved && "opacity-60")}>
            <span className="line-clamp-3 text-sm text-foreground">{comment.text}</span>
            <span className="text-xs text-muted-foreground">{comment.author === (author ?? t("design.comment.you")) ? t("design.comment.you") : comment.author} · {new Date(comment.createdAt).toLocaleDateString()}</span>
            {comment.resolved && <span className="text-xs text-muted-foreground">{t("design.comment.resolved")}</span>}
          </button>
        ))}
      </section>
    ),
  };
  const tabs: WorkspacePanel[] = [
    ...(empty
      ? [commentPanel]
      : [
          { id: "inspector", label: t("design.inspector.label"), content: <DesignInspector doc={doc} pageId={page?.id} selection={liveSelection} dispatch={dispatch} author={author} htmlSelection={htmlSelection} activeArtboardId={artboardId} onHtmlSelectionChange={setHtmlSelection} htmlNotice={htmlNotice} /> },
          commentPanel,
        ]),
    ...(panels ?? []),
  ];
  const shown = tabs.find((tab) => tab.id === panel) ?? tabs[0];
  const hasAside = shown !== undefined;

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
            pages={doc.pages}
            pageId={page?.id}
            onPageChange={(id) => {
              const nextPage = doc.pages.find((item) => item.id === id);
              setPageId(id);
              setArtboardId(nextPage?.artboards.find((board) => board.thumbnailOf === undefined)?.id ?? nextPage?.artboards[0]?.id);
              setSelection([]);
              fit(docRef.current, nextPage?.id);
            }}
            onAddPage={addPage}
            onPresent={() => { setPresentIndex(0); setPresenting((value) => !value); }}
            presenting={presenting}
            onDeviceWidth={setDeviceWidth}
            onZoom={(scale) => setViewport((vp) => ({ ...vp, zoom: scale }))}
            onToggleComments={() => setCommentsVisible((visible) => !visible)}
            commentsVisible={commentsVisible}
            deviceWidth={deviceWidth}
            drawStyle={drawStyle}
            onDrawStyle={setDrawStyle}
          />
        </div>
        {page && page.artboards.length > 1 && (
          <div role="group" aria-label={t("design.screens.label")} className="flex max-w-48 items-center gap-1 overflow-x-auto">
            {page.artboards.map((board) => (
              <button key={board.id} type="button" aria-current={board.id === artboardId ? "page" : undefined} onClick={() => selectArtboard(board.id)} className={cn("shrink-0 rounded-lg px-2 py-1 text-xs", board.id === artboardId ? "bg-accent/15 text-accent-ink" : "text-muted-foreground hover:bg-surface-hover", focusRing)}>{board.name}</button>
            ))}
            <button type="button" aria-label={t("design.screens.previous")} onClick={() => { const index = Math.max(0, currentArtboardIndex - 1); if (page.artboards[index]) selectArtboard(page.artboards[index].id); }} className={cn("rounded-lg px-2 py-1 text-xs", focusRing)}>‹</button>
            <button type="button" aria-label={t("design.screens.next")} onClick={() => { const index = Math.min(page.artboards.length - 1, currentArtboardIndex + 1); if (page.artboards[index]) selectArtboard(page.artboards[index].id); }} className={cn("rounded-lg px-2 py-1 text-xs", focusRing)}>›</button>
          </div>
        )}
        {doc.pages.some(isIndexPage) && page && !isIndexPage(page) ? (
          <button type="button" onClick={() => { const index = doc.pages.find(isIndexPage); if (index) { setPageId(index.id); setArtboardId(index.artboards[0]?.id); setSelection([]); fit(docRef.current, index.id); } }} className={cn("rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-surface-hover", focusRing)}>{t("design.index.open")}</button>
        ) : null}
        {compact && hasAside && (
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
              assetData={assetData}
              pageId={page.id}
              selection={liveSelection}
              onNavigate={navigateToArtboard}
              onSelectionChange={setSelection}
              tool={tool}
              onToolChange={setTool}
              viewport={viewport}
              onViewportChange={setViewport}
              dispatch={dispatch}
              onCommand={onCommand}
              announce={announce}
              author={author ?? t("design.comment.you")}
              onAddFrame={() => addArtboard({ id: "desktop", labelKey: "design.preset.desktop", width: 1440, height: 900 })}
              commentsVisible={commentsVisible}
              drawStyle={drawStyle}
              previewWidth={deviceWidth}
              editingArtboardId={!presenting ? artboardId : undefined}
              onActivateArtboard={activateArtboard}
              onHtmlSelectionRejected={() => {
                setHtmlSelection(null);
                setHtmlNotice(true);
              }}
              onHtmlElementSelect={(selected) => {
                if (selected && artboardId) setHtmlSelection({ ...selected, rect: { x: 0, y: 0, width: 0, height: 0 }, artboardId });
                else setHtmlSelection(null);
                setSelection([]);
              }}
            />
          )}
        </div>
        {hasAside && (!compact || inspectorOpen) && (
          <aside
            id="design-inspector"
            className={cn(
              "flex w-72 shrink-0 flex-col border-l border-hairline bg-card",
              compact && "absolute inset-y-0 right-0 z-20 shadow-elev-lg",
            )}
          >
            {tabs.length > 1 && (
              <Tabs
                aria-label={t("design.panel.tabs")}
                items={tabs.map((tab) => ({ value: tab.id, label: tab.label }))}
                value={shown.id}
                onChange={setPanel}
                className="shrink-0 px-2"
              />
            )}
            <div className="min-h-0 flex-1 overflow-y-auto">{shown.content}</div>
          </aside>
        )}
      </div>
      <div role="status" aria-live="polite" className="sr-only">
        {message}
      </div>
      {presenting && page && presentationBoards.length > 0 ? (
        <DesignPresentation
          doc={doc}
          pageId={page.id}
          boards={presentationBoards}
          index={presentIndex}
          onIndex={setPresentIndex}
          onClose={closePresentation}
          viewport={viewport}
        />
      ) : null}
      <ShortcutsHelp open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}
