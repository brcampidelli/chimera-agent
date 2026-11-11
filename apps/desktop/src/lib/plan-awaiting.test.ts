import { describe, expect, it } from "vitest";

import { clearIfStillAsked } from "@/lib/plan-awaiting";
import type { CodeApprovalEvent } from "@/lib/api";

const asked = (id: string): CodeApprovalEvent => ({ id, action: "1. Ler", reason: "", asked_at: 0, wait_seconds: 300 });

describe("clearIfStillAsked", () => {
  it("clears the card when it still holds the answered question", () => {
    expect(clearIfStillAsked(asked("q-1"), "q-1")).toBeNull();
  });

  it("keeps a newer question that replaced the answered one", () => {
    const newer = asked("q-2");
    expect(clearIfStillAsked(newer, "q-1")).toBe(newer);
  });

  it("keeps nothing when nothing was asked", () => {
    expect(clearIfStillAsked(null, "q-1")).toBeNull();
  });
});
