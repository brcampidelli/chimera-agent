import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { modeForApproval, modeTurn, type PermissionMode } from "@/lib/permission-modes";

/**
 * The table the server holds too (`chimera/api/posture.py`, `mode_turn`). Both suites read the same
 * fixture, so the Code screen and the server cannot give a mode two different postures.
 */
const FIXTURE = join(__dirname, "../../../../tests/fixtures/permission_modes.json");

interface Row {
  mode: Exclude<PermissionMode, "bypass">;
  base: { reach: "read_only" | "workspace" | "workspace_shell"; approval: "always" | "suspicious" | "never" };
  posture: { reach: string; approval: string; commands: string };
  planGate: boolean;
}

const rows = (JSON.parse(readFileSync(FIXTURE, "utf-8")) as { rows: Row[] }).rows;

describe("the permission-mode table matches the server's", () => {
  it.each(rows)("$mode over $base.reach / $base.approval", (row) => {
    const turn = modeTurn(row.mode, row.base);
    expect(turn.posture).toEqual(row.posture);
    expect(turn.planGate).toBe(row.planGate);
  });
});

describe("the default each Settings approval reads as", () => {
  it("always reads as Manual", () => {
    expect(modeForApproval("always")).toBe("manual");
  });

  it("suspicious reads as Auto", () => {
    expect(modeForApproval("suspicious")).toBe("auto");
  });

  it("never reads as Don't ask, with the same posture a configured never always had", () => {
    expect(modeForApproval("never")).toBe("dont_ask");
    expect(modeTurn("dont_ask", { reach: "workspace_shell", approval: "always" }).posture).toEqual({
      reach: "workspace_shell",
      approval: "never",
      commands: "granted",
    });
  });
});
