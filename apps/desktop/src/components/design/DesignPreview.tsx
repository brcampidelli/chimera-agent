import { HtmlArtboardFrame } from "@/components/design/HtmlArtboardFrame";
import { NodeShape } from "@/components/design/NodeShape";
import { boundsOf, layoutArtboards } from "@/lib/design/geometry";
import { resolveAssetReferences, tokensCss } from "@/lib/design/htmlArtboard";
import { safeColour, type DesignDocument, type DesignTokens } from "@/lib/design/model";
import { useT } from "@/lib/i18n";

const PREVIEW_CSP_META = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'">`;

/**
 * A model-written HTML prefix as an inert document. Defence in depth, innermost last: this strips
 * what can run or navigate, the CSP forbids everything but inline style, and the iframe is
 * `sandbox=""` (no scripts, no same-origin, no forms, no popups, no top navigation) - the guarantee
 * that matters if a pattern here is ever beaten by malformed markup. The CSP is written FIRST, so a
 * stray `<head>` later in the text cannot push it out of effect. It is stricter than the artboard's
 * own (no external fonts): a half-written page has no business reaching the network. The tokens are
 * the document's own, so `var(--ds-*)` in the model's HTML resolves as it will on the artboard.
 */
export function staticPreviewHtml(html: string, tokens?: DesignTokens): string {
  const inert = html
    .replace(/<!--[\s\S]*?(?:-->|$)/g, "")
    .replace(/<(script|iframe|frame|frameset|object|embed|applet|form|template|noscript)\b[\s\S]*?(?:<\/\1\s*>|$)/gi, "")
    .replace(/<\/?(?:script|meta|link|base|iframe|frame|object|embed|form|input|button|svg|math)\b[^>]*>?/gi, "")
    .replace(/\s+on[a-z]+\s*=\s*(?:"[^"]*"?|'[^']*'?|[^\s>]*)/gi, "")
    .replace(/\s(?:href|srcset|action|formaction|xlink:href|poster|data)\s*=\s*(?:"[^"]*"?|'[^']*'?|[^\s>]*)/gi, "")
    .replace(/\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi, (attribute, doubleQuoted: string | undefined, singleQuoted: string | undefined, bare: string | undefined) => {
      const value = doubleQuoted ?? singleQuoted ?? bare ?? "";
      return /^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(value) ? attribute : "";
    })
    .replace(/<[^>]*$/, "");
  const vars = tokens ? `<style>${tokensCss(tokens)}</style>` : "";
  return `<!doctype html><html><head><meta charset="utf-8">${PREVIEW_CSP_META}${vars}</head><body>${inert}</body></html>`;
}

/**
 * A still picture of a document's first page: what a proposal WOULD look like, drawn from the
 * server's dry run before anything is applied. Read-only on purpose: it is not the canvas, and
 * nothing drawn here can be edited or saved. Colours go through the same `safeColour` gate.
 * With `partialHtml` it instead shows the model's HTML so far, as a static, script-free page.
 */
export function DesignPreview({ doc, onNavigate, partialHtml, assets = new Map() }: { doc: DesignDocument; onNavigate?: (artboardId: string) => void; partialHtml?: string; assets?: ReadonlyMap<string, string> }) {
  const t = useT();
  if (partialHtml !== undefined) {
    const previewHtml = resolveAssetReferences(partialHtml, assets);
    return <iframe title={t("design.preview.label", { title: doc.title })} sandbox="" srcDoc={staticPreviewHtml(previewHtml, doc.tokens)} className="h-40 w-full rounded-card border border-hairline bg-surface-2" />;
  }
  const page = doc.pages[0];
  const thumbnail = page?.artboards.find((board) => board.thumbnailOf !== undefined);
  const source = thumbnail && doc.pages.flatMap((item) => item.artboards).find((board) => board.id === thumbnail.thumbnailOf);

  if (thumbnail && source) {
    return (
      <button type="button" aria-label={t("design.index.openScreen", { name: source.name })} onClick={() => onNavigate?.(source.id)} className="block w-full text-left">
        <svg role="img" aria-label={t("design.preview.label", { title: source.name })} viewBox={`0 0 ${source.width} ${source.height}`} className="h-40 w-full rounded-card border border-hairline bg-surface-2" preserveAspectRatio="xMidYMid meet">
          <rect width={source.width} height={source.height} fill={safeColour(source.background)} className="stroke-hairline" />
          {source.html != null ? <HtmlArtboardFrame html={source.html} assets={assets} tokens={doc.tokens} width={source.width} height={source.height} title={source.name} interactive={false} /> : <svg width={source.width} height={source.height} overflow="hidden">
            {source.nodeIds.map((id) => { const node = doc.nodes[id]; return node ? <NodeShape key={id} doc={doc} node={node} frame={node.frame} /> : null; })}
          </svg>}
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
            {board.html != null ? <HtmlArtboardFrame html={board.html} assets={assets} tokens={doc.tokens} width={board.width} height={board.height} title={board.name} interactive={false} /> : <svg width={board.width} height={board.height} overflow="hidden">
              {board.nodeIds.map((id) => {
                const node = doc.nodes[id];
                return node ? <NodeShape key={id} doc={doc} node={node} frame={node.frame} /> : null;
              })}
            </svg>}
          </g>
        );
      })}
    </svg>
  );
}
