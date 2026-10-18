import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Download, History as HistoryIcon, LayoutGrid, Lock, Palette, X } from "lucide-react";

import { DesignAssistant } from "@/components/design/DesignAssistant";
import { ExportDialog, VersionsDialog } from "@/components/design/DesignDialogs";
import { DesignList } from "@/components/design/DesignList";
import { DesignTokensPanel } from "@/components/design/DesignTokensPanel";
import { DesignWorkspace } from "@/components/design/DesignWorkspace";
import { SaveIndicator } from "@/components/design/SaveIndicator";
import { useDesignSession, AUTOSAVE_MS } from "@/components/design/useDesignSession";
import { MaximizeButton } from "@/components/shell/Maximize";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { Spinner } from "@/components/ui/panel";
import { Tooltip } from "@/components/ui/tooltip";
import { record } from "@/lib/design/history";
import type { DesignOperation } from "@/lib/design/model";
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

function Header({ title, children, onList, onClose }: { title: string; children?: ReactNode; onList?: () => void; onClose: () => void }) {
  const t = useT();
  const name = t("design.pane.name");
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-hairline px-3 py-2">
      <Palette className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
      <h2 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground" title={title}>
        {title}
      </h2>
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
  autosaveMs,
}: {
  id: string;
  brief?: string;
  agentEdit?: AgentEdit | null;
  onList: () => void;
  onClose: () => void;
  autosaveMs: number;
}) {
  const t = useT();
  const session = useDesignSession(id, autosaveMs);
  const [selection, setSelection] = useState<string[]>([]);
  const [jump, setJump] = useState<{ ids: string[]; key: number } | undefined>(undefined);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const { doc, agentEdited } = session;

  useEffect(() => {
    if (agentEdit && agentEdit.documentId === id) void agentEdited(agentEdit.revision);
    // Each frame once: `key` is what makes it a new one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentEdit?.key]);

  const onJump = useCallback((ids: string[]) => setJump((j) => ({ ids, key: (j?.key ?? 0) + 1 })), []);

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
  const header = (
    <Header title={title} onList={onList} onClose={onClose}>
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
        select={jump}
        compactBelow={900}
        panels={[
          {
            id: "assistant",
            label: t("design.panel.assistant"),
            content: <DesignAssistant designId={id} session={session} initialBrief={brief} selection={selection} onJump={onJump} />,
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
}: {
  target: DesignTarget;
  agentEdit?: AgentEdit | null;
  /** Show this design, or the list (`null`). */
  onOpen: (id: string | null, brief?: string) => void;
  onClose: () => void;
  autosaveMs?: number;
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
          autosaveMs={autosaveMs}
        />
      )}
    </section>
  );
}
