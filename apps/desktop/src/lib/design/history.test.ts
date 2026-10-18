import { describe, expect, it } from "vitest";

import {
  COALESCE_MS,
  afterRedo,
  afterUndo,
  canRedo,
  canUndo,
  emptyHistory,
  peekRedo,
  peekUndo,
  record,
  type History,
} from "@/lib/design/history";
import type { DesignDocument, DesignOperation } from "@/lib/design/model";
import { applyOps } from "@/lib/design/ops";
import { sampleDocument } from "@/test/design-fixture";

/** A tiny editor: the same loop the workspace runs, without React. */
function editor(start: DesignDocument) {
  let doc = start;
  let history: History = emptyHistory();
  return {
    get doc() {
      return doc;
    },
    get history() {
      return history;
    },
    edit(ops: DesignOperation[], at: number, coalesceKey?: string) {
      const r = applyOps(doc, ops);
      doc = r.document;
      history = record(history, { forward: ops, inverse: r.inverse, label: "x", coalesceKey, at });
    },
    undo() {
      const entry = peekUndo(history);
      if (!entry) return;
      const r = applyOps(doc, entry.inverse);
      doc = r.document;
      history = afterUndo(history, r.inverse);
    },
    redo() {
      const entry = peekRedo(history);
      if (!entry) return;
      const r = applyOps(doc, entry.inverse);
      doc = r.document;
      history = afterRedo(history, r.inverse);
    },
  };
}

const move = (x: number): DesignOperation[] => [{ type: "node.move", nodeId: "box", frame: { x, y: 300, width: 200, height: 100 } }];

describe("history", () => {
  it("undoes and redoes in order", () => {
    const e = editor(sampleDocument());
    e.edit(move(1), 0);
    e.edit([{ type: "node.update", nodeId: "title", patch: { text: "Two" } }], 5_000);
    e.undo();
    expect(e.doc.nodes.title).toMatchObject({ text: "Hello" });
    expect(e.doc.nodes.box.frame.x).toBe(1);
    e.undo();
    expect(e.doc.nodes.box.frame.x).toBe(100);
    expect(canUndo(e.history)).toBe(false);
    e.redo();
    e.redo();
    expect(e.doc.nodes.box.frame.x).toBe(1);
    expect(e.doc.nodes.title).toMatchObject({ text: "Two" });
    expect(canRedo(e.history)).toBe(false);
  });

  it("coalesces a drag (same key, close in time) into ONE step", () => {
    const e = editor(sampleDocument());
    for (let i = 1; i <= 5; i++) e.edit(move(100 + i * 10), i * 50, "drag:box");
    expect(e.history.past).toHaveLength(1);
    e.undo();
    expect(e.doc.nodes.box.frame.x).toBe(100);
    e.redo();
    expect(e.doc.nodes.box.frame.x).toBe(150);
  });

  it("does not coalesce across different keys or after the window", () => {
    const e = editor(sampleDocument());
    e.edit(move(110), 0, "nudge:box");
    e.edit(move(120), COALESCE_MS + 1, "nudge:box");
    e.edit(move(130), COALESCE_MS + 2, "nudge:other");
    e.edit(move(140), COALESCE_MS + 3);
    expect(e.history.past).toHaveLength(4);
  });

  it("drops the redo stack on a new edit", () => {
    const e = editor(sampleDocument());
    e.edit(move(1), 0);
    e.undo();
    expect(canRedo(e.history)).toBe(true);
    e.edit(move(2), 10);
    expect(canRedo(e.history)).toBe(false);
  });

  it("ignores an empty batch", () => {
    expect(record(emptyHistory(), { forward: [], inverse: [], label: "x", at: 0 }).past).toHaveLength(0);
  });
});
