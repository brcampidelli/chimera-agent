import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { artboardSrcDoc, HTML_ARTBOARD_SELECTION_SCRIPT } from "@/lib/design/htmlArtboard";
import type { DesignTokens } from "@/lib/design/model";

const tokens: DesignTokens = { colors: {}, typography: {}, spacing: {}, radii: {} };
const SOURCE = "<main><p>Hello</p><section><b>x</b></section><table><tr><td>1</td></tr></table></main>";

/**
 * Runs the selection script that ships in the iframe's srcdoc, as the browser would: the script is
 * read back out of the document the editor built and evaluated over the annotated markup. `parent`
 * is the top window in jsdom, so the messages it posts are the ones the editor would receive.
 */
function mountEditable(html: string): void {
  const parsed = new DOMParser().parseFromString(artboardSrcDoc(html, tokens, true), "text/html");
  const script = parsed.head.querySelector("script")?.textContent ?? "";
  expect(script).toBe(HTML_ARTBOARD_SELECTION_SCRIPT);
  document.body.replaceChildren(...Array.from(parsed.body.childNodes));
  window.eval(script);
}

function click(selector: string): void {
  const target = document.querySelector(selector);
  if (!target) throw new Error(`no element for ${selector}`);
  target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

describe("the selection script in an editable artboard", () => {
  const posted = vi.fn();

  beforeAll(() => {
    mountEditable(SOURCE);
  });

  beforeEach(() => {
    // Re-created per test: the config restores spies between tests, so the spy must be set here.
    posted.mockClear();
    vi.spyOn(window, "postMessage").mockImplementation((message: unknown) => {
      posted(message);
    });
    document.body.replaceChildren(...Array.from(new DOMParser().parseFromString(artboardSrcDoc(SOURCE, tokens, true), "text/html").body.childNodes));
  });

  it("sends the ordinal of the clicked element, with its tag and inline style", () => {
    document.body.querySelector("p")?.setAttribute("style", "color: red");
    click('[data-chimera-i="1"]');
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ type: "chimera-design-select", index: 1, tag: "p", inlineStyle: "color: red", text: "Hello" }));
  });

  it("sends the ordinal of the nearest stamped ancestor for an element the browser created", () => {
    // The source has no <tbody>; the browser inserted one. It carries no stamp, so its table answers.
    click("tbody");
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ index: 4, tag: "table" }));
  });

  it("restores a selected ordinal after the markup document is rebuilt", () => {
    click('[data-chimera-i="1"]');
    document.body.replaceChildren(...Array.from(new DOMParser().parseFromString(artboardSrcDoc(SOURCE, tokens, true), "text/html").body.childNodes));
    window.dispatchEvent(new MessageEvent("message", {
      source: window,
      data: { type: "chimera-design-restore-selection", index: 1 },
    }));
    expect(document.querySelector('[data-chimera-i="1"]')?.classList.contains("chimera-design-selected")).toBe(true);
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ type: "chimera-design-select", index: 1 }));
  });

  it("sends null when nothing stamped contains the click, and when Escape clears the selection", () => {
    click("body");
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ type: "chimera-design-select", index: null }));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ index: null }));
  });
});

describe("selecting the element under a point sent by the editor", () => {
  const posted = vi.fn();
  const elementFromPoint = vi.fn();

  beforeEach(() => {
    posted.mockClear();
    elementFromPoint.mockReset();
    vi.spyOn(window, "postMessage").mockImplementation((message: unknown) => {
      posted(message);
    });
    Object.defineProperty(document, "elementFromPoint", { value: elementFromPoint, configurable: true });
    document.body.replaceChildren(...Array.from(new DOMParser().parseFromString(artboardSrcDoc(SOURCE, tokens, true), "text/html").body.childNodes));
  });

  afterEach(() => {
    Reflect.deleteProperty(document, "elementFromPoint");
  });

  function sendFromParent(data: unknown, source: unknown = window): void {
    window.dispatchEvent(new MessageEvent("message", { data, source: source as MessageEventSource }));
  }

  it("selects the stamped element under the point", () => {
    // The point lands on the <p> itself (ordinal 1).
    elementFromPoint.mockImplementation(() => document.querySelector("p"));
    sendFromParent({ type: "chimera-design-select-at", x: 12, y: 34 });
    expect(elementFromPoint).toHaveBeenCalledWith(12, 34);
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ type: "chimera-design-select", index: 1, tag: "p" }));
  });

  it("answers a point that lands on nothing stamped with no selection, so an older one is cleared", () => {
    click('[data-chimera-i="1"]');
    expect(document.querySelector(".chimera-design-selected")).not.toBeNull();
    elementFromPoint.mockImplementation(() => document.body);
    sendFromParent({ type: "chimera-design-select-at", x: 0, y: 0 });
    expect(posted).toHaveBeenLastCalledWith(expect.objectContaining({ type: "chimera-design-select", index: null }));
    expect(document.querySelector(".chimera-design-selected")).toBeNull();
  });

  it("ignores a message from another source", () => {
    sendFromParent({ type: "chimera-design-select-at", x: 12, y: 34 }, null);
    expect(elementFromPoint).not.toHaveBeenCalled();
    expect(posted).not.toHaveBeenCalled();
  });

  it.each([
    ["a string x", { x: "12", y: 34 }],
    ["a missing y", { x: 12 }],
    ["an infinite x", { x: Infinity, y: 34 }],
  ])("ignores %s", (_label, overrides) => {
    sendFromParent({ type: "chimera-design-select-at", ...overrides });
    expect(elementFromPoint).not.toHaveBeenCalled();
  });
});
