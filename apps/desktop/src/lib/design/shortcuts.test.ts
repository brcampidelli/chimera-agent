import { describe, expect, it } from "vitest";

import { DESIGN_SHORTCUTS, displayKeys, matchDesignShortcut, type KeyLike } from "@/lib/design/shortcuts";
import { COMMANDS, TOOLS, TOOL_SHORTCUT } from "@/lib/design/tools";

function key(k: string, mods: Partial<KeyLike> = {}): KeyLike {
  return { key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods };
}

describe("design shortcuts", () => {
  it("matches modifiers exactly, so Shift+N and N are different shortcuts", () => {
    expect(matchDesignShortcut(key("n"))).toBe("selectNext");
    expect(matchDesignShortcut(key("N", { shiftKey: true }))).toBe("selectPrevious");
    expect(matchDesignShortcut(key("z", { ctrlKey: true }))).toBe("undo");
    expect(matchDesignShortcut(key("Z", { ctrlKey: true, shiftKey: true }))).toBe("redo");
    expect(matchDesignShortcut(key("z", { metaKey: true }))).toBe("undo");
    expect(matchDesignShortcut(key("z"))).toBeUndefined();
    expect(matchDesignShortcut(key("d", { ctrlKey: true, altKey: true }))).toBeUndefined();
  });

  it("treats Shift as part of characters like ? and +", () => {
    expect(matchDesignShortcut(key("?", { shiftKey: true }))).toBe("help");
    expect(matchDesignShortcut(key("+", { shiftKey: true }))).toBe("zoomIn");
    expect(matchDesignShortcut(key("ArrowUp", { shiftKey: true }))).toBe("moveFar");
    expect(matchDesignShortcut(key("ArrowUp"))).toBe("move");
  });

  it("gives every chord to exactly one shortcut", () => {
    const chords = DESIGN_SHORTCUTS.flatMap((s) => s.chords);
    expect(new Set(chords).size).toBe(chords.length);
  });

  it("documents every tool and every command the toolbar offers", () => {
    const ids = new Set(DESIGN_SHORTCUTS.map((s) => s.id));
    for (const tool of TOOLS) expect(ids.has(TOOL_SHORTCUT[tool])).toBe(true);
    for (const command of COMMANDS) expect(ids.has(command)).toBe(true);
  });

  it("writes every row's keys for the help sheet", () => {
    for (const row of DESIGN_SHORTCUTS) expect(displayKeys(row).length).toBeGreaterThan(0);
  });
});
