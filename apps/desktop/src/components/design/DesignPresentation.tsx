import { useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { DesignCanvas } from "@/components/design/DesignCanvas";
import { Dialog } from "@/components/ui/dialog";
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
 * It is the shared `ui/dialog`, stretched to the window, so the focus trap, the return of focus to
 * what opened it, Escape and the accessible name are the same ones every other dialog has rather
 * than a hand-made copy. The arrows step; they are listened for in the capture phase, because the
 * canvas stops the events it handles and would otherwise swallow them.
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
  const last = boards.length - 1;
  const current = Math.min(Math.max(index, 0), last);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") onIndex(Math.min(last, current + 1));
      else if (event.key === "ArrowLeft") onIndex(Math.max(0, current - 1));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [current, last, onIndex]);

  const control = cn("h-8 w-8 rounded-lg hover:bg-surface-hover disabled:opacity-40", focusRing);
  return (
    <Dialog
      open
      // Escape and the X arrive here as `false`; there is no way to be "open" from inside.
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("design.present.title")}
      // `flex flex-col` is what hands the full-window height down to the canvas: the dialog body is
      // `flex-1`, which only means something inside a flex parent. Without it the canvas fell to
      // its SVG's intrinsic height (~150px) — jsdom does no layout, so no test could see it.
      className="flex flex-col left-0 top-0 h-screen w-screen max-w-none translate-x-0 translate-y-0 rounded-none"
      footer={
        <>
          <span className="mr-auto self-center text-sm text-muted-foreground">
            {t("design.present.count", { current: current + 1, total: boards.length })}
          </span>
          <button type="button" aria-label={t("design.present.previous")} disabled={current === 0} onClick={() => onIndex(current - 1)} className={control}>
            <ChevronLeft className="mx-auto h-4 w-4" aria-hidden="true" />
          </button>
          <button type="button" aria-label={t("design.present.next")} disabled={current === last} onClick={() => onIndex(current + 1)} className={control}>
            <ChevronRight className="mx-auto h-4 w-4" aria-hidden="true" />
          </button>
          <button type="button" onClick={onClose} className={cn("rounded-lg px-3 text-sm hover:bg-surface-hover", focusRing)}>
            {t("design.present.close")}
          </button>
        </>
      }
    >
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
          onNavigate={NOOP}
          onCommand={NOOP}
          announce={NOOP}
          author=""
          onAddFrame={NOOP}
          onlyArtboardId={boards[current]?.id}
          commentsVisible={false}
        />
      </div>
    </Dialog>
  );
}
