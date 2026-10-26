import { NodeShape } from "@/components/design/NodeShape";
import { boundsOf, layoutArtboards } from "@/lib/design/geometry";
import { safeColour, type DesignDocument } from "@/lib/design/model";
import { useT } from "@/lib/i18n";

/**
 * A still picture of a document's first page: what a proposal WOULD look like, drawn from the
 * server's dry run before anything is applied. Read-only on purpose: it is not the canvas, and
 * nothing drawn here can be edited or saved. Colours go through the same `safeColour` gate.
 */
export function DesignPreview({ doc, onNavigate }: { doc: DesignDocument; onNavigate?: (artboardId: string) => void }) {
  const t = useT();
  const page = doc.pages[0];
  const thumbnail = page?.artboards.find((board) => board.thumbnailOf !== undefined);
  const source = thumbnail && doc.pages.flatMap((item) => item.artboards).find((board) => board.id === thumbnail.thumbnailOf);

  if (thumbnail && source) {
    return (
      <button type="button" aria-label={t("design.index.openScreen", { name: source.name })} onClick={() => onNavigate?.(source.id)} className="block w-full text-left">
        <svg role="img" aria-label={t("design.preview.label", { title: source.name })} viewBox={`0 0 ${source.width} ${source.height}`} className="h-40 w-full rounded-card border border-hairline bg-surface-2" preserveAspectRatio="xMidYMid meet">
          <rect width={source.width} height={source.height} fill={safeColour(source.background)} className="stroke-hairline" />
          <svg width={source.width} height={source.height} overflow="hidden">
            {source.nodeIds.map((id) => { const node = doc.nodes[id]; return node ? <NodeShape key={id} doc={doc} node={node} frame={node.frame} /> : null; })}
          </svg>
        </svg>
      </button>
    );
  }

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
