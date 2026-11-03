import { act, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/lib/i18n";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HtmlArtboardFrame, type HtmlElementSelection } from "@/components/design/HtmlArtboardFrame";
import type { DesignTokens } from "@/lib/design/model";
import { HTML_ARTBOARD_SELECTION_HASH, calculateSelectionScriptHash, artboardSrcDoc } from "@/lib/design/htmlArtboard";

// Counts how often the srcdoc is actually built. Spying on the module export through vi.mock is the
// only way to observe it: the component calls it from its own module, which a plain spy cannot reach.
const { srcDocBuilds } = vi.hoisted(() => ({ srcDocBuilds: vi.fn() }));
vi.mock("@/lib/design/htmlArtboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/design/htmlArtboard")>();
  return {
    ...actual,
    artboardSrcDoc: (html: string, tokens: DesignTokens, editing = false) => {
      srcDocBuilds();
      return actual.artboardSrcDoc(html, tokens, editing);
    },
  };
});

const tokens: DesignTokens = {
  colors: { ink: "#112233" },
  typography: {},
  spacing: {},
  radii: {},
};

/** The frame's notice is translated, so every render sits inside the provider the app uses. */
function renderIn(ui: React.ReactElement) {
  return render(<I18nProvider>{ui}</I18nProvider>);
}

function frame(html: string, title = "Hero board") {
  return (
    <svg>
      <HtmlArtboardFrame html={html} tokens={tokens} width={320} height={200} title={title} />
    </svg>
  );
}

function iframeOf(container: HTMLElement): HTMLIFrameElement {
  const found = container.querySelector("iframe");
  if (!found) throw new Error("no iframe rendered");
  return found;
}

describe("HtmlArtboardFrame", () => {
  beforeEach(() => srcDocBuilds.mockClear());

  it("keeps the same srcdoc and does not rebuild it when the parent re-renders with the same props", () => {
    const { container, rerender } = renderIn(frame("<main>Hero</main>"));
    const first = iframeOf(container);
    const srcDoc = first.getAttribute("srcdoc");
    expect(srcDocBuilds).toHaveBeenCalledTimes(1);

    rerender(<I18nProvider>{frame("<main>Hero</main>")}</I18nProvider>);
    expect(srcDocBuilds).toHaveBeenCalledTimes(1);
    expect(iframeOf(container).getAttribute("srcdoc")).toBe(srcDoc);
    // The same frame element survives: a reload would show up as a new node, not a changed attribute.
    expect(iframeOf(container)).toBe(first);
  });

  it("rebuilds the srcdoc when the markup changes", () => {
    const { container, rerender } = renderIn(frame("<main>Hero</main>"));
    const before = iframeOf(container).getAttribute("srcdoc");
    rerender(<I18nProvider>{frame("<main>Changed</main>")}</I18nProvider>);
    expect(srcDocBuilds).toHaveBeenCalledTimes(2);
    expect(iframeOf(container).getAttribute("srcdoc")).not.toBe(before);
    expect(iframeOf(container).getAttribute("srcdoc")).toContain("<main>Changed</main>");
  });

  it("is a memoised component, so an unchanged board skips rendering", () => {
    // Re-render counts are not observable from outside React, so this asserts the wrapper that
    // makes the skip happen; the test above asserts that nothing reloads when it does.
    expect((HtmlArtboardFrame as unknown as { $$typeof?: symbol }).$$typeof).toBe(Symbol.for("react.memo"));
  });

  it("uses the exact safe sandbox and CSP in edit mode and no scripts outside edit mode", () => {
    const editing = renderIn(<svg><HtmlArtboardFrame html="<main>Hero</main>" tokens={tokens} width={320} height={200} title="Board" editing onElementSelect={() => undefined} /></svg>);
    const iframe = iframeOf(editing.container);
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    const csp = iframe.getAttribute("srcdoc")?.match(/script-src '[^']+'/)?.[0];
    expect(csp).toBe(`script-src 'sha256-${HTML_ARTBOARD_SELECTION_HASH}'`);
    expect(csp).not.toContain("unsafe-inline");
    const plain = iframeOf(renderIn(frame("<main>Hero</main>")).container);
    expect(plain.getAttribute("sandbox")).toBe("");
    expect(plain.getAttribute("srcdoc")).toContain("script-src 'none'");
  });

  it("renders preview boards with model code removed and scripts disabled", () => {
    const source = '<main></main><script type="text/x-chimera">class Component { renderIn(state) {} }</script>';
    const iframe = iframeOf(renderIn(<svg><HtmlArtboardFrame html={source} tokens={tokens} width={320} height={200} title="Board" interactive={false} /></svg>).container);
    expect(iframe.getAttribute("sandbox")).toBe("");
    expect(iframe.getAttribute("srcdoc")).toContain("script-src 'none'");
    expect(iframe.getAttribute("srcdoc")).not.toContain("window.__chimeraRegister");
  });

  it.each(["allow-same-origin", "allow-forms", "allow-popups", "allow-top-navigation"])("model sandbox omits %s", (forbidden) => {
    const source = '<main></main><script type="text/x-chimera">class Component { renderIn(state) {} }</script>';
    const iframe = iframeOf(renderIn(frame(source)).container);
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.getAttribute("sandbox")).not.toContain(forbidden);
    expect(iframe.getAttribute("srcdoc")).toContain("connect-src 'none'");
  });

  it("keeps the selection-script hash pinned to its exact text", async () => {
    expect(await calculateSelectionScriptHash()).toBe(HTML_ARTBOARD_SELECTION_HASH);
    expect(artboardSrcDoc("<main />", tokens, true)).toContain("script-src 'sha256-");
  });

  it("sends the pending point once the editable document loads, and not again for the same request", () => {
    const board = (html: string, selectAt?: { x: number; y: number; key: number }) => (
      <I18nProvider>
        <svg>
          <HtmlArtboardFrame html={html} tokens={tokens} width={320} height={200} title="Board" editing onElementSelect={() => undefined} selectAt={selectAt} />
        </svg>
      </I18nProvider>
    );
    const { container, rerender } = render(board("<main>Hero</main>", { x: 12, y: 34, key: 1 }));
    const frame = iframeOf(container);
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => { frame.dispatchEvent(new Event("load")); });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith({ type: "chimera-design-select-at", x: 12, y: 34 }, "*");

    // An edit reloads the document with the same request still in place: nothing is selected twice.
    act(() => rerender(board("<main>Edited</main>", { x: 12, y: 34, key: 1 })));
    act(() => { frame.dispatchEvent(new Event("load")); });
    expect(post).toHaveBeenCalledTimes(1);
  });

  describe("a pending point replaces an older selection", () => {
    const rect = { x: 1, y: 2, width: 3, height: 4 };
    function editable(html: string, selectAt?: { x: number; y: number; key: number }) {
      return (
        <I18nProvider>
          <svg>
            <HtmlArtboardFrame html={html} tokens={tokens} width={320} height={200} title="Board" editing onElementSelect={onSelect} onSelectionRejected={() => undefined} selectAt={selectAt} />
          </svg>
        </I18nProvider>
      );
    }
    let onSelect = vi.fn<(selection: HtmlElementSelection | null) => void>();
    beforeEach(() => { onSelect = vi.fn<(selection: HtmlElementSelection | null) => void>(); });
    /** The element 5 was selected in this board, then the board was left and entered again. */
    function withOlderSelection() {
      const view = render(editable("<main>a</main>"));
      const frame = iframeOf(view.container);
      act(() => { window.dispatchEvent(new MessageEvent("message", { data: { type: "chimera-design-select", index: 0, tag: "main", rect, inlineStyle: "", text: null }, origin: "null", source: frame.contentWindow })); });
      act(() => view.rerender(editable("<main>b</main>", { x: 9, y: 9, key: 7 })));
      return { ...view, frame };
    }

    it("sends the point and does not restore the older selection", () => {
      const { container, frame } = withOlderSelection();
      const post = vi.spyOn(frame.contentWindow!, "postMessage");
      act(() => { frame.dispatchEvent(new Event("load")); });
      expect(post).toHaveBeenCalledWith({ type: "chimera-design-select-at", x: 9, y: 9 }, "*");
      expect(post).not.toHaveBeenCalledWith(expect.objectContaining({ type: "chimera-design-restore-selection" }), "*");
      expect(iframeOf(container)).toBe(frame);
    });

    it("on a point that lands on an element, the element the script reports is the selection", () => {
      const { frame } = withOlderSelection();
      act(() => { frame.dispatchEvent(new Event("load")); });
      act(() => { window.dispatchEvent(new MessageEvent("message", { data: { type: "chimera-design-select", index: 0, tag: "main", rect, inlineStyle: "", text: null }, origin: "null", source: frame.contentWindow })); });
      expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ index: 0, tag: "main" }));
    });

    it("on a point that lands on nothing, the script's null answer clears the selection and nothing is restored", () => {
      const { frame } = withOlderSelection();
      const post = vi.spyOn(frame.contentWindow!, "postMessage");
      act(() => { frame.dispatchEvent(new Event("load")); });
      act(() => { window.dispatchEvent(new MessageEvent("message", { data: { type: "chimera-design-select", index: null }, origin: "null", source: frame.contentWindow })); });
      expect(onSelect).toHaveBeenLastCalledWith(null);
      expect(post).not.toHaveBeenCalledWith(expect.objectContaining({ type: "chimera-design-restore-selection" }), "*");
    });
  });

  it("does not send a pending point to a frame that is not editing", () => {
    const { container } = renderIn(<svg><HtmlArtboardFrame html="<main>Hero</main>" tokens={tokens} width={320} height={200} title="Board" selectAt={{ x: 1, y: 2, key: 1 }} /></svg>);
    const frame = iframeOf(container);
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => { frame.dispatchEvent(new Event("load")); });
    expect(post).not.toHaveBeenCalled();
  });

  it("a board that goes inactive and is made active again is not taken for a self-navigated frame", () => {
    // Every step keeps the same root and the same frame element: a remount would reset the frame's
    // load bookkeeping and hide the very path this test is about.
    const board = (editing: boolean) => (
      <I18nProvider>
        <svg>
          <HtmlArtboardFrame html="<main>Hero</main>" tokens={tokens} width={320} height={200} title="Board" editing={editing} onElementSelect={() => undefined} />
        </svg>
      </I18nProvider>
    );
    const { container, rerender } = render(board(true));
    const frame = iframeOf(container);
    act(() => { frame.dispatchEvent(new Event("load")); });
    act(() => rerender(board(false)));
    act(() => { frame.dispatchEvent(new Event("load")); });
    act(() => rerender(board(true)));
    act(() => { frame.dispatchEvent(new Event("load")); });
    expect(iframeOf(container)).toBe(frame);
    expect(screen.queryByRole("status")).toBeNull();
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("takes the iframe out of the tab order and keeps its title for assistive technology", () => {
    const { container } = renderIn(frame("<main>Hero</main>", "Hero board"));
    const iframe = iframeOf(container);
    expect(iframe.getAttribute("tabindex")).toBe("-1");
    expect(iframe.getAttribute("title")).toBe("Hero board");
    expect(iframe.hasAttribute("aria-hidden")).toBe(false);
  });
});

describe("HtmlArtboardFrame navigation and selection", () => {
  const scripted = '<main>Hero</main><script type="text/x-chimera">class Component { renderIn(state) {} }</script>';

  it("replaces a frame that loaded a second time with its script-free twin and says why", () => {
    const { container } = renderIn(frame(scripted));
    const first = iframeOf(container);
    expect(first.getAttribute("srcdoc")).toContain("window.__chimeraRegister");
    act(() => { first.dispatchEvent(new Event("load")); });
    expect(screen.queryByRole("status")).toBeNull();
    // A second load of the same document is the frame navigating itself.
    act(() => { first.dispatchEvent(new Event("load")); });
    const after = iframeOf(container);
    expect(after.getAttribute("sandbox")).toBe("");
    expect(after.getAttribute("srcdoc")).not.toContain("window.__chimeraRegister");
    expect(after.getAttribute("srcdoc")).toContain("script-src 'none'");
    expect(screen.getByRole("status")).toHaveTextContent("The artboard's script tried to leave its frame");
  });

  it("does not treat a frame without scripts as navigated, whatever it loads", () => {
    const { container } = renderIn(frame("<main>Hero</main>"));
    const plain = iframeOf(container);
    act(() => { plain.dispatchEvent(new Event("load")); });
    act(() => { plain.dispatchEvent(new Event("load")); });
    expect(screen.queryByRole("status")).toBeNull();
    expect(iframeOf(container).getAttribute("srcdoc")).toContain("<main>Hero</main>");
  });

  it("accepts a selection only for an integer index below the stamped element count", () => {
    const onElementSelect = vi.fn();
    const onSelectionRejected = vi.fn();
    // Start tags: main, p, p. The stamped ordinals are 0, 1 and 2, so 3 is the first invalid index.
    const { container } = renderIn(
      <svg>
        <HtmlArtboardFrame html="<main><p>One</p><p>Two</p></main>" tokens={tokens} width={320} height={200} title="Board" editing onElementSelect={onElementSelect} onSelectionRejected={onSelectionRejected} />
      </svg>,
    );
    const source = iframeOf(container).contentWindow;
    const send = (index: unknown) => window.dispatchEvent(new MessageEvent("message", {
      data: { type: "chimera-design-select", index, tag: "p", rect: { x: 0, y: 0, width: 1, height: 1 }, inlineStyle: "", text: "Two" },
      origin: "null",
      source,
    }));
    send(2);
    expect(onElementSelect).toHaveBeenCalledTimes(1);
    send(3);
    send(-1);
    send(1.5);
    expect(onElementSelect).toHaveBeenCalledTimes(1);
    expect(onSelectionRejected).toHaveBeenCalledTimes(3);
  });
});
