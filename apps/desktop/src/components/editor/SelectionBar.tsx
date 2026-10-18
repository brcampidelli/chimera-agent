import { useState, type ReactNode } from "react";
import { MessageSquare, Wand2, X } from "lucide-react";

import { linesLabel } from "@/components/editor/InlineDiff";
import { focusRing } from "@/components/ui/focus";
import { shortcutLabel } from "@/lib/hotkeys";
import type { SelectionIntent } from "@/lib/editorSelection";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The bar that floats over a selection: ask about these lines, or ask for them to be changed.
 *
 * Two steps and not one, because the instruction is the point: "change this" with nothing typed is
 * a request the agent can only guess at. The first step names the lines; the second takes the words
 * in a small field right there, so the person never leaves the file to describe a piece of it.
 *
 * `mode` is the parent's because the keyboard reaches it too (⌘L / ⌘I from inside the editor).
 */
export function SelectionBar({
  startLine,
  endLine,
  mode,
  onMode,
  canChange,
  canAsk,
  needsSave = false,
  onSubmit,
}: {
  startLine: number;
  endLine: number;
  mode: SelectionIntent | null;
  onMode: (mode: SelectionIntent | null) => void;
  /** False on a file the Editor will not write (truncated, a document): a change could not be applied. */
  canChange: boolean;
  /** False where nothing can take the question to the conversation. */
  canAsk: boolean;
  /** The file has unsaved edits. The agent reads the lines from disk, so asking now would be about
   *  text other than what is on screen: the bar says to save first instead of offering either. */
  needsSave?: boolean;
  onSubmit: (intent: SelectionIntent, instruction: string) => void;
}) {
  const t = useT();
  const [text, setText] = useState("");
  const lines = linesLabel(startLine, endLine);
  const submit = () => {
    const words = text.trim();
    if (!mode || !words) return;
    onSubmit(mode, words);
    setText("");
  };

  return (
    <div
      role="toolbar"
      aria-label={t("edit.selection.bar", { lines })}
      // `.floating`: it genuinely overlays the text, which is what that elevation is for (DESIGN.md).
      className="floating absolute right-3 top-2 z-10 flex max-w-full items-center gap-1 px-1.5 py-1 text-xs"
    >
      {mode === null && needsSave ? (
        <span className="px-1 text-muted-foreground">
          {t("edit.selection.lines", { lines })} · {t("edit.selection.saveFirst")}
        </span>
      ) : mode === null ? (
        <>
          <span className="px-1 text-muted-foreground">{t("edit.selection.lines", { lines })}</span>
          {canAsk && (
            <BarButton
              onClick={() => onMode("ask")}
              title={t("edit.selection.askHint", { keys: shortcutLabel("editorAsk") ?? "" })}
            >
              <MessageSquare className="h-3 w-3" aria-hidden />
              {t("edit.selection.ask")}
            </BarButton>
          )}
          {canChange && (
            <BarButton
              onClick={() => onMode("change")}
              title={t("edit.selection.changeHint", { keys: shortcutLabel("editorChange") ?? "" })}
            >
              <Wand2 className="h-3 w-3" aria-hidden />
              {t("edit.selection.change")}
            </BarButton>
          )}
        </>
      ) : (
        <form
          className="flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            // The bar opened because someone wants to type: the field is where the keys should go.
            // eslint-disable-next-line jsx-a11y/no-autofocus
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onMode(null);
              }
            }}
            aria-label={mode === "ask" ? t("edit.selection.askLabel", { lines }) : t("edit.selection.changeLabel", { lines })}
            placeholder={mode === "ask" ? t("edit.selection.askPlaceholder") : t("edit.selection.changePlaceholder")}
            className="field w-64 min-w-0 px-2 text-xs"
          />
          <BarButton type="submit" disabled={!text.trim()}>
            {mode === "ask" ? t("edit.selection.send") : t("edit.selection.propose")}
          </BarButton>
          <button
            type="button"
            onClick={() => onMode(null)}
            aria-label={t("edit.selection.close")}
            className={cn("flex h-5 w-5 items-center justify-center rounded-sm hover:bg-surface-hover", focusRing)}
          >
            <X className="h-3 w-3" />
          </button>
        </form>
      )}
    </div>
  );
}

function BarButton({
  children,
  onClick,
  title,
  type = "button",
  disabled = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  title?: string;
  type?: "button" | "submit";
  disabled?: boolean;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-foreground hover:bg-surface-hover disabled:opacity-50",
        focusRing,
      )}
    >
      {children}
    </button>
  );
}
