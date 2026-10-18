import { NodeShape } from "@/components/design/NodeShape";
import { boundsOf, layoutArtboards } from "@/lib/design/geometry";
import { safeColour, type DesignDocument } from "@/lib/design/model";
import { useT } from "@/lib/i18n";

/**
 * A still picture of a document's first page: what a proposal WOULD look like, drawn from the
 * server's dry run before anything is applied. Read-only on purpose: it is not the canvas, and
 * nothing drawn here can be edited or saved. Colours go through the same `safeColour` gate.
 */
export function DesignPreview({ doc }: { doc: DesignDocument }) {
  const t = useT();
  const page = doc.pages[0];
  const layout = layoutArtboards(page);
  const bounds = boundsOf([...layout.values()]);
  if (!page || !bounds) {
    return <p className="text-xs text-muted-foreground">{t("design.preview.empty")}</p>;
  }
  return (
    <svg
      role="img"
      aria-label={t("design.preview.label", { title: doc.title })}
      viewBox={`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`}
      className="h-40 w-full rounded-card border border-hairline bg-surface-2"
      preserveAspectRatio="xMidYMid meet"
    >
      {page.artboards.map((board) => {
        const o = layout.get(board.id);
        if (!o) return null;
        return (
          <g key={board.id} transform={`translate(${o.x} ${o.y})`}>
            <rect width={board.width} height={board.height} fill={safeColour(board.background)} className="stroke-hairline" />
            <svg width={board.width} height={board.height} overflow="hidden">
              {board.nodeIds.map((id) => {
                const node = doc.nodes[id];
                return node ? <NodeShape key={id} doc={doc} node={node} frame={node.frame} /> : null;
              })}
            </svg>
          </g>
        );
      })}
    </svg>
  );
}
