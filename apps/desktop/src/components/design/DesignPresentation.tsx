import { useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

import { DesignCanvas } from "@/components/design/DesignCanvas";
import { focusRing } from "@/components/ui/focus";
import type { Viewport } from "@/lib/design/geometry";
import type { Artboard, DesignDocument } from "@/lib/design/model";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const NOOP = () => undefined;

/**
 * Presenting one artboard at a time, full window. Read-only by construction: the canvas gets no
 * `dispatch` and no-op commands, so nothing a viewer does can change the design.
 *
 * Focus moves into the dialog when it opens and back to the control that opened it when it closes.
 * Escape closes and the arrows step; both are listened for in the capture phase, because the canvas
 * stops the events it handles and would otherwise swallow the arrows.
 */
export function DesignPresentation({
  doc,
  pageId,
  boards,
  index,
  onIndex,
  onClose,
  viewport,
}: {
  doc: DesignDocument;
  pageId: string;
  boards: readonly Artboard[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
  viewport: Viewport;
}) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const last = boards.length - 1;
  const current = Math.min(Math.max(index, 0), last);

  useEffect(() => {
    const returnTo = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    return () => returnTo?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight") onIndex(Math.min(last, current + 1));
      else if (event.key === "ArrowLeft") onIndex(Math.max(0, current - 1));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [current, last, onClose, onIndex]);

  const control = cn("h-8 w-8 rounded-lg hover:bg-surface-hover disabled:opacity-40", focusRing);
  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("design.present.title")} className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="flex items-center justify-between border-b border-hairline p-2">
        <span className="text-sm text-muted-foreground">{t("design.present.count", { current: current + 1, total: boards.length })}</span>
        <div className="flex gap-1">
          <button type="button" aria-label={t("design.present.previous")} disabled={current === 0} onClick={() => onIndex(current - 1)} className={control}>
            <ChevronLeft className="mx-auto h-4 w-4" aria-hidden="true" />
          </button>
          <button type="button" aria-label={t("design.present.next")} disabled={current === last} onClick={() => onIndex(current + 1)} className={control}>
            <ChevronRight className="mx-auto h-4 w-4" aria-hidden="true" />
          </button>
          <button type="button" aria-label={t("design.present.close")} onClick={onClose} className={control}>
            <X className="mx-auto h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <DesignCanvas
          document={doc}
          pageId={pageId}
          selection={[]}
          onSelectionChange={NOOP}
          tool="pan"
          onToolChange={NOOP}
          viewport={viewport}
          onViewportChange={NOOP}
          onCommand={NOOP}
          announce={NOOP}
          author=""
          onAddFrame={NOOP}
          onlyArtboardId={boards[current]?.id}
          commentsVisible={false}
        />
      </div>
    </div>
  );
}
