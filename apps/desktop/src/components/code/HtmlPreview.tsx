import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Code2, Eye } from "lucide-react";

import { getFsFile } from "@/lib/api";
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
 * the two they are looking at. What still will not load — images, scripts, fonts, anything
 * absolute — is said out loud underneath rather than left to be discovered.
 */
export function HtmlPreview({ workspace, path, source }: {
  workspace: string;
  path: string;
  source: string;
}) {
  const t = useT();
  const [showing, setShowing] = useState(true);

  const sheets = useMemo(() => localStylesheets(source), [source]);
  const q = useQuery({
    queryKey: ["html-preview-css", workspace, path, sheets.join("|")],
    enabled: showing && sheets.length > 0,
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
    () => (q.data ? inlineStyles(source, q.data) : source),
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
      {showing ? (
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
