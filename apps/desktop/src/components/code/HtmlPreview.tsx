import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Code2, Eye } from "lucide-react";

import { getFsFile } from "@/lib/api";
import { chartSpecOf } from "@/lib/chart/page";
import { reachesOutside } from "@/lib/chart/spec";
import { ChartView } from "@/components/code/ChartView";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

/** Stylesheet hrefs a page pulls in from its own folder, in source order.
 *
 * Local only. An absolute URL is somebody else's server and is left alone — the preview says what
 * it did not load rather than reaching out for it, which would also be the one way this component
 * could make a network request.
 */
export function localStylesheets(html: string): string[] {
  const encontrados: string[] = [];
  const re = /<link\b[^>]*>/gi;
  for (const tag of html.match(re) ?? []) {
    if (!/rel\s*=\s*["']?stylesheet/i.test(tag)) continue;
    const href = /href\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    if (!href) continue;
    if (/^[a-z]+:|^\/\//i.test(href)) continue; // absolute: not ours to fetch
    encontrados.push(href);
  }
  return encontrados;
}

/** Replace each local stylesheet link with the CSS itself. */
export function inlineStyles(html: string, css: Map<string, string>): string {
  return html.replace(/<link\b[^>]*>/gi, (tag) => {
    if (!/rel\s*=\s*["']?stylesheet/i.test(tag)) return tag;
    const href = /href\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1] ?? "";
    const folha = css.get(href);
    return folha === undefined ? tag : `<style>\n${folha}\n</style>`;
  });
}

/** The preview frame's own policy. Its comment is the list of what loads; keep the two in step.
 *
 * The frame also inherits the app page's policy (`chimera/api/page_csp.py`), and both must allow a
 * request for it to happen — so this one only has to be the narrow half. What it permits is the
 * page's own inline scripts and styles, and nothing from anywhere else. It used to let Vega in from
 * jsDelivr and allow the `new Function` Vega compiles expressions with, for `render_chart`'s pages
 * alone; those pages are now drawn by the app's own renderer from the spec they carry (`ChartView`,
 * `lib/chart/page.ts`), so no previewed page needs another site's script or `eval` any more.
 * What it refuses is every way CSP can govern for a page to send something out on its own —
 * `fetch`/XHR/WebSocket (`connect-src`), images and fonts from a host (`img-src`/`font-src` take
 * only embedded bytes), frames, workers, plugins and forms. A self-navigation to another host is
 * refused by the PARENT's `frame-src`, which this meta cannot express.
 *
 * It is NOT every way out, and the note under the frame says so. CSP does not govern WebRTC:
 * measured in headless Edge with both policies in place, the frame's `fetch` was blocked while an
 * `RTCPeerConnection` reached a STUN server over UDP and a TURN server over TCP — so a page can
 * still put data in a hostname (DNS) or hand it to a TURN server. Deleting `RTCPeerConnection`
 * from this frame would not close it either: a child `about:blank` frame gets a fresh realm with a
 * fresh constructor. `dns-prefetch` is outside CSP too. See `chimera/api/page_csp.py` for the measurement and the browser flag that would narrow
 * (not close) the WebRTC half.
 */
export const PREVIEW_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "img-src data: blob:",
  "font-src data:",
  "media-src data: blob:",
  "connect-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "object-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join("; ");

// Whitespace the HTML parser skips before a doctype — ASCII only; any other character is text.
const HTML_SPACE = /[\t\n\f\r ]/;
// A doctype at a given offset (sticky). `[^>]*` cannot backtrack against anything after it.
const DOCTYPE_AT = /<!doctype[^>]*>/iy;

/** End offset of a leading doctype (with the whitespace and comments before it), or 0 if none.
 *
 * Only a doctype at the very start counts — one further down is already ignored by the parser —
 * and keeping it first keeps standards mode.
 *
 * A scan, not a regex. The regex this replaced, `^(\s*(?:<!--[\s\S]*?-->\s*)*<!doctype…)`,
 * backtracked exponentially: the lazy body could swallow a `-->`, so N leading comments with no
 * doctype split 2^(N-1) ways — 28 of them (about 230 bytes, a file the agent can write) froze the
 * window's main thread for 13 s. Here every character is looked at a bounded number of times.
 *
 * A comment ends where the PARSER ends it: at the first `-->` or `--!>` after `<!--` (searching
 * from the `<!--`'s own dashes also catches the abrupt `<!-->` and `<!--->`). The old regex knew
 * only `-->`, so `<!-- a --!><script>…</script>--><!doctype html>` read as one comment followed by
 * a doctype, and the policy went in AFTER a script the parser runs. Taking the earliest terminator
 * can only end a comment sooner than the parser does, and the cost of that is finding no doctype
 * and putting the policy at offset zero — the safe side.
 */
export function leadingDoctypeEnd(html: string): number {
  let i = 0;
  for (;;) {
    while (i < html.length && HTML_SPACE.test(html[i])) i++;
    if (!html.startsWith("<!--", i)) break;
    const ends = [html.indexOf("-->", i + 2), html.indexOf("--!>", i + 2)].filter((n) => n >= 0);
    if (ends.length === 0) return 0; // an unclosed comment runs to the end: there is no doctype
    const end = Math.min(...ends);
    i = end + (html.startsWith("-->", end) ? 3 : 4);
  }
  DOCTYPE_AT.lastIndex = i;
  return DOCTYPE_AT.test(html) ? DOCTYPE_AT.lastIndex : 0;
}

/** Put the policy `<meta>` at the very top of the document, before anything the page wrote.
 *
 * A `<meta>` policy governs only what comes AFTER it. Inserting it after `<head>` would let a page
 * that opens with `<script>…</script><head>` run that script before the policy exists. So it goes
 * first — after the doctype if there is one, otherwise at offset zero; the parser files a leading
 * `<meta>` into the head it creates, and the page's own `<html>`/`<head>` merge into that.
 */
export function withPreviewPolicy(html: string): string {
  const meta = `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">`;
  const end = leadingDoctypeEnd(html);
  return html.slice(0, end) + meta + html.slice(end);
}

/** Path of a sibling file, for a href relative to the page being previewed. */
export function siblingOf(pagePath: string, href: string): string {
  const dir = pagePath.includes("/") ? pagePath.slice(0, pagePath.lastIndexOf("/") + 1) : "";
  return href.startsWith("./") ? dir + href.slice(2) : dir + href;
}

/**
 * The page the agent just wrote, as a page.
 *
 * The viewer rendered HTML as syntax-highlighted source, which is the right answer for code and the
 * wrong one for a document: someone who asked for a landing page and is shown angle brackets cannot
 * tell whether the thing works. The defect a non-technical person is best placed to catch is
 * visual, and until now nothing in this app could show it to them.
 *
 * **`srcdoc` with a sandbox, not a URL.** `fs_api.py` refuses to serve `.html` and that refusal is
 * right: the app's bearer token is a `<meta>` tag in its own index.html, so a same-origin document
 * could read it and drive the API. Passing the text as `srcdoc` with `sandbox` (and deliberately
 * WITHOUT `allow-same-origin`) puts the page in an opaque origin — it cannot reach the API, cannot
 * read storage, and cannot navigate the app.
 *
 * **Local stylesheets are inlined**, because a preview that renders every page unstyled is worse
 * than no preview: it shows a broken version of working work, and the person cannot tell which of
 * the two they are looking at. What loads and what does not is set by `PREVIEW_CSP` and said out
 * loud underneath rather than left to be discovered.
 *
 * The note used to say scripts and other sites' resources did not load. Neither was true: with no
 * policy anywhere, the frame ran scripts (it has `allow-scripts`) and fetched whatever the page
 * named — which is also how `render_chart` charts drew at all. The policy makes most of it true;
 * the sentence (same key, corrected in all ten languages) names the channel no policy here governs
 * (WebRTC and its DNS lookups), instead of promising a sealed frame. An earlier draft of this change
 * said "requests to other sites are blocked" outright, which is the guard that affirms what the code
 * does not do. It also named jsDelivr, the one site charts needed; since charts are drawn by the app
 * (study 29, P6.1) no site is admitted, and the sentence says that instead.
 */
export function HtmlPreview({ workspace, path, source }: {
  workspace: string;
  path: string;
  source: string;
}) {
  const t = useT();
  const [showing, setShowing] = useState(true);

  // A page `render_chart` wrote is drawn from its spec rather than run: its own script loads Vega
  // from a CDN the page policy no longer admits.
  const chart = useMemo(() => chartSpecOf(source), [source]);
  const sheets = useMemo(() => localStylesheets(source), [source]);
  const q = useQuery({
    queryKey: ["html-preview-css", workspace, path, sheets.join("|")],
    enabled: showing && !chart && sheets.length > 0,
    queryFn: async () => {
      const pares = await Promise.all(
        sheets.map(async (href) => {
          try {
            const f = await getFsFile(workspace || null, siblingOf(path, href));
            return [href, f.content ?? ""] as const;
          } catch {
            // A stylesheet that is not there is a fact about the page, not a failure of the
            // preview: the link stays in the markup and the notice below says it did not load.
            return null;
          }
        }),
      );
      return new Map(pares.filter((p): p is readonly [string, string] => p !== null));
    },
  });

  const faltando = sheets.length - (q.data?.size ?? 0);
  const doc = useMemo(
    () => withPreviewPolicy(q.data ? inlineStyles(source, q.data) : source),
    [source, q.data],
  );

  // Remount the frame when the document changes: an iframe keeps whatever it loaded first, so
  // editing a file and previewing again would show the previous version.
  const [nonce, setNonce] = useState(0);
  useEffect(() => setNonce((n) => n + 1), [doc]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-1.5">
        <button
          type="button"
          onClick={() => setShowing((s) => !s)}
          className={cn(
            "flex items-center gap-1.5 rounded-chip px-2 py-1 text-xs",
            "text-muted-foreground transition hover:text-foreground",
            focusRing,
          )}
        >
          {showing ? <Code2 className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          {t(showing ? "code.preview.showSource" : "code.preview.showPage")}
        </button>
        {showing && sheets.length > 0 && q.isLoading ? (
          <span className="text-xs text-muted-foreground">{t("code.preview.loadingCss")}</span>
        ) : null}
      </div>
      {showing && chart ? (
        <>
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {reachesOutside(chart) ? (
              <p className="text-xs text-muted-foreground">{t("code.preview.chartExternal")}</p>
            ) : (
              <ChartView spec={chart} />
            )}
          </div>
          <p className="border-t border-hairline px-3 py-1.5 text-xs text-muted-foreground">
            {t("code.preview.chartNote")}
          </p>
        </>
      ) : showing ? (
        <>
          <iframe
            key={nonce}
            title={t("code.preview.frameTitle", { name: path })}
            // No `allow-same-origin`: the page must not be able to reach this app's API, read its
            // storage, or navigate it. Scripts are allowed because a page that needs them is a page
            // whose defect is invisible without them — and an opaque origin is what makes that safe.
            sandbox="allow-scripts"
            srcDoc={doc}
            className="min-h-0 w-full flex-1 border-0 bg-white"
          />
          {/* Said, not discovered. A preview that silently omits half the page teaches the user to
              distrust the preview — or worse, to distrust work that is fine. */}
          <p className="border-t border-hairline px-3 py-1.5 text-xs text-muted-foreground">
            {faltando > 0
              ? t("code.preview.partial", { n: faltando })
              : t("code.preview.note")}
          </p>
        </>
      ) : null}
    </div>
  );
}
