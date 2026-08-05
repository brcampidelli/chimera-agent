import { vi } from "vitest";
import type { CodeTurnDone, CodeTurnHandlers, PostureFacts, RoleModels } from "@/lib/api";
import type { AttemptReceipt, FsFile, FsNode, FsTree, GitStatus, RunReceipt } from "@/lib/types";

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
    streamCodeTurn: vi.fn(),
    deleteCodeSession: vi.fn(),
    getPostureFacts: vi.fn(),
    getRoleModels: vi.fn(),
  };
}

/** The posture facts the server would report for an ordinary local setup. */
export function postureFacts(over: Partial<PostureFacts> = {}): PostureFacts {
  return {
    writes: "workspace",
    workspace: "/repo",
    shell: "none",
    pauses: "tainted",
    fell_back_to_host: false,
    ...over,
  };
}

/** A scripted coding turn: drive the handlers in the order the real SSE stream delivers them.
 *
 *  Kept here rather than in one test file because the conversation is now part of the Code screen,
 *  so every Code test renders it — and a test that only mocks what IT uses will hit an undefined
 *  `streamCodeTurn` the moment someone types into the composer. */
/** The models a profile resolves to, as the server would report them. */
export function roleModels(over: Partial<RoleModels> = {}): RoleModels {
  return {
    explore: "vendor/weak",
    plan: "vendor/top",
    edit: "vendor/mid",
    review: "vendor/top",
    fuse_plan: true,
    fuse_review: false,
    ...over,
  };
}

export function scriptTurn(
  script: {
    session?: string;
    tokens?: string[];
    tools?: { name: string; arguments: Record<string, string>; ok: boolean; observation: string }[];
    edits?: { path: string; patch: string }[];
    done?: Partial<CodeTurnDone>;
    error?: boolean;
  } = {},
) {
  return async (_req: unknown, h: CodeTurnHandlers) => {
    h.onSession?.(script.session ?? "s1");
    for (const token of script.tokens ?? []) h.onToken?.(token);
    for (const tool of script.tools ?? []) h.onTool?.(tool);
    for (const edit of script.edits ?? []) h.onEdit?.(edit.path, edit.patch);
    if (script.error) {
      h.onError?.("boom");
      return;
    }
    h.onDone?.({
      answer: "done",
      steps: 1,
      stopped_reason: "final",
      tool_names: [],
      model: "vendor/model",
      prompt_tokens: 0,
      completion_tokens: 0,
      usd: null,
      context_peak_tokens: 0,
      route_meta: null,
      ...script.done,
    });
  };
}

// --- Fixture builders (shapes mirror the generated OpenAPI types) ---

export function emptyTree(): FsTree {
  return { workspace: "/repo", path: "", entries: [], capped: false };
}

/** One file leaf in the lazy tree (the viewer opens whatever leaf you click). */
export function fsNode(over: Partial<FsNode> = {}): FsNode {
  return { is_dir: false, name: "app.py", path: "src/app.py", ...over };
}

/** A root tree listing the given nodes — defaults to a single clickable `src/app.py`. */
export function treeWith(entries: FsNode[] = [fsNode()]): FsTree {
  return { workspace: "/repo", path: "", entries, capped: false };
}

/** A file read: clean + whole text by default. `truncated: true` (clipped at the read cap) or a
 *  non-empty `note` (binary/non-text) are the two honesty cases the viewer must refuse to edit. */
export function fsFile(over: Partial<FsFile> = {}): FsFile {
  return { path: "src/app.py", content: "print('hi')\n", note: "", truncated: false, ...over };
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
    evidence: "verifier",
    diff_productive: true,
    side_effects: [],
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
