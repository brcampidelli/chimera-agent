import { memo, useMemo } from "react";

import type { DesignTokens } from "@/lib/design/model";
import { artboardSrcDoc } from "@/lib/design/htmlArtboard";

/**
 * HTML remains data: the frame has an opaque origin, no capabilities and no pointer input.
 * The srcdoc is memoised because assigning a new srcdoc reloads the frame, and a canvas re-renders
 * on every pointer move; an unchanged board must not reload.
 */
export const HtmlArtboardFrame = memo(function HtmlArtboardFrame({
  html,
  tokens,
  width,
  height,
  title,
}: {
  html: string;
  tokens: DesignTokens;
  width: number;
  height: number;
  title: string;
}) {
  const srcDoc = useMemo(() => artboardSrcDoc(html, tokens), [html, tokens]);
  return (
    <foreignObject x={0} y={0} width={width} height={height} style={{ pointerEvents: "none" }}>
      {/* Out of the tab order: the frame is pointer-inert, so keyboard focus would land on a control the user cannot operate. It keeps its title for assistive technology. */}
      <iframe
        title={title}
        tabIndex={-1}
        sandbox=""
        referrerPolicy="no-referrer"
        loading="lazy"
        srcDoc={srcDoc}
        style={{ width, height, border: "none", display: "block", pointerEvents: "none" }}
      />
    </foreignObject>
  );
});
