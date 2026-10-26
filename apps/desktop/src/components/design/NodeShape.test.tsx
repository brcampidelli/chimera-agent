import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NodeShape } from "@/components/design/NodeShape";
import type { DesignNode } from "@/lib/design/model";
import { sampleDocument } from "@/test/design-fixture";

describe("NodeShape classes", () => {
  it("keeps the colour token of a text node when the canvas adds a class to it", () => {
    const doc = sampleDocument();
    const node: DesignNode = { ...doc.nodes.title, colorToken: undefined } as DesignNode;
    const { container } = render(
      <svg>
        <NodeShape doc={doc} node={node} frame={node.frame} className="design-node-enter" />
      </svg>,
    );
    const text = container.querySelector("text");
    expect(text).not.toBeNull();
    expect(text?.getAttribute("class")).toContain("fill-foreground");
    expect(text?.getAttribute("class")).toContain("design-node-enter");
  });

  it("keeps the outline colour token of a shape when the canvas adds a class to it", () => {
    const doc = sampleDocument();
    const node = doc.nodes.box;
    const { container } = render(
      <svg>
        <NodeShape doc={doc} node={node} frame={node.frame} className="design-node-enter" />
      </svg>,
    );
    const shape = container.querySelector("[data-node-id='box']");
    expect(shape?.getAttribute("class") ?? "").toContain("design-node-enter");
  });
});
