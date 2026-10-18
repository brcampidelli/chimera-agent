import { Loader2 } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { hunks, lineDiff, type DiffLine } from "@/lib/editorSelection";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * What the agent proposed, or changed, shown as a diff the person decides on.
 *
 * Two places in the Editor need it and both have the same rule: nothing reaches the file — or
 * leaves it — until a button says so. A proposal is applied by Accept and by nothing else; a change
 * the agent made on disk is held off the screen until Keep or Undo. "Silently reloaded" and
 * "silently applied" are the same failure seen from two sides.
 */

/** A diff, + and − by line, with the unchanged stretches between changes folded away. */
export function DiffLines({ before, after, label }: { before: string; after: string; label: string }) {
  const t = useT();
  const shown = hunks(lineDiff(before, after));
  return (
    <pre
      aria-label={label}
      className="max-h-48 overflow-auto rounded-chip bg-surface-2 p-2 font-mono text-xs leading-relaxed"
    >
      {shown.map((line, i) =>
        line === null ? (
          <div key={i} className="text-muted-foreground">
            {t("edit.diff.skipped")}
          </div>
        ) : (
          <Line key={i} line={line} />
        ),
      )}
    </pre>
  );
}

function Line({ line }: { line: DiffLine }) {
  const mark = line.kind === "add" ? "+" : line.kind === "del" ? "-" : " ";
  return (
    <div
      data-kind={line.kind}
      className={cn(
        line.kind === "add" && "bg-ok/10 text-ok-foreground",
        line.kind === "del" && "bg-bad/10 text-bad-foreground",
        line.kind === "same" && "text-muted-foreground",
      )}
    >
      {`${mark} ${line.text}`}
    </div>
  );
}

function Action({ onClick, children, primary = false }: { onClick: () => void; children: string; primary?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-sm px-1.5 py-0.5 hover:bg-surface-hover",
        primary ? "text-accent-ink" : "underline",
        focusRing,
      )}
    >
      {children}
    </button>
  );
}

/** The lines a range covers, as the panel names them: "line 4" or "lines 4–9". */
export function linesLabel(start: number, end: number): string {
  return start === end ? String(start) : `${start}–${end}`;
}

export type InlineEditState =
  | { status: "drafting" }
  | { status: "ready"; replacement: string }
  | { status: "prose"; answer: string }
  | { status: "failed"; message: string };

/** "Change this": the agent's proposal for a range, and the only two ways out of it. */
export function ProposalPanel({
  startLine,
  endLine,
  instruction,
  original,
  state,
  stale,
  onAccept,
  onReject,
  onCancel,
}: {
  startLine: number;
  endLine: number;
  instruction: string;
  /** The lines as they were when the change was asked for. */
  original: string;
  state: InlineEditState;
  /** The lines changed under the proposal since it was asked for: Accept would apply it to text the
   *  agent never saw, so it is not offered. */
  stale: boolean;
  onAccept: () => void;
  onReject: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const lines = linesLabel(startLine, endLine);
  return (
    <section
      aria-label={t("edit.inline.title", { lines })}
      className="flex shrink-0 flex-col gap-1.5 border-b border-hairline bg-card/40 px-3 py-2 text-xs"
    >
      <p className="text-foreground">
        <span className="font-medium">{t("edit.inline.title", { lines })}</span>
        <span className="text-muted-foreground">{` · ${instruction}`}</span>
      </p>
      {state.status === "drafting" ? (
        <p className="flex items-center gap-2 text-muted-foreground" role="status">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {t("edit.inline.drafting")}
          <Action onClick={onCancel}>{t("edit.inline.cancel")}</Action>
        </p>
      ) : state.status === "failed" ? (
        <p className="flex flex-wrap items-center gap-2 text-bad-foreground" role="alert">
          {t("edit.inline.failed", { message: state.message })}
          <Action onClick={onReject}>{t("edit.inline.dismiss")}</Action>
        </p>
      ) : state.status === "prose" ? (
        <>
          <p className="text-muted-foreground">{t("edit.inline.noProposal")}</p>
          <p className="max-h-32 overflow-auto whitespace-pre-wrap text-foreground/90">{state.answer}</p>
          <span>
            <Action onClick={onReject}>{t("edit.inline.dismiss")}</Action>
          </span>
        </>
      ) : (
        <>
          <DiffLines before={original} after={state.replacement} label={t("edit.inline.diff")} />
          {stale && <p className="text-warn-foreground">{t("edit.inline.stale")}</p>}
          <span className="flex items-center gap-2">
            {!stale && (
              <Action onClick={onAccept} primary>
                {t("edit.inline.accept")}
              </Action>
            )}
            <Action onClick={onReject}>{t("edit.inline.reject")}</Action>
          </span>
        </>
      )}
    </section>
  );
}

/**
 * The file changed on disk while it was open and unedited here: held, shown, and decided on.
 *
 * `byChimera` is true only while an agent turn or run is working in this project (or was, since the
 * file was last in step) — the screen cannot know WHO wrote a file, and a banner naming Chimera for
 * a change made by another editor would be a guess dressed as a fact.
 */
export function AgentChangeBanner({
  before,
  after,
  byChimera,
  onKeep,
  onUndo,
  undoing,
}: {
  before: string;
  after: string;
  byChimera: boolean;
  onKeep: () => void;
  onUndo: () => void;
  undoing: boolean;
}) {
  const t = useT();
  const title = byChimera ? t("edit.changed.byChimera") : t("edit.changed.onDisk");
  return (
    <section
      aria-label={title}
      className="flex shrink-0 flex-col gap-1.5 border-b border-hairline bg-warn/10 px-3 py-2 text-xs"
    >
      <p className="flex flex-wrap items-center gap-2 text-warn-foreground">
        <span className="font-medium">{title}</span>
        <Action onClick={onKeep} primary>
          {t("edit.changed.keep")}
        </Action>
        <Action onClick={onUndo}>{undoing ? t("edit.saving") : t("edit.changed.undo")}</Action>
      </p>
      <DiffLines before={before} after={after} label={t("edit.changed.diff")} />
    </section>
  );
}
