/**
 * Draw a Vega-Lite spec into an element, with the app's own Vega and nothing else.
 *
 * Loaded only by dynamic import (`ChartView`), so the ~1 MB of Vega is a chunk of its own that a
 * session with no chart never downloads. Three choices make it safe to hand a spec the agent wrote:
 *
 * - **No eval.** Vega compiles expressions with `new Function` unless it is given an interpreter.
 *   Parsing with `ast: true` and running with `vega-interpreter` keeps every expression as data, so
 *   the page's policy (`chimera/api/page_csp.py`) does not need `'unsafe-eval'` for a chart.
 * - **No network.** The loader refuses every load and every link. A spec that names data by URL or
 *   an image by address cannot make the window fetch it — the server already withholds such specs
 *   from the conversation (`chart_frame`), and this is the second lock, for a file in the viewer.
 * - **SVG, not canvas.** Text in the chart is text in the DOM, readable and selectable, and the
 *   renderer needs no canvas, which is also what lets the tests draw in jsdom.
 * - **Nothing outside its own element.** The chart draws in the app's document, not a frame, so
 *   whatever the spec says, its inputs go in its own container and it listens to no event on the
 *   window or on an element it names (`sealEvents`).
 */
import { formats, loader as vegaLoader, parse, View, type Loader, type Spec } from "vega";
import { compile, type TopLevelSpec } from "vega-lite";
import { expressionInterpreter } from "vega-interpreter";

import { parseObjects } from "@/lib/chart/dsv";

// vega-loader's format registry, which `vega` re-exports at runtime but its typings leave out.
declare module "vega" {
  export function formats(name: string, reader: DelimitedReader): unknown;
}

type DelimitedReader = ((data: unknown, format?: { delimiter?: string; header?: string[] }) => object[]) & {
  responseType: "text";
};

function delimited(fixed?: string): DelimitedReader {
  const read = (data: unknown, format?: { delimiter?: string; header?: string[] }) =>
    parseObjects(String(data), fixed ?? format?.delimiter ?? ",", format?.header);
  return Object.assign(read, { responseType: "text" as const });
}

// Vega's own readers for delimited text build each row with `new Function` (d3-dsv), which the
// page's policy refuses. These read the same text by the same rules without it. The registry is
// this module's Vega's, and this module is the only thing in the app that draws with it.
formats("csv", delimited(","));
formats("tsv", delimited("\t"));
formats("dsv", delimited());

/** Colours and font the chart takes from the theme, resolved to concrete values. */
export interface ChartTheme {
  text: string;
  muted: string;
  hairline: string;
  border: string;
  font: string;
  series: string[];
}

/** Thrown for a load the chart asked for: it names what was refused, so the card can say it. */
export class RefusedLoad extends Error {
  constructor(readonly uri: string) {
    super(`refused to load ${uri}`);
  }
}

/** A loader that loads nothing. `sanitize` gates images and links, `load` gates data. */
function sealedLoader(): Loader {
  const sealed = vegaLoader();
  const refuse = (uri: unknown) => Promise.reject(new RefusedLoad(String(uri)));
  sealed.load = refuse;
  sealed.sanitize = refuse;
  sealed.http = refuse;
  sealed.file = refuse;
  return sealed;
}

function config(theme: ChartTheme) {
  const axis = {
    labelColor: theme.muted,
    titleColor: theme.text,
    domainColor: theme.border,
    tickColor: theme.border,
    gridColor: theme.hairline,
    labelFont: theme.font,
    titleFont: theme.font,
  };
  return {
    background: "transparent",
    font: theme.font,
    view: { stroke: theme.hairline },
    axis,
    legend: { labelColor: theme.muted, titleColor: theme.text, labelFont: theme.font, titleFont: theme.font },
    header: { labelColor: theme.muted, titleColor: theme.text },
    title: { color: theme.text, subtitleColor: theme.muted, font: theme.font },
    mark: { color: theme.series[0] },
    range: { category: theme.series },
  };
}

/**
 * Keep the chart's events and inputs inside its own element.
 *
 * Inside a frame these reached only the frame's document. Here the chart shares the app's: a
 * binding's `element` is cleared and replaced with the input (`#root` would wipe the app, or the
 * approval card), and `window:` and selector event sources would hear every key typed in the
 * composer and in Settings. Written over the compiled config, because the spec's own `config.events`
 * would otherwise win, and the spec is the model's text.
 *
 * `timer` is off for the same reason a heavy spec is withheld (`withheldOf`, `preflight.ts`): a
 * selection `on: "timer{1}"` re-runs the whole dataflow every millisecond for as long as the chart is
 * on screen, on the thread the rest of the app runs on. Nothing a chart here needs moves by itself.
 */
export function sealEvents(vg: Spec): Spec {
  const config = vg.config ?? {};
  return {
    ...vg,
    config: {
      ...config,
      events: {
        ...(config.events ?? {}),
        bind: "container",
        window: false,
        selector: false,
        timer: false,
        globalCursor: false,
      },
    },
  };
}

/** A spec compiled, sealed and parsed, ready for a View. The theme fills what the spec left open. */
function runtimeOf(spec: Record<string, unknown>, theme?: ChartTheme) {
  // The spec's own `config` still wins over the theme where it sets something: an author who chose
  // a colour chose it. The theme only fills what the spec left open.
  const { spec: vg } = compile(spec as unknown as TopLevelSpec, theme ? { config: config(theme) } : {});
  return parse(sealEvents(vg), undefined, { ast: true });
}

/** Every item in a scene, at any depth: what the SVG renderer would make a node for. */
function countItems(node: unknown): number {
  const items = (node as { items?: unknown } | null)?.items;
  if (!Array.isArray(items)) return 0;
  return items.reduce((sum: number, item: unknown) => sum + 1 + countItems(item), 0);
}

/**
 * Run `spec` headless and say how many scene items it made, drawing nothing.
 *
 * What `preflight.worker.ts` runs off the app's thread, under a time budget, before the chart is
 * drawn on it: the dataflow is where a spec's cost is, and it runs the same with no renderer.
 */
export async function measureChart(spec: Record<string, unknown>): Promise<number> {
  const view = new View(runtimeOf(spec), { renderer: "none", loader: sealedLoader(), expr: expressionInterpreter });
  try {
    await view.runAsync();
    // The typings describe `scenegraph()` as a Scene; at runtime it is the Scenegraph holding one.
    return countItems((view.scenegraph() as unknown as { root: unknown }).root);
  } finally {
    view.finalize();
  }
}

/** Draw `spec` into `el`. Returns the function that tears the view down. */
export async function renderChart(
  el: HTMLElement,
  spec: Record<string, unknown>,
  theme: ChartTheme,
): Promise<() => void> {
  const view = new View(runtimeOf(spec, theme), {
    renderer: "svg",
    container: el,
    loader: sealedLoader(),
    expr: expressionInterpreter,
    hover: true,
  });
  await view.runAsync();
  return () => view.finalize();
}
