import { vi } from "vitest";
import type { AttemptReceipt, FsTree, GitStatus, RunReceipt } from "@/lib/types";

/** The `@/lib/api` surface the Code screen touches. Used as the `vi.mock` factory (via a dynamic
 *  import, so it survives the factory hoisting) — the network is never reached from a test. */
export function makeCodeApiMock() {
  return {
    cancelRun: vi.fn(),
    captureScreenshot: vi.fn(),
    getFsFile: vi.fn(),
    getFsTree: vi.fn(),
    getGitDiff: vi.fn(),
    getGitStatus: vi.fn(),
    getPlan: vi.fn(),
    getRuns: vi.fn(),
    gitCommit: vi.fn(),
    gitRevert: vi.fn(),
    saveFile: vi.fn(),
    streamExec: vi.fn(),
    streamRun: vi.fn(),
  };
}

// --- Fixture builders (shapes mirror the generated OpenAPI types) ---

export function emptyTree(): FsTree {
  return { workspace: "/repo", path: "", entries: [], capped: false };
}

export function gitStatus(over: Partial<GitStatus> = {}): GitStatus {
  return { is_repo: true, branch: "main", files: [], ...over };
}

export function attempt(over: Partial<AttemptReceipt> = {}): AttemptReceipt {
  return {
    index: 1,
    success: true,
    verified: true,
    reverted: false,
    diff_summary: "1 file changed",
    diffs: [{ path: "src/app.py", patch: "@@ -1 +1 @@\n-old\n+new", truncated: false }],
    feedback: "",
    verify_output: "",
    ...over,
  };
}

export function receipt(over: Partial<RunReceipt> = {}): RunReceipt {
  return {
    task: "make the test pass",
    answer: "done",
    success: true,
    paused: false,
    ts: "2026-07-16T12:00:00Z",
    verify_command: "pytest -q",
    attempts: [attempt()],
    ...over,
  };
}
