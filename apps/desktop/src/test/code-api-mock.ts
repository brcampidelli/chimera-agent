import { vi } from "vitest";
import type {
  CodeTurnDone,
  CodeTurnHandlers,
  CodeVerified,
  PostureFacts,
  ProfileWorth,
  RoleModels,
  WorthReport,
} from "@/lib/api";
import type { AttemptReceipt, FsFile, FsNode, FsTree, GitStatus, RunReceipt } from "@/lib/types";

/** The `@/lib/api` surface the Code screen touches. Used as the `vi.mock` factory (via a dynamic
 *  import, so it survives the factory hoisting) — the network is never reached from a test. */
export function makeCodeApiMock() {
  return {
    cancelRun: vi.fn(),
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
    revertCodeTurn: vi.fn(),
    uploadAttachment: vi.fn(),
    transcribe: vi.fn(),
    // Resolved by default so a suite that is not about vision never renders the caveat: the warning
    // must appear because a model cannot see, not because a fixture forgot to say it could.
    getVisionSupport: vi.fn(async () => ({ model: "vendor/model", support: "yes" })),
    // Available by default so a suite that is not about dictation never renders the caveat.
    getDictationSupport: vi.fn(async () => ({ support: "yes", how: "local" })),
    deleteCodeSession: vi.fn(),
    // Resolved by default with an unset posture: the Code screen reads the deployment's posture
    // instead of hardcoding one now, and an unset posture is what every install starts with — so
    // the fallbacks (edit the workspace, stop and ask if the run read something untrusted) are what
    // these suites exercise, exactly as before the setting existed.
    getConfig: vi.fn(async () => ({
      autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
    })),
    getPostureFacts: vi.fn(),
    getRoleModels: vi.fn(),
    getWorth: vi.fn(),
    // Git and the cost table moved to the Work screen, which also mounts Runs and Agents on its
    // other tabs — and a tab that is not shown still renders its screen's shared queries. Mounting
    // a bigger host means mocking a bigger surface; leaving these out fails as "No export is
    // defined on the mock", which reads like a broken test rather than a missing stub.
    // Resolved by default, unlike its neighbours, and that is deliberate: every Code test now
    // mounts the session sidebar, so a bare `vi.fn()` would resolve undefined and react-query would
    // throw "Query data cannot be undefined" in a dozen suites that have nothing to do with
    // sessions. A default of "no past conversations" is also the honest starting state.
    listCodeSessions: vi.fn(async () => []),
    // The provider picker reads the live catalogue of external agents from here. Resolved by
    // default with an EMPTY list, which is the state of a machine with no adapters installed — so
    // every suite that is not about external agents sees the screen it saw before they existed.
    getDoctor: vi.fn(async () => ({
      has_any_key: true,
      configured_providers: ["openrouter"],
      default_model: "test/model",
      tiers: { weak: "w", mid: "m", top: "t" },
      memory_backend: "sqlite",
      cache: true,
      sandbox: "local",
      external_agents: [],
    })),
    getPausedRuns: vi.fn(),
    respondToRun: vi.fn(),
    streamAgents: vi.fn(),
    cancelAgents: vi.fn(),
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
    external_agent: "",
    // The coding turn is always assembled with the ledger, so the fixture's default is the guarded
    // case. A test that wants the unguarded chat has to ask for it — which is the right way round:
    // the warning should never appear in a suite that is not about the warning.
    unguarded: false,
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

/** A "was it worth it?" report, as the server would compute it. */
export function worthReport(profiles: Partial<ProfileWorth>[] = [], over: Partial<WorthReport> = {}): WorthReport {
  const groups: ProfileWorth[] = profiles.map((p) => ({
    profile: "balanced",
    runs: 1,
    passed: 1,
    // The fixture's default pass is a VERIFIED one, so a test that says nothing about evidence gets
    // the strong case. A default of 0 would quietly make every unrelated test assert the warning
    // colour, which is how a fixture starts deciding what the tests are about.
    passed_by_verifier: 1,
    reverted: 0,
    unproductive: 0,
    attempts_total: 1,
    usd_total: 0.01,
    usd_known_runs: 1,
    ...p,
  }));
  return {
    profiles: groups,
    total_runs: groups.reduce((n, g) => n + g.runs, 0),
    readable_n: 10,
    any_readable: groups.some((g) => g.runs >= 10),
    ...over,
  };
}

export function scriptTurn(
  script: {
    session?: string;
    tokens?: string[];
    tools?: { name: string; arguments: Record<string, string>; ok: boolean; observation: string }[];
    edits?: { path: string; patch: string }[];
    verified?: CodeVerified;
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
    // Before `done`, as the server sends it — `done` is the terminal frame, and a verdict after it
    // would never reach a client that finalises there.
    if (script.verified) h.onVerified?.(script.verified);
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
    // Empty, matching a receipt written before the field existed — the case every upgrade has and
    // the one a per-project filter must not silently attribute to whatever project is open.
    workspace: "",
    ...over,
  };
}
