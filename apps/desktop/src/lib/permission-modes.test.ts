import { describe, expect, it } from "vitest";

import { bypassTurn, modeForApproval, modeTurn, PERMISSION_MODES } from "@/lib/permission-modes";

const BASE = { reach: "workspace", approval: "suspicious" } as const;

describe("the permission modes map onto the posture axes", () => {
  it("lists the six modes in the order the selector shows them", () => {
    expect(PERMISSION_MODES).toEqual(["manual", "accept_edits", "plan", "auto", "dont_ask", "bypass"]);
  });

  it("Manual is the configured reach with approval always", () => {
    expect(modeTurn("manual", BASE)).toEqual({
      posture: { reach: "workspace", approval: "always", commands: "granted" },
      planGate: false,
    });
  });

  it("Accept edits edits without asking and asks before every command", () => {
    expect(modeTurn("accept_edits", BASE)).toEqual({
      posture: { reach: "workspace_shell", approval: "never", commands: "asks" },
      planGate: false,
    });
  });

  it("Accept edits never raises a read-only owner's reach", () => {
    expect(modeTurn("accept_edits", { reach: "read_only", approval: "always" }).posture.reach).toBe("read_only");
  });

  it("Plan is never wider than Auto: a configured never reads as suspicious", () => {
    expect(modeTurn("plan", { reach: "workspace", approval: "never" }).posture.approval).toBe("suspicious");
  });

  it("Plan forces the plan gate and keeps the configured posture", () => {
    expect(modeTurn("plan", { reach: "workspace_shell", approval: "always" })).toEqual({
      posture: { reach: "workspace_shell", approval: "always", commands: "granted" },
      planGate: true,
    });
  });

  it("Auto pauses only when the run read something suspicious", () => {
    expect(modeTurn("auto", { reach: "workspace", approval: "always" })).toEqual({
      posture: { reach: "workspace", approval: "suspicious", commands: "granted" },
      planGate: false,
    });
  });

  it("Bypass is the configured posture, the switch answers the questions", () => {
    expect(bypassTurn({ reach: "workspace", approval: "never" })).toEqual({
      posture: { reach: "workspace", approval: "never", commands: "granted" },
      planGate: false,
    });
  });

  it("the Settings default reads the configured approval as its mode", () => {
    expect(modeForApproval("always")).toBe("manual");
    expect(modeForApproval("suspicious")).toBe("auto");
    expect(modeForApproval("never")).toBe("dont_ask");
  });

  it("only Plan turns the plan gate on, whatever the base", () => {
    for (const mode of ["manual", "accept_edits", "auto"] as const) {
      expect(modeTurn(mode, BASE).planGate).toBe(false);
    }
    expect(modeTurn("plan", BASE).planGate).toBe(true);
  });
});
