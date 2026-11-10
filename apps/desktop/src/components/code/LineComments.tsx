import { useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  kindLabel,
  lineCommentLabels,
  removeLineComment,
  requestSendLineComments,
  saveLineComment,
  useLineComments,
  useLineCommentsNotice,
  type LineComment,
  type LineRef,
} from "@/lib/line-comments";
import { useT } from "@/lib/i18n";

/** The box a line's comment is written in. Enter saves, Shift+Enter breaks the line, Escape
 *  cancels, and Ctrl/Cmd+Enter saves what is written and asks for every pending comment to be sent.
 *  `onDone(returnFocus)` closes it; the diff puts focus back on the line's `+` when it is asked to. */
export function LineCommentEditor({
  sessionId,
  target: line,
  label,
  initial,
  onDone,
}: {
  sessionId: string;
  target: LineRef;
  label: string;
  initial: string;
  onDone: (returnFocus: boolean) => void;
}) {
  const t = useT();
  const [text, setText] = useState(initial);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // An IME is composing: its Enter picks a candidate, it does not send.
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onDone(true);
      return;
    }
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    saveLineComment(sessionId, line, text);
    onDone(true);
    if (event.ctrlKey || event.metaKey) requestSendLineComments(sessionId);
  }

  return (
    <div className="my-1 space-y-1 rounded-chip border border-accent/40 bg-surface p-2 font-sans">
      <textarea
        // The line is the subject of the comment, so the box names it: a screen reader reads a bare
        // text field as "edit text" with nothing to say what it is about.
        aria-label={label}
        autoFocus
        rows={2}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        className="field w-full resize-y px-2 py-1 text-xs"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{t("code.lineComments.hint")}</span>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => onDone(true)}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              saveLineComment(sessionId, line, text);
              onDone(true);
            }}
          >
            {t("code.lineComments.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** A saved comment, drawn under its line until it is sent. */
export function LineCommentNote({
  sessionId,
  comment,
  onEdit,
  onRemove,
}: {
  sessionId: string;
  comment: LineComment;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const t = useT();
  const labels = lineCommentLabels(t);
  const where = { line: comment.line, kind: kindLabel(comment.marker, labels) };
  return (
    <div className="my-1 flex items-start gap-2 rounded-chip border border-accent/40 bg-accent/10 px-2 py-1 font-sans text-xs">
      <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground">{comment.text}</p>
      <Button size="sm" variant="ghost" aria-label={t("code.lineComments.editLine", where)} onClick={onEdit}>
        {t("code.lineComments.edit")}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        aria-label={t("code.lineComments.remove", where)}
        onClick={() => {
          removeLineComment(sessionId, comment.id);
          onRemove();
        }}
      >
        {t("common.delete")}
      </Button>
    </div>
  );
}

/** The bar above the composer: how many comments wait, the button that sends them all at once, and a
 *  short notice when a send was asked for and could not go out. */
export function LineCommentsBar({
  sessionId,
  disabled,
  onSend,
}: {
  sessionId: string;
  disabled: boolean;
  onSend: () => void;
}) {
  const t = useT();
  const items = useLineComments(sessionId);
  const notice = useLineCommentsNotice(sessionId);
  if (items.length === 0 && !notice) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-chip border border-accent/40 bg-accent/10 px-3 py-2 text-xs">
      {items.length > 0 ? <span className="text-foreground">{t("code.lineComments.pending", { n: items.length })}</span> : null}
      <p role="status" aria-live="polite" className="min-w-0 flex-1 text-right text-bad-foreground empty:hidden">
        {notice ?? ""}
      </p>
      {items.length > 0 ? (
        <Button
          size="sm"
          variant="outline"
          disabled={disabled}
          title={disabled ? t("code.lineComments.busy") : t("code.lineComments.hint")}
          onClick={onSend}
        >
          {t("code.lineComments.send", { n: items.length })}
        </Button>
      ) : null}
    </div>
  );
}
