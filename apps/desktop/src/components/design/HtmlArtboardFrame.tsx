import { memo, useEffect, useMemo, useRef, useState } from "react";

import type { DesignTokens } from "@/lib/design/model";
import { annotatedElementCount, artboardSrcDoc, hasModelScript, staticArtboardSrcDoc } from "@/lib/design/htmlArtboard";
import { useT } from "@/lib/i18n";

export interface HtmlElementSelection {
  /** Ordinal of the element's opening tag in the artboard source (see htmlElements.ts). */
  index: number;
  tag: string;
  rect: { x: number; y: number; width: number; height: number };
  inlineStyle: string;
  text: string | null;
}

type SelectionMessage = { kind: "clear" } | { kind: "select"; selection: HtmlElementSelection } | { kind: "rejected" };

/**
 * A message from the frame is data: anything out of shape is rejected. An index is accepted only when
 * it is an integer naming one of the elements this artboard's markup actually stamped.
 */
function readSelectionMessage(value: unknown, elementCount: number): SelectionMessage | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (item.type !== "chimera-design-select") return null;
  if (item.index === null) return { kind: "clear" };
  const rect = item.rect as Record<string, unknown> | null;
  const ok =
    typeof item.index === "number" && Number.isInteger(item.index) && item.index >= 0 && item.index < elementCount &&
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
  interactive = true,
  onElementSelect,
  onSelectionRejected,
}: {
  html: string;
  tokens: DesignTokens;
  width: number;
  height: number;
  title: string;
  editing?: boolean;
  interactive?: boolean;
  onElementSelect?: (selection: HtmlElementSelection | null) => void;
  onSelectionRejected?: () => void;
}) {
  const t = useT();
  const runsModelScript = interactive && hasModelScript(html);
  // Scripts run in this frame when it is in edit mode, or when the model wrote one.
  const scripted = editing || runsModelScript;
  const srcDoc = useMemo(
    () => interactive ? artboardSrcDoc(html, tokens, editing) : staticArtboardSrcDoc(html, tokens),
    [html, tokens, editing, interactive],
  );
  const elementCount = useMemo(() => (editing ? annotatedElementCount(html) : 0), [editing, html]);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const selectedIndex = useRef<number | null>(null);
  useEffect(() => {
    if (!editing || !onElementSelect) return;
    const receive = (event: MessageEvent<unknown>) => {
      if (event.source !== frameRef.current?.contentWindow || event.origin !== "null") return;
      const message = readSelectionMessage(event.data, elementCount);
      if (!message) return;
      if (message.kind === "rejected") onSelectionRejected?.();
      else {
        selectedIndex.current = message.kind === "select" ? message.selection.index : null;
        onElementSelect(message.kind === "select" ? message.selection : null);
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [editing, elementCount, onElementSelect, onSelectionRejected]);

  // The policy is the lock on navigation, and this is the second line: a srcdoc frame loads once per
  // document it is given. A second load of the same document means the frame navigated itself (a
  // script's location change, a reload, a link). Then the frame is replaced by the same markup with
  // the model's script removed and no scripts at all, and the person is told why.
  const [navigatedDoc, setNavigatedDoc] = useState<string | null>(null);
  const navigated = scripted && navigatedDoc === srcDoc;
  const shown = useMemo(
    () => (navigated ? staticArtboardSrcDoc(html, tokens) : srcDoc),
    [html, tokens, navigated, srcDoc],
  );
  const loads = useRef<{ doc: string; count: number } | null>(null);
  const onLoad = () => {
    const seen = loads.current;
    if (!seen || seen.doc !== shown) {
      loads.current = { doc: shown, count: 1 };
      // A new document lost its DOM, so the selected element must be re-marked here. The static
      // swap after a navigation has no script to receive it, so it is skipped.
      const index = selectedIndex.current;
      if (editing && !navigated && index !== null) {
        if (index < elementCount) frameRef.current?.contentWindow?.postMessage({ type: "chimera-design-restore-selection", index }, "*");
        else {
          selectedIndex.current = null;
          onElementSelect?.(null);
        }
      }
      return;
    }
    seen.count += 1;
    if (seen.count > 1 && !navigated) setNavigatedDoc(srcDoc);
  };

  return (
    <foreignObject x={0} y={0} width={width} height={height} style={{ pointerEvents: editing ? "auto" : "none" }}>
      <div style={{ position: "relative", width, height }}>
        <iframe
          ref={frameRef}
          title={title}
          tabIndex={-1}
          sandbox={scripted && !navigated ? "allow-scripts" : ""}
          referrerPolicy="no-referrer"
          loading="lazy"
          srcDoc={shown}
          onLoad={scripted ? onLoad : undefined}
          style={{ width, height, border: "none", display: "block", pointerEvents: editing ? "auto" : "none" }}
        />
        {navigated ? (
          <p role="status" className="absolute inset-x-0 bottom-0 m-0 border-t border-hairline bg-background px-2 py-1 text-xs text-foreground">
            {t("design.html.scriptNavigated")}
          </p>
        ) : null}
      </div>
    </foreignObject>
  );
});
