import type { ReactElement } from "react";

import {
  safeColour,
  tokenColour,
  type DesignDocument,
  type DesignNode,
  type Rect,
} from "@/lib/design/model";
import { cn } from "@/lib/utils";

/** Default type when a text element names no font token: a readable body size, in canvas pixels. */
const DEFAULT_FONT_SIZE = 24;
const DEFAULT_LINE_HEIGHT = 1.25;

export function textMetrics(doc: DesignDocument, node: DesignNode): { family?: string; size: number; weight?: number; lineHeight: number } {
  const token = node.type === "text" && node.fontToken ? doc.tokens.typography[node.fontToken] : undefined;
  return {
    family: token?.family,
    size: token?.size ?? DEFAULT_FONT_SIZE,
    weight: token?.weight,
    lineHeight: token?.lineHeight ?? DEFAULT_LINE_HEIGHT,
  };
}

/** `[x0, y0, …]` normalised to the frame, as SVG points in the frame's space. */
function framePoints(points: readonly number[], frame: Rect): string {
  const out: string[] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    out.push(`${frame.x + points[i] * frame.width},${frame.y + points[i + 1] * frame.height}`);
  }
  return out.join(" ");
}

/**
 * One element, drawn in its artboard's local space.
 *
 * Every colour goes through `safeColour` (or a token lookup that does) on its way to an SVG
 * ATTRIBUTE — never a `style` string, never `dangerouslySetInnerHTML`. A value that is not a strict
 * hex simply is not painted: the element falls back to an outline in the theme's muted ink, which
 * is chrome, not the person's colour.
 */
export function NodeShape({
  doc,
  node,
  frame,
  hidden,
  className,
}: {
  doc: DesignDocument;
  node: DesignNode;
  /** The frame to draw at — the stored one, or a live drag preview. */
  frame: Rect;
  /** Hidden while its text is being edited in the overlay, so the two never double up. */
  hidden?: boolean;
  className?: string;
}): ReactElement | null {
  if (!node.visible) return null;
  const opacity = hidden ? 0 : node.style?.opacity;
  const fill = safeColour(node.style?.fill);
  const stroke = safeColour(node.style?.stroke);
  const strokeWidth = node.style?.strokeWidth;

  switch (node.type) {
    case "text": {
      const m = textMetrics(doc, node);
      const ink = tokenColour(doc, node.colorToken) ?? fill;
      const anchor = node.align === "center" ? "middle" : node.align === "right" ? "end" : "start";
      const x = node.align === "center" ? frame.x + frame.width / 2 : node.align === "right" ? frame.x + frame.width : frame.x;
      const lines = node.text.split("\n");
      return (
        <text
          data-node-id={node.id}
          x={x}
          y={frame.y + m.size}
          fontFamily={m.family}
          fontSize={m.size}
          fontWeight={m.weight}
          textAnchor={anchor}
          fill={ink}
          opacity={opacity}
          className={cn(ink ? undefined : "fill-foreground", className)}
        >
          {lines.map((line, i) => (
            <tspan key={i} x={x} dy={i === 0 ? 0 : m.size * m.lineHeight}>
              {line || " "}
            </tspan>
          ))}
        </text>
      );
    }
    case "shape": {
      const outline = !fill && !stroke;
      const common = {
        "data-node-id": node.id,
        opacity,
        fill: node.shape === "line" || node.shape === "path" ? "none" : (fill ?? "none"),
        stroke: stroke ?? (node.shape === "line" || node.shape === "path" ? fill : undefined),
        strokeWidth: strokeWidth ?? (node.shape === "line" || node.shape === "path" ? 2 : undefined),
        className: cn(outline ? "stroke-muted-foreground" : undefined, className),
      };
      if (node.shape === "ellipse") {
        return <ellipse {...common} cx={frame.x + frame.width / 2} cy={frame.y + frame.height / 2} rx={frame.width / 2} ry={frame.height / 2} />;
      }
      if (node.shape === "line") {
        const p = node.points && node.points.length >= 4 ? node.points : [0, 0, 1, 1];
        return (
          <line
            {...common}
            x1={frame.x + p[0] * frame.width}
            y1={frame.y + p[1] * frame.height}
            x2={frame.x + p[2] * frame.width}
            y2={frame.y + p[3] * frame.height}
            strokeLinecap="round"
          />
        );
      }
      if (node.shape === "path") {
        return <polyline {...common} points={framePoints(node.points ?? [], frame)} strokeLinecap="round" strokeLinejoin="round" />;
      }
      return <rect {...common} x={frame.x} y={frame.y} width={frame.width} height={frame.height} rx={node.style?.radius} />;
    }
    case "image":
      // Assets are resolved by the API in a later step; until then the frame and its alt text
      // stand in, so an image is never fetched from a URL the document names.
      return (
        <g data-node-id={node.id} opacity={opacity} className={className}>
          <rect x={frame.x} y={frame.y} width={frame.width} height={frame.height} className="fill-muted stroke-border" />
          <title>{node.alt}</title>
        </g>
      );
    case "group":
      return (
        <g data-node-id={node.id} opacity={opacity} className={className}>
          {node.childIds.map((id) => {
            const child = doc.nodes[id];
            return child ? <NodeShape key={id} doc={doc} node={child} frame={child.frame} /> : null;
          })}
        </g>
      );
  }
}
