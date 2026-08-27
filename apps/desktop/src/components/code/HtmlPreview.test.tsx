import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  HtmlPreview,
  inlineStyles,
  localStylesheets,
  siblingOf,
} from "@/components/code/HtmlPreview";
import { getFsFile } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

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
