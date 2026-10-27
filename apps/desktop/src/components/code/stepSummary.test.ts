import { describe, expect, it } from "vitest";

import { diffStat, LARGE_DIFF_LINES, MAX_OUTCOMES, summarizeSteps } from "@/components/code/stepSummary";
import type { CodeToolEvent } from "@/lib/api";

const ok = (name: string, args: Record<string, string> = {}, duration = 1): CodeToolEvent =>
  ({ name, arguments: args, ok: true, observation: "", duration_seconds: duration });
const failed = (name: string, args: Record<string, string> = {}): CodeToolEvent =>
  ({ name, arguments: args, ok: false, observation: "boom" });

describe("summarizeSteps", () => {
  it("counts calls, separates the failures, and names the tool that failed most", () => {
    const s = summarizeSteps([ok("read_file", { path: "a" }), failed("run_tests"), failed("run_tests"), failed("lint")]);
    expect(s.count).toBe(4);
    expect(s.succeeded).toBe(1);
    expect(s.failed).toBe(3);
    expect(s.failedTool).toBe("run_tests");
  });

  it("adds the measured durations of the calls", () => {
    expect(summarizeSteps([ok("a", {}, 1.5), ok("b", {}, 2.5)]).elapsed).toBe(4);
  });

  it("lists the files a turn wrote, edited or read, once each", () => {
    const s = summarizeSteps(
      [ok("write_file", { path: "site/index.html" }), ok("read_file", { path: "site/index.html" }), ok("read_file", { path: "README.md" })],
      [{ path: "src/app.py" }],
    );
    expect(s.outcomes.filter((o) => o.kind === "file").map((o) => o.label)).toEqual([
      "site/index.html",
      "src/app.py",
      "README.md",
    ]);
  });

  it("drops the calls that failed when it lists what happened", () => {
    const s = summarizeSteps([failed("write_file", { path: "gone.py" })]);
    expect(s.outcomes.some((o) => o.label === "gone.py")).toBe(false);
  });

  it("reports a server, its port, and generated media", () => {
    const s = summarizeSteps([ok("start_server", { port: "8080" }), ok("generate_image", { out: "hero.png" })]);
    expect(s.outcomes).toEqual(
      expect.arrayContaining([
        { kind: "server", label: ":8080" },
        { kind: "media", label: "hero.png" },
      ]),
    );
  });

  it("says a verification ran, pass or fail, with its command", () => {
    const s = summarizeSteps([failed("run_command", { command: "pytest -q" })]);
    expect(s.outcomes[s.outcomes.length - 1]).toEqual({ kind: "verify", label: "pytest -q" });
  });

  it("does not count a shell command that is not a check as a verification", () => {
    const s = summarizeSteps([ok("run_command", { command: "ls -la" })]);
    expect(s.outcomes[s.outcomes.length - 1]).toEqual({ kind: "verify", label: null });
  });

  it("always ends with the verification slot, even when none ran", () => {
    const s = summarizeSteps([ok("write_file", { path: "a.ts" })]);
    expect(s.outcomes[s.outcomes.length - 1]).toEqual({ kind: "verify", label: null });
  });

  it("never shows more than four outcomes", () => {
    const calls = [1, 2, 3, 4, 5].map((n) => ok("write_file", { path: `f${n}.ts` }));
    const s = summarizeSteps(calls, [{ path: "e.ts" }]);
    expect(s.outcomes.length).toBeLessThanOrEqual(MAX_OUTCOMES);
    expect(s.outcomes[s.outcomes.length - 1]?.kind).toBe("verify");
  });
});

describe("diffStat", () => {
  it("counts additions and deletions and ignores the file headers", () => {
    expect(diffStat("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n+more")).toEqual({ additions: 2, deletions: 1 });
  });

  it("marks a diff as large past the folding threshold", () => {
    const body = Array.from({ length: LARGE_DIFF_LINES + 1 }, (_, i) => `+line ${i}`).join("\n");
    expect(diffStat(body).additions).toBeGreaterThan(LARGE_DIFF_LINES);
  });
});
