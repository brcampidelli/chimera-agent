import { describe, expect, it } from "vitest";

import { afterRedo, afterUndo, emptyHistory, peekRedo, peekUndo, record, recordAgentEdit } from "@/lib/design/history";
import { rebase } from "@/lib/design/sync";
import { sampleDocument } from "@/test/design-fixture";

describe("rebase: unsaved edits over a newer server copy", () => {
  it("replays edits that still apply, keeping the server's revision", () => {
    const remote = { ...sampleDocument(), revision: 7, title: "Renamed elsewhere" };
    const out = rebase(remote, [{ type: "node.move", nodeId: "box", frame: { x: 1, y: 2, width: 3, height: 4 } }]);
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.document.revision).toBe(7);
    expect(out.document.title).toBe("Renamed elsewhere");
    expect(out.document.nodes.box.frame).toEqual({ x: 1, y: 2, width: 3, height: 4 });
  });

  it("refuses the whole replay when one edit no longer applies", () => {
    const remote = sampleDocument();
    delete (remote.nodes as Record<string, unknown>).box;
    remote.pages[0].artboards[0].nodeIds = ["title", "dot"];
    const out = rebase(remote, [
      { type: "document.rename", title: "Mine" },
      { type: "node.move", nodeId: "box", frame: { x: 1, y: 2, width: 3, height: 4 } },
    ]);
    expect(out).toEqual({ ok: false, code: "nodeNotFound" });
  });
});

describe("history entries for edits made on the server", () => {
  it("restores `from` on undo and `to` on redo, and is never merged with a manual step", () => {
    let h = record(emptyHistory(), { forward: [{ type: "document.rename", title: "A" }], inverse: [{ type: "document.rename", title: "B" }], label: "x", coalesceKey: "k", at: 0 });
    h = recordAgentEdit(h, 3, 5, "design.did.agent", 10);
    expect(h.past).toHaveLength(2);
    expect(peekUndo(h)?.revisions).toEqual({ from: 3, to: 5 });

    h = afterUndo(h, []);
    expect(peekRedo(h)?.revisions).toEqual({ from: 5, to: 3 });
    h = afterRedo(h, []);
    expect(peekUndo(h)?.revisions).toEqual({ from: 3, to: 5 });
  });

  it("records nothing when the revision did not move", () => {
    expect(recordAgentEdit(emptyHistory(), 4, 4, "design.did.agent", 0).past).toHaveLength(0);
  });
});
