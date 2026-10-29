import { memo, useEffect, useMemo, useRef } from "react";

import type { DesignTokens } from "@/lib/design/model";
import { artboardSrcDoc } from "@/lib/design/htmlArtboard";

export interface HtmlElementSelection {
  /** Ordinal of the element's opening tag in the artboard source (see htmlElements.ts). */
  index: number;
  tag: string;
  rect: { x: number; y: number; width: number; height: number };
  inlineStyle: string;
  text: string | null;
}

const MAX_INDEX = 100000;

type SelectionMessage = { kind: "clear" } | { kind: "select"; selection: HtmlElementSelection } | { kind: "rejected" };

/** A message from the frame is data: anything out of shape is rejected, and an oversized one says so. */
function readSelectionMessage(value: unknown): SelectionMessage | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (item.type !== "chimera-design-select") return null;
  if (item.index === null) return { kind: "clear" };
  const rect = item.rect as Record<string, unknown> | null;
  const ok =
    typeof item.index === "number" && Number.isInteger(item.index) && item.index >= 0 && item.index <= MAX_INDEX &&
    typeof item.tag === "string" && item.tag.length <= 32 &&
    typeof item.inlineStyle === "string" && item.inlineStyle.length <= 4096 &&
    (item.text === null || (typeof item.text === "string" && item.text.length <= 4096)) &&
    !!rect && [rect.x, rect.y, rect.width, rect.height].every((part) => typeof part === "number" && Number.isFinite(part));
  if (!ok) return { kind: "rejected" };
  return {
    kind: "select",
    selection: {
      index: item.index as number,
      tag: item.tag as string,
      rect: rect as HtmlElementSelection["rect"],
      inlineStyle: item.inlineStyle as string,
      text: item.text as string | null,
    },
  };
}

/** HTML stays in an opaque-origin sandbox; only the known selection script runs in edit mode. */
export const HtmlArtboardFrame = memo(function HtmlArtboardFrame({
  html,
  tokens,
  width,
  height,
  title,
  editing = false,
  onElementSelect,
  onSelectionRejected,
}: {
  html: string;
  tokens: DesignTokens;
  width: number;
  height: number;
  title: string;
  editing?: boolean;
  onElementSelect?: (selection: HtmlElementSelection | null) => void;
  onSelectionRejected?: () => void;
}) {
  const srcDoc = useMemo(() => artboardSrcDoc(html, tokens, editing), [html, tokens, editing]);
  const frameRef = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    if (!editing || !onElementSelect) return;
    const receive = (event: MessageEvent<unknown>) => {
      if (event.source !== frameRef.current?.contentWindow || event.origin !== "null") return;
      const message = readSelectionMessage(event.data);
      if (!message) return;
      if (message.kind === "rejected") onSelectionRejected?.();
      else onElementSelect(message.kind === "select" ? message.selection : null);
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [editing, onElementSelect, onSelectionRejected]);
  return (
    <foreignObject x={0} y={0} width={width} height={height} style={{ pointerEvents: editing ? "auto" : "none" }}>
      <iframe
        ref={frameRef}
        title={title}
        tabIndex={-1}
        sandbox={editing ? "allow-scripts" : ""}
        referrerPolicy="no-referrer"
        loading="lazy"
        srcDoc={srcDoc}
        style={{ width, height, border: "none", display: "block", pointerEvents: editing ? "auto" : "none" }}
      />
    </foreignObject>
  );
});
