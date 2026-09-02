import { describe, expect, it } from "vitest";

import { DEFAULT_MODE, MODES, isMode, readMode, usesVerify } from "./modes";

/**
 * The mode is in the address, so it has to survive the round trip.
 *
 * Pure, and separate from the console's own tests for the reason `Work.tsx` writes down about its
 * tabs: the union type and the list are two declarations of one fact that the typechecker cannot
 * relate, so a mode added to one and not the other compiles and then falls back to the default in
 * silence — the address bar saying one thing while the screen shows another.
 */
describe("the mode in the address", () => {
  it.each(MODES)("reads %s back out of a hash", (mode) => {
    expect(readMode(`#/work?tab=task&mode=${mode}`)).toBe(mode);
  });

  it("falls back for a name that is not a mode", () => {
    // A hand-edited or stale URL must land somewhere real rather than rendering nothing.
    expect(readMode("#/work?mode=lifecycl")).toBe(DEFAULT_MODE);
    expect(readMode("#/work?mode=")).toBe(DEFAULT_MODE);
    expect(readMode("#/work")).toBe(DEFAULT_MODE);
    expect(readMode("")).toBe(DEFAULT_MODE);
  });

  it("does not treat a tab name as a mode name", () => {
    // The two live in the same query string. Reading the wrong key would put the console into a
    // mode nobody asked for, on a URL that looks correct.
    expect(readMode("#/work?tab=git")).toBe(DEFAULT_MODE);
    expect(isMode("git")).toBe(false);
  });
});

describe("which modes send a check", () => {
  it("is every mode that can run one", () => {
    // Named individually rather than counted: "three of four" would still pass if the wrong three
    // were listed, and the one that must be out is out for a reason a count cannot express.
    expect(MODES.filter(usesVerify)).toEqual(["single", "lifecycle", "crew"]);
  });

  it("excludes the one whose workers cannot run anything", () => {
    // The hierarchy mounts its workers tool-free: they read and answer and never touch a file, so
    // there is nothing for a shell command to check. Offering the field would be offering to send
    // something nowhere.
    expect(usesVerify("hierarchy")).toBe(false);
  });
});
