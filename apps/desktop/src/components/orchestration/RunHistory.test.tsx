import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunHistory } from "@/components/orchestration/RunHistory";
import { getOrchestrationFrames, getOrchestrationRuns } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getOrchestrationRuns: vi.fn(),
  getOrchestrationFrames: vi.fn(),
}));

/**
 * The runs on disk, and the way back into them.
 *
 * `getOrchestrationRuns` had zero callers — not in a component, not in a test — while the endpoint
 * behind it worked perfectly. What the screen could reach was one id in localStorage, so it
 * resumed the LAST run from the machine that started it, and everything before that sat intact and
 * unreachable. rc11 announced that a paid-for run survives the tab that started it; it did, and
 * nobody could get to it.
 */

function run(over: Record<string, unknown> = {}) {
  return {
    run_id: "abc123",
    task: "compare the release notes",
    kind: "hierarchy",
    started: 1_787_424_275,
    frames: 9,
    done: true,
    orphaned: false,
    ...over,
  };
}

describe("RunHistory", () => {
  beforeEach(() => {
    vi.mocked(getOrchestrationRuns).mockReset();
    vi.mocked(getOrchestrationFrames).mockReset();
  });

  it("asks the server what is on disk", async () => {
    vi.mocked(getOrchestrationRuns).mockResolvedValue({ runs: [run()] });
    renderWithProviders(<RunHistory onOpen={() => {}} />);

    expect(await screen.findByText("compare the release notes")).toBeTruthy();
    expect(getOrchestrationRuns).toHaveBeenCalled();
  });

  it("replays a past run instead of running it again", async () => {
    const frames = [{ seq: 1, kind: "run", task_id: "", text: "", data: {} }];
    vi.mocked(getOrchestrationRuns).mockResolvedValue({ runs: [run()] });
    vi.mocked(getOrchestrationFrames).mockResolvedValue({ run_id: "abc123", frames, seq: 1 });
    const onOpen = vi.fn();
    renderWithProviders(<RunHistory onOpen={onOpen} />);

    await userEvent.click(await screen.findByRole("button", { name: /compare the release notes/ }));

    await waitFor(() =>
      expect(onOpen).toHaveBeenCalledWith({ runId: "abc123", kind: "hierarchy", frames }),
    );
  });

  it("tells an abandoned run apart from one that is still working", async () => {
    // `done: false` covered both, and one measured run sat in the second state for twenty-two
    // minutes looking exactly like the first.
    vi.mocked(getOrchestrationRuns).mockResolvedValue({
      runs: [
        run({ run_id: "a", task: "still going", done: false, orphaned: false }),
        run({ run_id: "b", task: "process died", done: false, orphaned: true }),
      ],
    });
    renderWithProviders(<RunHistory onOpen={() => {}} />);

    const alive = await screen.findByRole("button", { name: /still going/ });
    const dead = screen.getByRole("button", { name: /process died/ });

    expect(alive.textContent).toContain("running");
    expect(dead.textContent).toContain("abandoned");
  });

  it("says so when there is nothing rather than showing an empty box", async () => {
    vi.mocked(getOrchestrationRuns).mockResolvedValue({ runs: [] });
    renderWithProviders(<RunHistory onOpen={() => {}} />);

    expect(await screen.findByText(/Nothing yet/)).toBeTruthy();
  });
});

/**
 * The class, not the instance.
 *
 * Two API helpers were written, typed, documented and never called. That is not a typo — it is the
 * shape this project keeps finding in itself, and the reason rc11's own audit existed: a capability
 * whose wire was never connected reads exactly like a capability that works.
 */
describe("every orchestration API helper has a caller", () => {
  const SRC = join(__dirname, "..", "..");

  function sources(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) sources(full, out);
      else if (/\.tsx?$/.test(entry) && !entry.endsWith(".d.ts")) out.push(full);
    }
    return out;
  }

  /** Code with the comments taken out.
   *
   * The rule this file learned the hard way, three times over: a guard that reads source has to
   * strip prose first. `DelegationSavings.tsx` opens by NAMING the helper it calls and explaining
   * why it had no caller — so deleting the actual call left the check green, satisfied by a
   * sentence describing the bug it was meant to catch. `form-buttons.test.ts` was wrong in exactly
   * the same way about `<form>` and `<button>`.
   */
  function code(source: string): string {
    return source
      .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, " ")
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  }

  it("finds no orchestration helper that only api.ts mentions", () => {
    const api = readFileSync(join(SRC, "lib", "api.ts"), "utf8");
    const exported = [...api.matchAll(/export const (\w*Orchestration\w*|getDelegations)\s*=/g)].map(
      (m) => m[1],
    );
    expect(exported.length).toBeGreaterThan(2); // the scan itself has to be finding things

    // Production sources only: not `api.ts` itself, not `*.test.*`, and not the `test/` helpers.
    //
    // Each exclusion was earned by this check failing to fail. First it counted test files, so it
    // saw `getDelegations` in its own regex above and passed no matter what. Then it counted
    // `src/test/code-api-mock.ts` — which is a helper, not a `.test.` file — so adding the endpoint
    // to the shared mock was enough to satisfy it, and deleting the real caller changed nothing.
    //
    // Which is also the point of the check: a helper reached only from a mock is reachable from no
    // screen at all, and that is precisely the state these were in.
    const TEST_HELPERS = `${join(SRC, "test")}${sep}`;
    const elsewhere = sources(SRC)
      .filter(
        (f) =>
          !f.endsWith(join("lib", "api.ts")) &&
          !/\.test\.tsx?$/.test(f) &&
          !f.startsWith(TEST_HELPERS),
      )
      .map((f) => code(readFileSync(f, "utf8")))
      .join("\n");

    const orphans = exported.filter((name) => !new RegExp(`\\b${name}\\b`).test(elsewhere));
    expect(orphans).toEqual([]);
  });
});
