import { Globe } from "lucide-react";
import type { CodeBrowserFrame } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * The agent's browser, as the person sees it: the viewport after the last action of this turn.
 *
 * The model reads a page as text (the tag tree `render_elements` prints), and until this panel the
 * person read the same text — or, with `CHIMERA_BROWSER_HEADLESS` off, watched a second Chromium
 * window beside the app. This is one JPEG per browser action, sent on the turn's own stream and
 * never replayed (a reopened conversation shows no frame, honestly, rather than a page that has
 * moved on). The address is printed as text next to the picture on purpose: a screenshot of a page
 * is a claim about where the browser is, and the URL is the part of that claim a person can check.
 *
 * Nothing here is interactive. The person is watching, not driving — clicking the picture would
 * click nothing, and a control that looks live and is not would be the first lie on this screen.
 */
export function BrowserView({ frame }: { frame: CodeBrowserFrame | null | undefined }) {
  const t = useT();
  if (!frame) return null;
  return (
    <figure className="space-y-1 rounded-chip border border-border p-2" data-testid="browser-view">
      <figcaption className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
        <Globe className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="uppercase tracking-wider">{t("code.browser.title")}</span>
        <span className="min-w-0 truncate font-mono text-foreground/80" title={frame.url}>
          {frame.url}
        </span>
        <span className="ml-auto shrink-0 whitespace-nowrap">
          {t("code.browser.frame", { n: frame.n, action: frame.action })}
        </span>
      </figcaption>
      <img
        src={`data:image/jpeg;base64,${frame.jpeg}`}
        alt={t("code.browser.alt", { title: frame.title || frame.url })}
        width={frame.width || undefined}
        height={frame.height || undefined}
        className="w-full rounded-chip border border-hairline bg-surface-2"
      />
    </figure>
  );
}
