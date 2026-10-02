import { useRef, type KeyboardEvent, type PointerEvent } from "react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { SIZE_LIMITS, type SizedRegion } from "@/lib/layout/model";
import { cn } from "@/lib/utils";

/**
 * The thin line between two columns that changes their widths: phase 2 of the dynamic screen.
 *
 * The WAI-ARIA window-splitter pattern: `role="separator"`, focusable, with the width it controls as
 * `aria-valuenow` between its min and max. Arrow keys move it 16px, Home puts it back to the width it
 * starts with, and so does a double click. Dragging updates the layout as the pointer moves, and the
 * layout clamps every value, so a column can neither vanish nor swallow the window.
 *
 * **Why not `react-resizable-panels`.** The plan named it, and the owner approved it. On building this
 * phase it turned out not to fit: it sizes sibling panels inside one group, while here the right panel
 * lives in the shell and the conversation list inside the Code screen, and the layout model already
 * keeps the widths, their limits and their storage. What the library would have bought is this file.
 *
 * `grows` says which way the region grows: `right` for a region on the left of the line (dragging
 * right widens it), `left` for a region on its right.
 */
const STEP = 16;

const LABEL_KEY: Record<Exclude<SizedRegion, "bottom">, string> = {
  left: "layout.resize.left",
  right: "layout.resize.right",
  viewer: "layout.resize.viewer",
};

export function Splitter({
  region,
  grows,
  className,
}: {
  region: Exclude<SizedRegion, "bottom">;
  grows: "left" | "right";
  className?: string;
}) {
  const t = useT();
  const { layout, dispatch, settle } = useLayout();
  const limits = SIZE_LIMITS[region];
  const size = layout.regions[region].size ?? limits.initial;
  const drag = useRef<{ x: number; from: number } | null>(null);
  const sign = grows === "right" ? 1 : -1;

  const resize = (px: number) => void dispatch({ type: "resize", region, size: px });

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    e.preventDefault();
    settle();
    drag.current = { x: e.clientX, from: size };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    resize(drag.current.from + sign * (e.clientX - drag.current.x));
  }

  function onPointerUp(e: PointerEvent<HTMLDivElement>) {
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      resize(size + (e.key === "ArrowRight" ? 1 : -1) * sign * STEP);
    } else if (e.key === "Home") {
      e.preventDefault();
      settle();
      resize(limits.initial);
    }
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={t(LABEL_KEY[region])}
      aria-valuenow={size}
      aria-valuemin={limits.min}
      aria-valuemax={limits.max}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
      onBlur={settle}
      onDoubleClick={() => {
        settle();
        resize(limits.initial);
      }}
      className={cn(
        // A wide hit area around a one-pixel line: easy to grab, quiet to look at.
        "group relative z-10 -mx-1 w-2 shrink-0 cursor-col-resize touch-none select-none",
        focusRing,
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent",
          // Hover only: focus is shown by the one shared ring on the separator itself (`focusRing`), and a
          // second focus style here would be the per-component ring the design gate forbids.
          "transition-colors duration-1 ease-out group-hover:bg-accent",
        )}
      />
    </div>
  );
}
