import { useEffect, useRef, useState } from "react";
import { BarChart3, Loader2 } from "lucide-react";

import type { ChartTheme } from "@/lib/chart/render";
import { preflight } from "@/lib/chart/preflight";
import { titleOf, withheldOf, type CodeChartFrame } from "@/lib/chart/spec";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

/** A theme token as the colour it resolves to right now.
 *
 * Vega writes colours into SVG attributes, which cannot hold `var()`, so the token is resolved by
 * the browser on a throwaway element rather than parsed here: the value is whatever the theme says,
 * and no colour is written in this file (DESIGN.md).
 */
function tokenColour(name: string): string {
  const probe = document.createElement("span");
  probe.style.display = "none";
  probe.style.color = `hsl(var(--${name}))`;
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  // A resolver that cannot do `var()` (jsdom) answers "" or echoes the expression: take the text
  // colour around the chart rather than hand Vega a value it cannot paint.
  return resolved && !resolved.includes("var(") ? resolved : "currentColor";
}

export function chartTheme(): ChartTheme {
  const font = getComputedStyle(document.documentElement).getPropertyValue("--font-sans").trim();
  return {
    text: tokenColour("foreground"),
    muted: tokenColour("muted-foreground"),
    hairline: tokenColour("hairline"),
    border: tokenColour("border"),
    font: font || "sans-serif",
    // The brand pair first, then colours that read apart from it. Status colours come last: a
    // series painted `bad` reads as a failure before its legend is read.
    series: ["accent", "accent2", "muted-foreground", "warn", "ok", "bad"].map(tokenColour),
  };
}

type Drawing =
  | { state: "drawing" }
  | { state: "drawn" }
  | { state: "failed"; error: string }
  | { state: "heavy" }
  | { state: "unchecked"; error: string };

// What on <html> the chart's drawing reads: the colours (`data-theme`) and the font, which
// Settings swaps through `data-font` (`theme.ts: applyUiFont`) and Vega receives already resolved.
const THEME_ATTRIBUTES = ["data-theme", "data-font"];

/** The theme as a value that changes when the person switches it, so the chart is redrawn. */
export function useThemeKey(): string {
  const read = () => THEME_ATTRIBUTES.map((name) => document.documentElement.getAttribute(name) ?? "").join("|");
  const [key, setKey] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setKey(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: THEME_ATTRIBUTES });
    return () => observer.disconnect();
  }, []);
  return key;
}

/**
 * A Vega-Lite spec, drawn by the app.
 *
 * Vega arrives by dynamic import, so the conversation and the viewer pay for it the first time a
 * chart is on screen and never before. Drawing states are said, not left blank: a chart that is
 * still loading and one that failed must not look the same.
 *
 * Nothing is drawn before it is known to be light enough to draw here, because the drawing runs on
 * the app's own thread: a spec that states a heavy generator is refused as read (`withheldOf`), and
 * any other runs first off the thread under a time budget (`preflight`). It is checked here, not
 * only by the callers, because this is the one place that draws.
 */
export function ChartView({ spec }: { spec: Record<string, unknown> }) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const [drawing, setDrawing] = useState<Drawing>({ state: "drawing" });
  const themeKey = useThemeKey();
  const title = titleOf(spec);
  const held = withheldOf(spec);

  useEffect(() => {
    const el = host.current;
    if (!el || held) return;
    let alive = true;
    let teardown: (() => void) | null = null;
    const stop = new AbortController();
    setDrawing({ state: "drawing" });
    void preflight(spec, stop.signal)
      .then(async (check) => {
        if (!alive) return;
        if (check.verdict !== "draw") {
          setDrawing(check.verdict === "heavy" ? { state: "heavy" } : { state: check.verdict, error: check.error });
          return;
        }
        const { renderChart } = await import("@/lib/chart/render");
        const finalize = await renderChart(el, spec, chartTheme());
        if (!alive) {
          finalize();
          return;
        }
        teardown = finalize;
        setDrawing({ state: "drawn" });
      })
      .catch((err: unknown) => {
        if (alive) setDrawing({ state: "failed", error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
      stop.abort();
      teardown?.();
      el.replaceChildren();
    };
  }, [spec, themeKey, held]);

  if (held === "external") return <p className="text-xs text-muted-foreground">{t("code.chart.withheld.external")}</p>;
  if (held === "heavy") return <p className="text-xs text-muted-foreground">{t("code.chart.withheld.heavy")}</p>;

  return (
    <div className="space-y-1">
      {/* State changes are announced; the drawing itself is not a live region. */}
      <p role="status" className="text-xs text-muted-foreground">
        {drawing.state === "drawing" ? (
          <span className="flex items-center gap-1.5">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t("code.chart.drawing")}
          </span>
        ) : drawing.state === "failed" ? (
          <span className="text-bad-foreground">{t("code.chart.failed", { error: drawing.error })}</span>
        ) : drawing.state === "heavy" ? (
          t("code.chart.withheld.heavy")
        ) : drawing.state === "unchecked" ? (
          t("code.chart.unchecked", { error: drawing.error })
        ) : null}
      </p>
      <div
        ref={host}
        role="img"
        aria-label={t("code.chart.label", { title: title ?? t("code.chart.untitled") })}
        data-testid="chart-view"
        className="w-full overflow-x-auto text-foreground"
      />
    </div>
  );
}

/** The conversation's card for a chart the agent drew: the chart, its file, or why it is not drawn. */
export function ChartCard({
  frame,
  onOpenFile,
}: {
  frame: CodeChartFrame;
  onOpenFile?: (path: string) => void;
}) {
  const t = useT();
  // The server already withholds a spec that reaches outside itself; checked again here because a
  // frame is data off the wire, and this is the line that decides what the window draws.
  const withheld = frame.withheld ?? (frame.spec ? withheldOf(frame.spec) : null);
  return (
    <section
      aria-label={t("layout.card.name.chart")}
      className="space-y-2 rounded-chip border border-border p-2"
    >
      <div className="flex items-center gap-1.5 pr-16 text-xs uppercase tracking-wider text-muted-foreground">
        <BarChart3 className="h-3 w-3" />
        <span className="min-w-0 truncate">{frame.title ?? t("layout.card.name.chart")}</span>
      </div>
      {withheld === "external" ? (
        <p className="text-xs text-muted-foreground">{t("code.chart.withheld.external")}</p>
      ) : withheld === "heavy" ? (
        <p className="text-xs text-muted-foreground">{t("code.chart.withheld.heavy")}</p>
      ) : withheld === "large" || !frame.spec ? (
        <p className="text-xs text-muted-foreground">
          {t("code.chart.withheld.large", { kb: String(Math.ceil(frame.bytes / 1000)) })}
        </p>
      ) : (
        <ChartView spec={frame.spec} />
      )}
      {onOpenFile ? (
        <button
          type="button"
          onClick={() => onOpenFile(frame.path)}
          className={cn("rounded-chip font-mono text-xs text-accent-ink underline decoration-dotted", focusRing)}
        >
          {t("code.chart.open", { path: frame.path })}
        </button>
      ) : (
        <p className="font-mono text-xs text-muted-foreground">{frame.path}</p>
      )}
    </section>
  );
}
