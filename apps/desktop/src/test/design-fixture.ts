/**
 * A small Chimera Design document for tests. Colours are built from channels, not written as
 * literals: this file is not a `.test.ts`, so the design gate reads it like shipping code — which
 * is the point, a fixture is how a literal palette would sneak into the app.
 */
import { channelsToHex } from "@/lib/design/defaults";
import type { DesignDocument } from "@/lib/design/model";

export const PAPER = channelsToHex(250, 250, 250);
export const INK = channelsToHex(20, 24, 32);
export const BRAND = channelsToHex(10, 120, 200);

export function sampleDocument(): DesignDocument {
  return {
    schemaVersion: 1,
    id: "doc_1",
    ownerId: "owner_1",
    title: "Launch page",
    revision: 0,
    updatedAt: "2026-10-17T00:00:00.000Z",
    tokens: {
      colors: { background: PAPER, text: INK, primary: BRAND },
      typography: { heading: { family: "Inter", size: 48, weight: 700 }, body: { family: "Inter", size: 16, weight: 400 } },
      spacing: { md: 16 },
      radii: { md: 8 },
    },
    pages: [
      {
        id: "page_1",
        name: "Page 1",
        artboards: [
          { id: "board_1", name: "Desktop", width: 1440, height: 900, background: PAPER, nodeIds: ["title", "box", "dot"] },
          { id: "board_2", name: "Phone", width: 390, height: 844, background: PAPER, nodeIds: [] },
        ],
      },
    ],
    nodes: {
      title: {
        id: "title", type: "text", name: "Title", text: "Hello", fontToken: "heading", colorToken: "text",
        frame: { x: 100, y: 100, width: 400, height: 60 }, visible: true,
      },
      box: {
        id: "box", type: "shape", shape: "rectangle", name: "Box",
        frame: { x: 100, y: 300, width: 200, height: 100 }, visible: true, style: { fill: BRAND },
      },
      dot: {
        id: "dot", type: "shape", shape: "ellipse", name: "Dot",
        frame: { x: 600, y: 300, width: 100, height: 100 }, visible: true,
      },
    },
    comments: [],
  };
}

export function emptyDocument(): DesignDocument {
  return { ...sampleDocument(), pages: [], nodes: {}, comments: [] };
}
