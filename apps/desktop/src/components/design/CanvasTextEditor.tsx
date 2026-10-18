import { useEffect, useRef, useState, type CSSProperties } from "react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The textarea laid over the canvas while a text element (or a new comment) is being written.
 *
 * An HTML field rather than SVG text editing: SVG has no caret, no selection and no IME support,
 * and a screen reader announces a textarea for what it is. Escape and Mod+Enter commit, as does
 * leaving the field; nothing here cancels silently, because losing typed words is worse than an
 * extra undo step.
 */
export function CanvasTextEditor({
  initial,
  label,
  style,
  onCommit,
}: {
  initial: string;
  /** Accessible name: "Edit text" or "New comment". */
  label: string;
  /** Screen position and type, in pixels already multiplied by the zoom. */
  style: CSSProperties;
  onCommit: (value: string) => void;
}) {
  const t = useT();
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  function commit(): void {
    if (done.current) return;
    done.current = true;
    onCommit(value);
  }

  return (
    <textarea
      ref={ref}
      aria-label={label}
      aria-describedby="design-editor-hint"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape" || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
          e.preventDefault();
          commit();
        }
      }}
      onPointerDown={(e) => e.stopPropagation()}
      placeholder={t("design.editor.placeholder")}
      style={style}
      className={cn(
        "absolute z-10 resize-none overflow-hidden rounded-sm border border-accent bg-card/95 p-0 text-foreground",
        focusRing,
      )}
    />
  );
}
