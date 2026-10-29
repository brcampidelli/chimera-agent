import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HtmlArtboardFrame } from "@/components/design/HtmlArtboardFrame";
import type { DesignTokens } from "@/lib/design/model";

// Counts how often the srcdoc is actually built. Spying on the module export through vi.mock is the
// only way to observe it: the component calls it from its own module, which a plain spy cannot reach.
const { srcDocBuilds } = vi.hoisted(() => ({ srcDocBuilds: vi.fn() }));
vi.mock("@/lib/design/htmlArtboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/design/htmlArtboard")>();
  return {
    ...actual,
    artboardSrcDoc: (html: string, tokens: DesignTokens) => {
      srcDocBuilds();
      return actual.artboardSrcDoc(html, tokens);
    },
  };
});

const tokens: DesignTokens = {
  colors: { ink: "#112233" },
  typography: {},
  spacing: {},
  radii: {},
};

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
    const { container, rerender } = render(frame("<main>Hero</main>"));
    const first = iframeOf(container);
    const srcDoc = first.getAttribute("srcdoc");
    expect(srcDocBuilds).toHaveBeenCalledTimes(1);

    rerender(frame("<main>Hero</main>"));
    expect(srcDocBuilds).toHaveBeenCalledTimes(1);
    expect(iframeOf(container).getAttribute("srcdoc")).toBe(srcDoc);
    // The same frame element survives: a reload would show up as a new node, not a changed attribute.
    expect(iframeOf(container)).toBe(first);
  });

  it("rebuilds the srcdoc when the markup changes", () => {
    const { container, rerender } = render(frame("<main>Hero</main>"));
    const before = iframeOf(container).getAttribute("srcdoc");
    rerender(frame("<main>Changed</main>"));
    expect(srcDocBuilds).toHaveBeenCalledTimes(2);
    expect(iframeOf(container).getAttribute("srcdoc")).not.toBe(before);
    expect(iframeOf(container).getAttribute("srcdoc")).toContain("<main>Changed</main>");
  });

  it("is a memoised component, so an unchanged board skips rendering", () => {
    // Re-render counts are not observable from outside React, so this asserts the wrapper that
    // makes the skip happen; the test above asserts that nothing reloads when it does.
    expect((HtmlArtboardFrame as unknown as { $$typeof?: symbol }).$$typeof).toBe(Symbol.for("react.memo"));
  });

  it("takes the iframe out of the tab order and keeps its title for assistive technology", () => {
    const { container } = render(frame("<main>Hero</main>", "Hero board"));
    const iframe = iframeOf(container);
    expect(iframe.getAttribute("tabindex")).toBe("-1");
    expect(iframe.getAttribute("title")).toBe("Hero board");
    expect(iframe.hasAttribute("aria-hidden")).toBe(false);
  });
});
