import type { CodeToolEvent } from "@/lib/api";

export type OutcomeKind = "file" | "server" | "media" | "verify";

/** One thing the turn visibly did. `label` is null only for a verification nobody ran. */
export interface Outcome {
  kind: OutcomeKind;
  label: string | null;
}

export interface StepSummary {
  count: number;
  succeeded: number;
  failed: number;
  /** The tool with the most failed calls, or null when nothing failed. */
  failedTool: string | null;
  /** Total time the calls took, from the server's own measurement. */
  elapsed: number;
  /** At most `MAX_OUTCOMES`. The last one is always the verification slot. */
  outcomes: Outcome[];
}

export const MAX_OUTCOMES = 4;

const FILE_TOOLS = new Set(["write_file", "edit_file", "apply_patch", "create_document"]);
/** Read after the writes: a file the turn only looked at is still one it touched, and the viewer opens it. */
const READ_TOOLS = new Set(["read_file", "view_file", "open_file"]);
const SERVER_TOOL = /(?:^|_)(?:server|serve|preview)(?:_|$)/i;
const MEDIA_TOOL = /image|video|media|chart|render/i;
const VERIFY_TOOL = /^(?:verify|check|test|lint|typecheck|run_tests|pytest|build)$/i;
const VERIFY_COMMAND = /test|lint|typecheck|build|check|verify|pytest/i;

/** Facts for the compact receipt, drawn only from calls the turn actually made.
 *
 *  A verification counts when it RAN, pass or fail: a failing test run is still a verification.
 *  `run_command` counts only when its command reads like one, so `ls` does not claim a check. */
export function summarizeSteps(tools: readonly CodeToolEvent[], edits: readonly { path: string }[] = []): StepSummary {
  const failures = new Map<string, number>();
  for (const tool of tools) if (!tool.ok) failures.set(tool.name, (failures.get(tool.name) ?? 0) + 1);
  const failedTool = [...failures].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? null;

  const files: string[] = [];
  const reads: string[] = [];
  const servers: string[] = [];
  const media: string[] = [];
  const verifies: string[] = [];
  for (const tool of tools) {
    const args = tool.arguments ?? {};
    const command = args.command ?? args.cmd ?? "";
    if (tool.ok && FILE_TOOLS.has(tool.name)) pushUnique(files, args.path ?? args.file_path ?? "");
    if (tool.ok && READ_TOOLS.has(tool.name)) pushUnique(reads, args.path ?? args.file_path ?? "");
    if (tool.ok && SERVER_TOOL.test(tool.name)) pushUnique(servers, args.port ? `:${args.port}` : command || tool.name);
    if (tool.ok && MEDIA_TOOL.test(tool.name)) pushUnique(media, args.out ?? args.path ?? tool.name);
    if (VERIFY_TOOL.test(tool.name) || (tool.name === "run_command" && VERIFY_COMMAND.test(command))) {
      pushUnique(verifies, command || tool.name);
    }
  }
  for (const edit of edits) pushUnique(files, edit.path);
  for (const read of reads) pushUnique(files, read);

  const head: Outcome[] = [
    ...files.map((label): Outcome => ({ kind: "file", label })),
    ...servers.map((label): Outcome => ({ kind: "server", label })),
    ...media.map((label): Outcome => ({ kind: "media", label })),
  ].slice(0, MAX_OUTCOMES - 1);
  const verify: Outcome = verifies.length > 0
    ? { kind: "verify", label: verifies[0] ?? null }
    : { kind: "verify", label: null };

  const succeeded = tools.filter((tool) => tool.ok).length;
  return {
    count: tools.length,
    succeeded,
    failed: tools.length - succeeded,
    failedTool,
    elapsed: tools.reduce((total, tool) => total + (tool.duration_seconds ?? 0), 0),
    outcomes: [...head, verify],
  };
}

/** Additions and deletions of a unified diff, counting no file headers. */
export function diffStat(patch: string): { additions: number; deletions: number } {
  let additions = 0;
  let deletions = 0;
  for (const line of patch.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) additions += 1;
    else if (line.startsWith("-") && !line.startsWith("---")) deletions += 1;
  }
  return { additions, deletions };
}

/** A diff longer than this (additions plus deletions) starts folded in the normal view. */
export const LARGE_DIFF_LINES = 40;

function pushUnique(list: string[], value: string): void {
  if (value && !list.includes(value)) list.push(value);
}
