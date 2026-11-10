import { useMemo, useRef, useState, type KeyboardEvent } from "react";

import { LineCommentEditor, LineCommentNote } from "@/components/code/LineComments";
import { diffLineClass, parseDiffRows, type DiffRow } from "@/components/code/diffRows";
import { Button } from "@/components/ui/button";
import { focusRing } from "@/components/ui/focus";
import { kindLabel, lineCommentId, lineCommentLabels, useLineComments, type LineRef } from "@/lib/line-comments";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Where the comments of a diff go: the conversation they are sent to, the file they are about, and
 *  which diff they were read from (`edit:<i>:<j>` for an edit in the transcript, `git` for Changes). */
export interface DiffCommentScope {
  sessionId: string;
  file: string;
  origin: string;
}

/** A hand-rolled unified-diff renderer: + green, - red, @@ dim headers, file headers muted.
 *
 *  This existed twice — byte-identical in `Code.tsx` and `Agents.tsx`, each with a comment noting
 *  that it mirrored the other. Two copies of a renderer is two places to fix a colour and one place
 *  to forget, so it now lives here and both screens import it.
 *
 *  Deliberately not a diff *parser* for display: it colours lines by their first character. The
 *  parser in `diffRows.ts` only numbers the lines, for comments. The patches it renders are real
 *  unified diffs produced server-side from the file on disk before and after a write, so there is
 *  nothing here that could invent a change that did not happen.
 *
 *  With `commentable`, every body line gets a `+` beside its number; a comment written there is drawn
 *  under its line until it is sent with the conversation's other comments. */
export function DiffView({ patch, commentable }: { patch: string; commentable?: DiffCommentScope }) {
  const rows = useMemo(() => parseDiffRows(patch), [patch]);
  if (!commentable) {
    return (
      <pre className="overflow-x-auto rounded-chip bg-surface-2 p-2 font-mono text-xs leading-relaxed">
        {rows.map((row, i) => (
          <div key={i} className={diffLineClass(row.text)}>
            {row.text || " "}
          </div>
        ))}
      </pre>
    );
  }
  return <CommentableDiff rows={rows} scope={commentable} />;
}

function rowRef(scope: DiffCommentScope, row: DiffRow): LineRef | null {
  if (!row.ref) return null;
  return { ...row.ref, file: scope.file, origin: scope.origin };
}

/** The lines take focus as one group: a single `+` is in the tab order, and the arrow keys move
 *  between the others. Pressing a `+` opens its box; closing the box puts focus back on that `+`. */
function CommentableDiff({ rows, scope }: { rows: DiffRow[]; scope: DiffCommentScope }) {
  const t = useT();
  const comments = useLineComments(scope.sessionId);
  const [open, setOpen] = useState<string | null>(null);
  const [stop, setStop] = useState<string | null>(null);
  const plusButtons = useRef(new Map<string, HTMLButtonElement>());

  const ids = useMemo(
    () => rows.flatMap((row) => {
      const ref = rowRef(scope, row);
      return ref ? [lineCommentId(ref)] : [];
    }),
    [rows, scope],
  );
  const tabStop = stop !== null && ids.includes(stop) ? stop : (ids[0] ?? null);

  function focusLine(id: string) {
    setStop(id);
    plusButtons.current.get(id)?.focus();
  }

  return (
    <div className="overflow-x-auto rounded-chip bg-surface-2 p-2 font-mono text-xs leading-relaxed">
      {rows.map((row, i) => {
        const ref = rowRef(scope, row);
        if (!ref) {
          return (
            <div key={i} className={cn(diffLineClass(row.text), "whitespace-pre")}>
              {row.text || " "}
            </div>
          );
        }
        const id = lineCommentId(ref);
        const saved = comments.find((item) => item.id === id);
        return (
          <div key={i} className="group">
            <DiffCommentLine
              line={ref}
              text={row.text}
              active={open === id}
              tabStop={id === tabStop}
              buttonRef={(el) => {
                if (el) plusButtons.current.set(id, el);
                else plusButtons.current.delete(id);
              }}
              onFocus={() => setStop(id)}
              onArrow={(key) => {
                const at = ids.indexOf(id);
                const next = key === "ArrowDown" ? ids[at + 1] : ids[at - 1];
                if (next !== undefined) focusLine(next);
              }}
              onOpen={() => setOpen(id)}
            />
            {saved && open !== id ? (
              <LineCommentNote
                sessionId={scope.sessionId}
                comment={saved}
                onEdit={() => setOpen(id)}
                onRemove={() => focusLine(id)}
              />
            ) : null}
            {open === id ? (
              <LineCommentEditor
                sessionId={scope.sessionId}
                target={ref}
                label={editorLabel(ref, t)}
                initial={saved?.text ?? ""}
                onDone={(returnFocus) => {
                  setOpen(null);
                  if (returnFocus) focusLine(id);
                }}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** Names the line a box or button is about: its number and whether it was added, removed or is
 *  context. The same words are used everywhere, so a screen reader hears one name for one line. */
function editorLabel(ref: LineRef, t: ReturnType<typeof useT>): string {
  return t("code.lineComments.box", { line: ref.line, kind: kindLabel(ref.marker, lineCommentLabels(t)) });
}

/** One body line: its number, the `+` that opens a comment on it, and the line itself. */
function DiffCommentLine({
  line: ref,
  text,
  active,
  tabStop,
  buttonRef,
  onFocus,
  onArrow,
  onOpen,
}: {
  line: LineRef;
  text: string;
  active: boolean;
  tabStop: boolean;
  buttonRef: (el: HTMLButtonElement | null) => void;
  onFocus: () => void;
  onArrow: (key: "ArrowDown" | "ArrowUp") => void;
  onOpen: () => void;
}) {
  const t = useT();
  const kind = kindLabel(ref.marker, lineCommentLabels(t));
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      onArrow(event.key);
    }
  }
  return (
    <div className={cn("flex items-start", diffLineClass(text))}>
      <span className="flex w-12 shrink-0 select-none justify-end pr-2 text-muted-foreground" aria-hidden="true">
        {ref.line}
      </span>
      <Button
        ref={buttonRef}
        size="icon"
        variant="ghost"
        tabIndex={tabStop ? 0 : -1}
        // The visible "+" is part of the name, so what is seen and what is announced match.
        aria-label={`+ ${t("code.lineComments.add", { line: ref.line, kind })}`}
        aria-expanded={active}
        onClick={onOpen}
        onFocus={onFocus}
        onKeyDown={onKeyDown}
        className={cn(
          // Visible at rest, dimmer until the line is hovered or has focus, so touch users see it too.
          "h-5 w-5 shrink-0 text-accent opacity-40 group-hover:opacity-100 group-focus-within:opacity-100",
          active && "opacity-100",
          focusRing,
        )}
      >
        +
      </Button>
      <span className="min-w-0 whitespace-pre pl-1">{text || " "}</span>
    </div>
  );
}
