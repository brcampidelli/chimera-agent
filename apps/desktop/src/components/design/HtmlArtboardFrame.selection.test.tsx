import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HtmlArtboardFrame } from "@/components/design/HtmlArtboardFrame";
import type { DesignTokens } from "@/lib/design/model";
import { I18nProvider } from "@/lib/i18n";

const tokens: DesignTokens = { colors: {}, typography: {}, spacing: {}, radii: {} };

function mount(onElementSelect: () => void, onSelectionRejected: () => void): { iframe: HTMLIFrameElement; rerender: (html: string) => void } {
  // Three stamped elements (main, p, p): the ordinals 0 to 2 are the valid selections.
  const ui = (html: string) => (
    <I18nProvider>
      <svg>
        <HtmlArtboardFrame
          html={html}
          tokens={tokens}
          width={320}
          height={200}
          title="Board"
          editing
          onElementSelect={onElementSelect}
          onSelectionRejected={onSelectionRejected}
        />
      </svg>
    </I18nProvider>
  );
  const view = render(ui("<main><p>x</p><p>y</p></main>"));
  return { iframe: view.container.querySelector("iframe") as HTMLIFrameElement, rerender: (html) => view.rerender(ui(html)) };
}

function send(iframe: HTMLIFrameElement, data: unknown): void {
  window.dispatchEvent(new MessageEvent("message", { data, origin: "null", source: iframe.contentWindow }));
}

const rect = { x: 1, y: 2, width: 3, height: 4 };

describe("selection messages from an editable artboard", () => {
  it("passes an ordinal selection through with its index", () => {
    const select = vi.fn();
    const rejected = vi.fn();
    const { iframe } = mount(select, rejected);
    send(iframe, { type: "chimera-design-select", index: 2, tag: "p", rect, inlineStyle: "", text: "x" });
    expect(select).toHaveBeenLastCalledWith(expect.objectContaining({ index: 2, tag: "p" }));
    expect(rejected).not.toHaveBeenCalled();
  });

  it("restores an existing selection after the srcdoc loads again", () => {
    const select = vi.fn();
    const rejected = vi.fn();
    const { iframe, rerender } = mount(select, rejected);
    send(iframe, { type: "chimera-design-select", index: 2, tag: "p", rect, inlineStyle: "", text: "x" });
    rerender("<main><p>updated</p><p>y</p></main>");
    const post = vi.spyOn(iframe.contentWindow!, "postMessage");
    iframe.dispatchEvent(new Event("load"));
    expect(post).toHaveBeenCalledWith({ type: "chimera-design-restore-selection", index: 2 }, "*");
    expect(select).toHaveBeenLastCalledWith(expect.objectContaining({ index: 2 }));
    expect(rejected).not.toHaveBeenCalled();
  });

  it("clears selection after reload if that ordinal no longer exists", () => {
    const select = vi.fn();
    const rejected = vi.fn();
    const { iframe, rerender } = mount(select, rejected);
    send(iframe, { type: "chimera-design-select", index: 2, tag: "p", rect, inlineStyle: "", text: "x" });
    rerender("<main><p>only one child</p></main>");
    const post = vi.spyOn(iframe.contentWindow!, "postMessage");
    iframe.dispatchEvent(new Event("load"));
    expect(post).not.toHaveBeenCalled();
    expect(select).toHaveBeenLastCalledWith(null);
    expect(rejected).not.toHaveBeenCalled();
  });

  it("clears the selection on index null", () => {
    const select = vi.fn();
    const rejected = vi.fn();
    const { iframe } = mount(select, rejected);
    send(iframe, { type: "chimera-design-select", index: null });
    expect(select).toHaveBeenLastCalledWith(null);
    expect(rejected).not.toHaveBeenCalled();
  });

  it.each([
    ["a string index", { index: "2" }],
    ["a fractional index", { index: 1.5 }],
    ["a negative index", { index: -1 }],
    ["an oversized inline style", { index: 2, inlineStyle: "x".repeat(4097) }],
  ])("discards %s and says so instead of going silent", (_label, overrides) => {
    const select = vi.fn();
    const rejected = vi.fn();
    const { iframe } = mount(select, rejected);
    send(iframe, { type: "chimera-design-select", tag: "p", rect, inlineStyle: "", text: null, ...overrides });
    expect(select).not.toHaveBeenCalled();
    expect(rejected).toHaveBeenCalledTimes(1);
  });
});
