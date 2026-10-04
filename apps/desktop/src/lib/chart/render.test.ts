import { afterEach, describe, expect, it, vi } from "vitest";

import { parse, View, type Spec } from "vega";
import { expressionInterpreter } from "vega-interpreter";

import { CHART_MAX_ITEMS } from "@/lib/chart/preflight";
import { measureChart, RefusedLoad, renderChart, sealEvents, type ChartTheme } from "@/lib/chart/render";

/**
 * The renderer the conversation and the viewer draw charts with.
 *
 * What it promises is narrow and each part is checked here against the thing it guards: it draws a
 * spec into SVG; it never compiles an expression into code (so the page policy needs no
 * `'unsafe-eval'` for a chart); and it never reaches the network, whatever the spec names.
 */
const THEME: ChartTheme = {
  text: "currentColor",
  muted: "currentColor",
  hairline: "currentColor",
  border: "currentColor",
  font: "sans-serif",
  series: ["currentColor"],
};

const BARS = {
  title: "Sales",
  data: { values: [{ a: "A", b: 5 }, { a: "B", b: 8 }, { a: "C", b: 3 }] },
  mark: "bar",
  encoding: { x: { field: "a", type: "nominal" }, y: { field: "b", type: "quantitative" } },
};

// Expressions exercise the code path Vega would compile: a calculate, a filter and a tooltip.
const WITH_EXPRESSIONS = {
  ...BARS,
  transform: [{ calculate: "datum.b * 2", as: "double" }, { filter: "datum.double > 6" }],
  encoding: { ...BARS.encoding, y: { field: "double", type: "quantitative" }, tooltip: { field: "a" } },
};

function host(): HTMLElement {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("renderChart", () => {
  it("draws a spec as SVG, one mark per row", async () => {
    const el = host();
    const finalize = await renderChart(el, BARS, THEME);
    const svg = el.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(el.querySelectorAll("path[aria-roledescription='bar']").length + el.querySelectorAll(".mark-rect path").length).toBeGreaterThanOrEqual(3);
    expect(el.textContent).toContain("Sales");
    finalize();
  });

  it("never turns an expression into code", async () => {
    // With eval and the Function constructor gone, the chart must still draw: this is what lets
    // the page drop 'unsafe-eval'. Without the interpreter Vega calls `Function(...)` here.
    const realFunction = globalThis.Function;
    const trap = new Proxy(realFunction, {
      apply: () => {
        throw new Error("Function() called");
      },
      construct: () => {
        throw new Error("new Function() called");
      },
    });
    vi.stubGlobal("Function", trap);
    vi.stubGlobal("eval", () => {
      throw new Error("eval called");
    });
    const el = host();
    try {
      const finalize = await renderChart(el, WITH_EXPRESSIONS, THEME);
      expect(el.querySelector("svg")).not.toBeNull();
      expect(el.querySelectorAll(".mark-rect path").length).toBe(2); // 10 and 16 pass the filter
      finalize();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("refuses data named by address and makes no request for it", async () => {
    const fetchSpy = vi.fn(() => Promise.reject(new Error("network")));
    vi.stubGlobal("fetch", fetchSpy);
    const spec = { ...BARS, data: { url: "https://example.com/steal?d=secret" } };
    const el = host();
    // Vega reports a failed load and draws the chart without its data; either way, no request.
    await renderChart(el, spec, THEME).catch((err: unknown) => {
      expect(err).toBeInstanceOf(RefusedLoad);
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses an image mark's address and makes no request for it", async () => {
    const fetchSpy = vi.fn(() => Promise.reject(new Error("network")));
    vi.stubGlobal("fetch", fetchSpy);
    const spec = {
      data: { values: [{ x: 1, img: "https://example.com/pixel.png?d=secret" }] },
      mark: "image",
      encoding: { x: { field: "x", type: "quantitative" }, url: { field: "img", type: "nominal" } },
    };
    const el = host();
    await renderChart(el, spec, THEME).catch(() => undefined);
    expect(fetchSpy).not.toHaveBeenCalled();
    for (const image of el.querySelectorAll("image")) {
      expect(image.getAttribute("href") ?? "").not.toContain("example.com");
    }
  });

  it("keeps a bound input inside the chart, whatever element the spec names", async () => {
    // The chart draws in the app's own document, not a frame. Vega clears the element a binding
    // names (`el.textContent = ''`) and puts its input there, so a spec naming `#root` or `body`
    // would wipe the conversation or the approval card and put its own labelled input in their place.
    for (const target of ["#app-root", "body"]) {
      const app = document.createElement("div");
      app.id = "app-root";
      const approve = document.createElement("button");
      approve.textContent = "Approve";
      app.appendChild(approve);
      document.body.appendChild(app);
      const el = host();
      const spec = {
        ...BARS,
        params: [{ name: "Click_Approve", value: "", bind: { input: "text", element: target } }],
        config: { events: { bind: "any" } }, // the spec asking for it does not get it either
      };
      const finalize = await renderChart(el, spec, THEME);
      expect(app.isConnected).toBe(true);
      expect(app.querySelector("button")?.textContent).toBe("Approve");
      expect(el.querySelector("svg")).not.toBeNull();
      // The input still exists, but in the chart's own form.
      expect(el.querySelector("input")).not.toBeNull();
      expect(app.querySelector("input")).toBeNull();
      finalize();
      document.body.replaceChildren();
    }
  });

  it("listens to nothing outside the chart", async () => {
    // `window:keydown` or a selector source would hear every key typed in the composer and in
    // Settings, since the chart now shares the app's window.
    const app = document.createElement("div");
    app.id = "app-root";
    document.body.appendChild(app);
    const onWindow = vi.spyOn(window, "addEventListener");
    const onApp = vi.spyOn(app, "addEventListener");
    const spec = {
      ...BARS,
      params: [
        { name: "keys", select: { type: "point", on: "window:keydown" } },
        { name: "clicks", select: { type: "point", on: "#app-root:click" } },
      ],
      config: { events: { window: true, selector: true } },
    };
    const el = host();
    try {
      const finalize = await renderChart(el, spec, THEME);
      expect(el.querySelector("svg")).not.toBeNull();
      expect(onWindow.mock.calls.map(([type]) => type)).not.toContain("keydown");
      expect(onApp).not.toHaveBeenCalled();
      finalize();
    } finally {
      onWindow.mockRestore();
      onApp.mockRestore();
    }
  });

  it("reads inline CSV, TSV and DSV without turning a row into code", async () => {
    // d3-dsv, which Vega reads delimited text with, builds each row with `new Function`; under the
    // page's policy (no 'unsafe-eval') that throws and the chart is not drawn.
    const realFunction = globalThis.Function;
    const trap = new Proxy(realFunction, {
      apply: () => {
        throw new Error("Function() called");
      },
      construct: () => {
        throw new Error("new Function() called");
      },
    });
    const encoding = BARS.encoding;
    const specs = [
      { data: { values: "a,b\nA,5\nB,8\n\"C, quoted\",3\n", format: { type: "csv" } }, mark: "bar", encoding },
      { data: { values: "a\tb\nA\t5\nB\t8\nC\t3", format: { type: "tsv" } }, mark: "bar", encoding },
      { data: { values: "a|b\r\nA|5\r\nB|8\r\nC|3", format: { type: "dsv", delimiter: "|" } }, mark: "bar", encoding },
      { datasets: { rows: "a,b\nA,5\nB,8\nC,3" }, data: { name: "rows", format: { type: "csv" } }, mark: "bar", encoding },
    ];
    vi.stubGlobal("Function", trap);
    try {
      for (const spec of specs) {
        const el = host();
        const finalize = await renderChart(el, spec, THEME);
        expect(el.querySelectorAll(".mark-rect path").length).toBe(3);
        finalize();
      }
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("measureChart", () => {
  it("runs a chart headless and counts its items, drawing nothing", async () => {
    const items = await measureChart(BARS);
    // Three bars, plus the axes, their ticks and labels, and the groups that hold them.
    expect(items).toBeGreaterThan(3);
    expect(document.querySelector("svg")).toBeNull();
  });

  it("counts the rows a spec makes up, so a heavy one is over the limit", async () => {
    const spec = {
      data: { sequence: { start: 0, stop: CHART_MAX_ITEMS + 1, as: "x" } },
      mark: "point",
      encoding: { x: { field: "x", type: "quantitative" } },
    };
    expect(await measureChart(spec)).toBeGreaterThan(CHART_MAX_ITEMS);
  }, 30000);
});

describe("sealEvents", () => {
  // A raw Vega spec, because a timer is a Vega event source: a signal counting its own ticks.
  const TICKING = {
    signals: [{ name: "ticks", value: 0, on: [{ events: "timer{1}", update: "ticks + 1" }] }],
    config: { events: { timer: true } },
  } as unknown as Spec;

  async function ticksAfter(spec: Spec, ms: number): Promise<number> {
    const view = new View(parse(spec, undefined, { ast: true }), { renderer: "none", expr: expressionInterpreter });
    await view.runAsync();
    await new Promise((resolve) => setTimeout(resolve, ms));
    const ticks = view.signal("ticks") as number;
    view.finalize();
    return ticks;
  }

  it("turns timers off, whatever the spec's own config says", async () => {
    // `on: "timer{1}"` re-ran the dataflow every millisecond on the app's thread for as long as the
    // chart was on screen. The unsealed spec shows the timer is real.
    expect(await ticksAfter(TICKING, 60)).toBeGreaterThan(0);
    expect(await ticksAfter(sealEvents(TICKING), 60)).toBe(0);
  });
});
