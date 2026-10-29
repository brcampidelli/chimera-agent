import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Download, History as HistoryIcon, LayoutGrid, Lock, Palette, Share2, X } from "lucide-react";

import { DesignAssistant } from "@/components/design/DesignAssistant";
import { Elapsed } from "@/components/code/Elapsed";
import { ExportDialog, RenameDialog, ShareLinkDialog, VersionsDialog } from "@/components/design/DesignDialogs";
import { DesignList } from "@/components/design/DesignList";
import { DesignMenu, DesignMenuItem } from "@/components/design/DesignMenu";
import { DesignTokensPanel } from "@/components/design/DesignTokensPanel";
import { DesignWorkspace } from "@/components/design/DesignWorkspace";
import { SaveIndicator } from "@/components/design/SaveIndicator";
import { useDesignSession, AUTOSAVE_MS } from "@/components/design/useDesignSession";
import { MaximizeButton } from "@/components/shell/Maximize";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { Spinner } from "@/components/ui/panel";
import { Tooltip } from "@/components/ui/tooltip";
import { useOptionalToast } from "@/components/ui/toast";
import { record } from "@/lib/design/history";
import { applyDesignOps, createDesign, deleteDesign } from "@/lib/api";
import { shareLinkFor } from "@/lib/design/deepLink";
import { newId } from "@/lib/design/model";
import type { DesignOperation } from "@/lib/design/model";
import type { CodeDesignFrame } from "@/lib/design/frames";
import { applyOps, DesignOpError } from "@/lib/design/ops";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** What the viewer shows: one design, or the list (`id: null`), optionally with a brief to start from.
 *  `key` changes on every request, so asking again for the same design still lands. */
export interface DesignTarget {
  id: string | null;
  brief?: string;
  key: number;
}

/** The agent saved a design (a `design` frame on the turn's stream). */
export interface AgentEdit {
  documentId: string;
  revision: number;
  frame?: CodeDesignFrame;
  key: number;
}

const iconButton = cn(
  "inline-flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground transition-colors duration-1 ease-out hover:bg-surface-hover hover:text-foreground",
  focusRing,
);

function HeaderButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <Tooltip label={label}>
      <button type="button" aria-label={label} onClick={onClick} className={iconButton}>
        {children}
      </button>
    </Tooltip>
  );
}

/** The header's title is the heading; `actions` replaces its text with a control (the title menu).
 *  While the agent is still drawing, the title gives way to the "Creating…" status and its elapsed time. */
function Header({
  title,
  actions,
  children,
  onList,
  onClose,
  onShare,
  drawingStartedAt,
}: {
  title: string;
  actions?: ReactNode;
  children?: ReactNode;
  onList?: () => void;
  onClose: () => void;
  onShare?: () => void;
  drawingStartedAt?: number;
}) {
  const t = useT();
  const name = t("design.pane.name");
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-hairline px-3 py-2">
      <Palette className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
      {drawingStartedAt !== undefined ? (
        <span className="flex min-w-0 flex-1 items-center gap-2 truncate text-sm font-medium text-foreground">
          {t("design.pane.creating")}
          <Elapsed startedAt={drawingStartedAt} testId="design-drawing-elapsed" />
        </span>
      ) : (
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground" title={title}>
          {actions ?? title}
        </h2>
      )}
      {onShare ? (
        <HeaderButton label={t("design.header.share")} onClick={onShare}>
          <Share2 className="h-3.5 w-3.5" aria-hidden="true" />
        </HeaderButton>
      ) : null}
      {children}
      {onList ? (
        <HeaderButton label={t("design.pane.list")} onClick={onList}>
          <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
        </HeaderButton>
      ) : null}
      <MaximizeButton panel="viewer" name={name} />
      <HeaderButton label={t("layout.card.close", { name })} onClick={onClose}>
        <X className="h-3.5 w-3.5" aria-hidden="true" />
      </HeaderButton>
    </div>
  );
}

function DesignEditor({
  id,
  brief,
  agentEdit,
  onList,
  onClose,
  onOpen,
  autosaveMs,
  drawingStartedAt,
}: {
  id: string;
  brief?: string;
  agentEdit?: AgentEdit | null;
  onList: () => void;
  onClose: () => void;
  onOpen: (id: string | null, brief?: string) => void;
  autosaveMs: number;
  drawingStartedAt?: number;
}) {
  const t = useT();
  const toast = useOptionalToast();
  const session = useDesignSession(id, autosaveMs);
  const [selection, setSelection] = useState<string[]>([]);
  const [htmlSelection, setHtmlSelection] = useState<{ artboardId: string; index: number; tag: string; inlineStyle: string; text: string | null } | null>(null);
  const [jump, setJump] = useState<{ ids: string[]; key: number } | undefined>(undefined);
  const [openBoard, setOpenBoard] = useState<{ id: string; key: number } | undefined>(undefined);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [shareLink, setShareLink] = useState<string | null>(null);
  const { doc, agentEdited, agentFrame } = session;

  useEffect(() => {
    if (agentEdit && agentEdit.documentId === id) {
      if (agentEdit.frame) void agentFrame(agentEdit.frame);
      else void agentEdited(agentEdit.revision);
    }
    // Each frame once: `key` is what makes it a new one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentEdit?.key]);

  const onJump = useCallback((ids: string[]) => setJump((j) => ({ ids, key: (j?.key ?? 0) + 1 })), []);
  const onOpenArtboard = useCallback((id: string) => setOpenBoard((b) => ({ id, key: (b?.key ?? 0) + 1 })), []);

  /** A change made outside the canvas (the tokens panel): applied, queued and undoable like one. */
  const edit = useCallback(
    (ops: DesignOperation[], label: string): boolean => {
      if (!session.doc) return false;
      try {
        const result = applyOps(session.doc, ops);
        session.onOps(ops, result);
        session.setHistory(record(session.history, { forward: ops, inverse: result.inverse, label, at: Date.now() }));
        return true;
      } catch (error) {
        if (error instanceof DesignOpError) return false;
        throw error;
      }
    },
    [session],
  );

  const title = doc?.title ?? t("design.pane.name");

  const rename = (next: string) => {
    setRenameOpen(false);
    if (!next || !doc || next === title) return;
    const ops: DesignOperation[] = [{ type: "document.rename", title: next }];
    try {
      const result = applyOps(doc, ops);
      session.onOps(ops, result);
      session.setHistory(record(session.history, { forward: ops, inverse: result.inverse, label: "design.header.rename", at: Date.now() }));
    } catch (error) {
      if (!(error instanceof DesignOpError)) throw error;
    }
  };

  /** A copy is made in two steps (create, then apply the content). If the second fails, the empty copy
   *  is deleted so the list does not fill with orphans, and the person hears about it. */
  const duplicate = async () => {
    if (!doc) return;
    let copy: { id: string; revision: number } | null = null;
    try {
      copy = await createDesign(t("design.header.copyTitle", { title }));
      const pageIds = new Map(doc.pages.map((page) => [page.id, newId("page")]));
      const boardIds = new Map(doc.pages.flatMap((page) => page.artboards.map((board) => [board.id, newId("artboard")] as const)));
      const nodeIds = new Map<string, string>();
      const assignIds = (nodeId: string): void => {
        const node = doc.nodes[nodeId];
        if (!node || nodeIds.has(nodeId)) return;
        nodeIds.set(nodeId, newId("node"));
        if (node.type === "group") node.childIds.forEach(assignIds);
      };
      doc.pages.forEach((page) => page.artboards.forEach((board) => board.nodeIds.forEach(assignIds)));
      const ops: DesignOperation[] = [{ type: "tokens.replace", tokens: doc.tokens }];
      for (const page of doc.pages) {
        ops.push({ type: "page.add", page: { id: pageIds.get(page.id)!, name: page.name, artboards: [] } });
        for (const board of page.artboards) {
          ops.push({ type: "artboard.add", pageId: pageIds.get(page.id)!, artboard: { ...board, id: boardIds.get(board.id)!, nodeIds: [] } });
          for (const rootId of board.nodeIds) {
            const root = doc.nodes[rootId];
            if (!root) continue;
            const cloneNode = (source: typeof root): typeof root => {
              const cloned = { ...source, id: nodeIds.get(source.id)!, frame: { ...source.frame } };
              if (cloned.type === "group" && source.type === "group") cloned.childIds = source.childIds.map((child) => nodeIds.get(child)!);
              return cloned;
            };
            const descendants: typeof root[] = [];
            const collect = (childId: string): void => {
              const child = doc.nodes[childId];
              if (!child) return;
              descendants.push(cloneNode(child));
              if (child.type === "group") child.childIds.forEach(collect);
            };
            if (root.type === "group") root.childIds.forEach(collect);
            ops.push({ type: "node.add", pageId: pageIds.get(page.id)!, artboardId: boardIds.get(board.id)!, node: cloneNode(root), ...(root.type === "group" ? { children: descendants } : {}) });
          }
        }
      }
      for (const comment of doc.comments) {
        const artboardId = boardIds.get(comment.artboardId);
        if (artboardId) ops.push({ type: "comment.add", comment: { ...comment, id: newId("comment"), artboardId, ...(comment.nodeId ? { nodeId: nodeIds.get(comment.nodeId) } : {}) } });
      }
      await applyDesignOps(copy.id, copy.revision, ops);
      onOpen(copy.id);
    } catch {
      if (copy) await deleteDesign(copy.id).catch(() => undefined);
      toast(t("design.header.duplicateError"), "bad");
    }
  };

  /** Copy the local link to this design. Where the clipboard refuses, the link is shown to copy by hand. */
  const share = async () => {
    const link = shareLinkFor(window.location.href, id);
    try {
      await navigator.clipboard.writeText(link);
      toast(t("design.header.shareCopied"), "ok");
    } catch {
      setShareLink(link);
    }
  };

  const titleMenu = (
    <DesignMenu
      className="max-w-full text-sm font-medium text-foreground"
      trigger={<span className="truncate">{title}</span>}
      items={
        <>
          <DesignMenuItem onSelect={() => setRenameOpen(true)}>{t("design.header.rename")}</DesignMenuItem>
          <DesignMenuItem onSelect={() => void duplicate()}>{t("design.header.duplicate")}</DesignMenuItem>
          <DesignMenuItem onSelect={() => setExportOpen(true)}>{t("design.header.export")}</DesignMenuItem>
        </>
      }
    />
  );

  const header = (
    <Header title={title} actions={titleMenu} onList={onList} onClose={onClose} onShare={() => void share()} drawingStartedAt={drawingStartedAt}>
      {session.load === "ready" ? (
        <>
          <SaveIndicator
            state={session.save}
            note={session.note}
            revision={session.savedRevision}
            onRetry={() => void (session.save === "conflict" ? session.reload() : session.flush())}
          />
          <HeaderButton label={t("design.versions.title")} onClick={() => setVersionsOpen(true)}>
            <HistoryIcon className="h-3.5 w-3.5" aria-hidden="true" />
          </HeaderButton>
          <HeaderButton label={t("design.export.title")} onClick={() => setExportOpen(true)}>
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
          </HeaderButton>
        </>
      ) : null}
    </Header>
  );

  let body: ReactNode;
  if (session.load === "loading") body = <Spinner />;
  else if (session.load === "missing") body = <p className="p-4 text-sm text-muted-foreground">{t("design.pane.missing")}</p>;
  else if (session.load === "denied")
    body = (
      <p className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Lock className="h-4 w-4" aria-hidden="true" />
        {t("design.list.denied")}
      </p>
    );
  else if (session.load === "failed" || !doc) body = <ErrorState error={null} onRetry={() => void session.reload()} />;
  else
    body = (
      <DesignWorkspace
        document={doc}
        onOps={session.onOps}
        history={session.history}
        onHistoryChange={session.setHistory}
        onRevisionStep={session.stepRevision}
        onSelectionChange={setSelection}
        onHtmlSelectionChange={setHtmlSelection}
        select={jump}
        openArtboard={openBoard}
        compactBelow={900}
        panels={[
          {
            id: "assistant",
            label: t("design.panel.assistant"),
            content: <DesignAssistant designId={id} session={session} initialBrief={brief} selection={selection} htmlSelection={htmlSelection} onJump={onJump} onNavigate={onOpenArtboard} />,
          },
          {
            id: "tokens",
            label: t("design.panel.tokens"),
            content: <DesignTokensPanel designId={id} doc={doc} session={session} edit={edit} onJump={onJump} />,
          },
        ]}
      />
    );

  return (
    <>
      {header}
      <div className="flex min-h-0 flex-1 flex-col">{body}</div>
      <VersionsDialog open={versionsOpen} onOpenChange={setVersionsOpen} designId={id} session={session} />
      <ExportDialog open={exportOpen} onOpenChange={setExportOpen} designId={id} session={session} />
      <RenameDialog open={renameOpen} onOpenChange={setRenameOpen} title={title} onRename={rename} />
      <ShareLinkDialog link={shareLink} onOpenChange={(open) => { if (!open) setShareLink(null); }} />
    </>
  );
}

/**
 * Chimera Design in the Code screen's viewer: the list of designs, or one design on the canvas with
 * its panel. Loaded on demand (the Code screen imports this lazily), so a conversation that never
 * opens a design never downloads the canvas.
 */
export default function DesignPane({
  target,
  agentEdit,
  onOpen,
  onClose,
  autosaveMs = AUTOSAVE_MS,
  drawingStartedAt,
}: {
  target: DesignTarget;
  agentEdit?: AgentEdit | null;
  /** Show this design, or the list (`null`). */
  onOpen: (id: string | null, brief?: string) => void;
  onClose: () => void;
  autosaveMs?: number;
  drawingStartedAt?: number;
}) {
  const t = useT();
  return (
    <section data-panel="viewer" aria-label={t("design.pane.name")} className="flex min-h-0 flex-1 flex-col">
      {target.id === null ? (
        <>
          <Header title={t("design.pane.name")} onClose={onClose} />
          <div className="min-h-0 flex-1">
            <DesignList brief={target.brief} onOpen={onOpen} />
          </div>
        </>
      ) : (
        <DesignEditor
          key={target.id}
          id={target.id}
          brief={target.brief}
          agentEdit={agentEdit}
          onList={() => onOpen(null)}
          onClose={onClose}
          onOpen={onOpen}
          autosaveMs={autosaveMs}
          drawingStartedAt={drawingStartedAt}
        />
      )}
    </section>
  );
}
