import { describe, expect, it } from "vitest";

import { defaultLayout } from "@/lib/layout/model";

/**
 * The right panel's starting state, for someone who has not arranged it. The environment, context and
 * changes cards are open; the machine readings (CPU, memory, VRAM) start folded, so the panel is not
 * spent on numbers nobody asked for. A person who opens them keeps them open: this is the start only.
 */
describe("the right panel's starting state", () => {
  const panels = defaultLayout().panels;

  it("starts the machine readings folded", () => {
    expect(panels["activity.machine"].mode).toBe("minimized");
  });

  it("starts the environment, context and changes cards open", () => {
    expect(panels["conversation.environment"].mode).toBe("open");
    expect(panels["conversation.context"].mode).toBe("open");
    expect(panels["conversation.changes"].mode).toBe("open");
  });
});
