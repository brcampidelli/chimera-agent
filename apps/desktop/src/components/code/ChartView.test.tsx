import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ChartCard } from "@/components/code/ChartView";
import { preflight } from "@/lib/chart/preflight";
import { renderChart } from "@/lib/chart/render";
import type { CodeChartFrame } from "@/lib/chart/spec";
import { renderWithProviders } from "@/test/utils";

// The check off the app's thread runs in a Worker, which jsdom does not have; its protocol is
// tested in preflight.test.ts. Here it answers "draw" unless a test says otherwise, and the real
// renderer is wrapped so a test can say it was never reached.
vi.mock("@/lib/chart/preflight", () => ({ preflight: vi.fn(async () => ({ verdict: "draw" })) }));
vi.mock("@/lib/chart/render", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/chart/render")>();
  return { ...real, renderChart: vi.fn(real.renderChart) };
});

// Vega is a dynamic import, and the first one of a run can take seconds under a loaded suite.
// Loaded once up front, so each test's wait measures the drawing, not the download.
beforeAll(async () => {
  await import("@/lib/chart/render");
}, 60000);

// `restoreMocks` restores spies, not a `vi.fn` made in a module mock: each test starts from "draw".
// A test's own answer is set for the whole test (not once), because a strict-mode mount runs the
// drawing effect twice and the second run would otherwise get the default.
beforeEach(() => {
  vi.mocked(preflight).mockReset().mockResolvedValue({ verdict: "draw" });
  vi.mocked(renderChart).mockClear();
});

/**
 * The card a chart gets in the conversation (study 29, P6.1).
 *
 * It draws the spec the turn carried, says when it is still drawing or could not draw, and when the
 * server withheld the spec it says why instead of drawing nothing — a blank card and a chart with no
 * data must not look like the same thing. The file is always one click away.
 */
const SPEC = {
  title: "Sales",
  data: { values: [{ a: "A", b: 5 }, { a: "B", b: 8 }] },
  mark: "bar",
  encoding: { x: { field: "a", type: "nominal" }, y: { field: "b", type: "quantitative" } },
};

function frame(over: Partial<CodeChartFrame> = {}): CodeChartFrame {
  return { path: "charts/sales.html", format: "html", title: "Sales", spec: SPEC, withheld: null, bytes: 180, ...over };
}

describe("ChartCard", () => {
  it("draws the chart the turn carried, labelled with its title", async () => {
    renderWithProviders(<ChartCard frame={frame()} />);

    const view = screen.getByTestId("chart-view");
    expect(view.getAttribute("aria-label")).toBe("Chart: Sales");
    await waitFor(() => expect(view.querySelector("svg")).not.toBeNull(), { timeout: 10000 });
    expect(screen.queryByText(/could not be drawn/)).toBeNull();
  });

  it("opens the file it wrote", async () => {
    const open = vi.fn();
    renderWithProviders(<ChartCard frame={frame()} onOpenFile={open} />);
    await userEvent.click(screen.getByRole("button", { name: "Open charts/sales.html" }));
    expect(open).toHaveBeenCalledWith("charts/sales.html");
  });

  it("says why a chart that reaches outside itself is not drawn", () => {
    renderWithProviders(<ChartCard frame={frame({ spec: null, withheld: "external" })} />);
    expect(screen.getByText(/loads data or follows links from outside itself/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-view")).toBeNull();
  });

  it("does not trust a frame that says nothing is withheld while its spec names an address", () => {
    // The server withholds such a spec; a frame is data off the wire, so the card decides again.
    const spec = { ...SPEC, data: { url: "https://example.com/d.csv" } };
    renderWithProviders(<ChartCard frame={frame({ spec, withheld: null })} />);
    expect(screen.getByText(/loads data or follows links from outside itself/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-view")).toBeNull();
  });

  it("says a chart too large to carry is in the file, and how large", () => {
    renderWithProviders(<ChartCard frame={frame({ spec: null, withheld: "large", bytes: 512_400 })} />);
    const card = screen.getByRole("region", { name: "Chart" });
    expect(within(card).getByText(/too large to carry in the conversation \(513 KB\)/)).toBeInTheDocument();
  });

  it("does not run a spec that makes up millions of rows, and says why", () => {
    // 125 bytes that held Vega for 29.6 s on the thread the composer runs on. The server withholds
    // it; a frame is data off the wire, so the card decides again, before anything runs.
    const spec = {
      data: { sequence: { start: 0, stop: 4000000, as: "x" } },
      mark: "point",
      encoding: { x: { field: "x", type: "quantitative" } },
    };
    renderWithProviders(<ChartCard frame={frame({ spec, withheld: null })} />);
    expect(screen.getByText(/without freezing the window/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-view")).toBeNull();
    expect(preflight).not.toHaveBeenCalled();
    expect(renderChart).not.toHaveBeenCalled();
  });

  it("says a chart the server withheld as heavy is in the file", () => {
    renderWithProviders(<ChartCard frame={frame({ spec: null, withheld: "heavy" })} />);
    expect(screen.getByText(/without freezing the window/)).toBeInTheDocument();
    expect(screen.getByText("charts/sales.html")).toBeInTheDocument();
  });

  it("does not draw a chart its check off the thread called heavy", async () => {
    vi.mocked(preflight).mockResolvedValue({ verdict: "heavy" });
    renderWithProviders(<ChartCard frame={frame()} />);
    expect(await screen.findByText(/without freezing the window/)).toBeInTheDocument();
    expect(renderChart).not.toHaveBeenCalled();
  });

  it("does not draw a chart whose check did not run, and says so", async () => {
    vi.mocked(preflight).mockResolvedValue({ verdict: "unchecked", error: "no worker" });
    renderWithProviders(<ChartCard frame={frame()} />);
    expect(await screen.findByText(/check that keeps a chart from freezing the window did not run \(no worker\)/)).toBeInTheDocument();
    expect(renderChart).not.toHaveBeenCalled();
  });

  it("redraws in the new font when the person switches it", async () => {
    // Settings swaps the font through `data-font` (theme.ts), and Vega is handed it resolved, so
    // a chart already on screen kept the old one until the conversation was remounted.
    const root = document.documentElement;
    root.style.setProperty("--font-sans", "FontBefore");
    try {
      renderWithProviders(<ChartCard frame={frame()} />);
      const view = screen.getByTestId("chart-view");
      await waitFor(() => expect(view.querySelector('text[font-family="FontBefore"]')).not.toBeNull(), { timeout: 10000 });
      // The appearance provider stamps `data-theme` on mount, which redraws the chart once. Let that
      // redraw finish first: one still in flight would pick up the new font by itself and pass this
      // test with the font switch unobserved.
      await new Promise((resolve) => setTimeout(resolve, 500));
      const draws = vi.mocked(renderChart).mock.calls.length;
      expect(view.querySelector('text[font-family="FontBefore"]')).not.toBeNull();
      root.style.setProperty("--font-sans", "FontAfter");
      root.dataset.font = "serif";
      await waitFor(() => expect(view.querySelector('text[font-family="FontAfter"]')).not.toBeNull(), { timeout: 10000 });
      expect(vi.mocked(renderChart).mock.calls.length).toBeGreaterThan(draws);
    } finally {
      root.style.removeProperty("--font-sans");
    }
  });

  it("says when it could not draw, instead of leaving the card blank", async () => {
    // Not a chart Vega can compile: a mark it does not know.
    renderWithProviders(<ChartCard frame={frame({ spec: { ...SPEC, mark: "no-such-mark" } })} />);
    expect(await screen.findByText(/could not be drawn here/, undefined, { timeout: 10000 })).toBeInTheDocument();
  });
});
