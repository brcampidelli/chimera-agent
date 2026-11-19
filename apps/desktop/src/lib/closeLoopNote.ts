import type { CloseLoopPayload, CodeTurnDone } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";

const SKIPPED: Readonly<Record<string, string>> = {
  "verification_failed": "code.closeLoop.skip.verification_failed",
  "self_check_failed": "code.closeLoop.skip.self_check_failed",
  "incomplete_changes": "code.closeLoop.skip.incomplete_changes",
  "not_repo": "code.closeLoop.skip.not_repo",
  "no_changes": "code.closeLoop.skip.no_changes",
  "detached": "code.closeLoop.skip.detached",
  "default_branch": "code.closeLoop.skip.default_branch",
  "no_gh": "code.closeLoop.skip.no_gh",
  "no_origin": "code.closeLoop.skip.no_origin",
  "push_elsewhere": "code.closeLoop.skip.push_elsewhere",
  "no_base": "code.closeLoop.skip.no_base",
  "nothing_ahead": "code.closeLoop.skip.nothing_ahead",
  "gh_signed_out": "code.closeLoop.skip.gh_signed_out",
  "gh_unknown_host": "code.closeLoop.skip.gh_unknown_host",
  "remote_unreachable": "code.closeLoop.skip.remote_unreachable",
  "bad_branch": "code.closeLoop.skip.bad_branch",
};
const MERGE: Readonly<Record<string, string>> = {
  "merging_off": "code.closeLoop.merge.merging_off",
  "unreadable_diff": "code.closeLoop.merge.unreadable_diff",
  "exceptions": "code.closeLoop.merge.exceptions",
  "merge_refused": "code.closeLoop.merge.merge_refused",
};
const CI: Readonly<Record<string, string>> = {
  "pending": "code.closeLoop.ci.pending",
  "passed": "code.closeLoop.ci.passed",
  "failed": "code.closeLoop.ci.failed",
  "none": "code.closeLoop.ci.none",
};

/** Reasons come from decision codes, never from matching an older English sentence. */
function reason(t: TFunc, kind: "skip" | "merge", code: string | undefined, raw: string): string {
  const known = kind === "skip" ? SKIPPED : MERGE;
  const key = code && Object.prototype.hasOwnProperty.call(known, code) ? known[code] : undefined;
  return key ? t(key) : raw;
}

function note(state: CloseLoopPayload, t: TFunc, includeLink = true): string | null {
  if (state.skipped_reason) {
    return t("code.closeLoop.skipped", {
      reason: reason(t, "skip", state.skipped_code, state.skipped_reason),
    });
  }
  const parts: string[] = [];
  if (state.committed) parts.push(t("code.closeLoop.committed", {
    commit: (state.commit ?? "").slice(0, 7), branch: state.branch ?? "",
  }));
  if (includeLink && state.pr_url) parts.push(t("code.closeLoop.pullRequest", { url: state.pr_url }));
  if (state.ci) parts.push(t("code.closeLoop.ci", {
    status: Object.prototype.hasOwnProperty.call(CI, state.ci) ? t(CI[state.ci]) : state.ci,
  }));
  if (state.fix_attempts) parts.push(t("code.closeLoop.fixAttempts", { n: state.fix_attempts }));
  if (state.merged) parts.push(t("code.closeLoop.merged"));
  else if (state.merge_blocked_reason) parts.push(t("code.closeLoop.notMerged", {
    reason: reason(t, "merge", state.merge_blocked_code, state.merge_blocked_reason),
  }));
  if (state.error) parts.push(t("code.closeLoop.error", { detail: state.error }));
  return parts.length ? t("code.closeLoop.note", { details: parts.join("; ") }) : null;
}

/** Live, replay and watcher refresh all use the same durable marker. */
export function closeLoopNote(done: Pick<CodeTurnDone, "close_loop" | "close_loop_note"> | null, t: TFunc): string | null {
  return done?.close_loop_note === "payload" && done.close_loop ? note(done.close_loop, t) : null;
}


/** A passed check is provisional until the watch records its merge decision. */
export function closeLoopWatchPending(done: Pick<CodeTurnDone, "close_loop" | "close_loop_note"> | null): boolean {
  const state = done?.close_loop;
  if (done?.close_loop_note !== "payload" || !state || state.error) return false;
  return state.ci === "pending" || (state.ci === "passed" && !state.merged && !state.merge_blocked_reason);
}

export function closeLoopPresentation(done: Pick<CodeTurnDone, "close_loop" | "close_loop_note"> | null, t: TFunc) {
  if (done?.close_loop_note !== "payload" || !done.close_loop) return null;
  const state = done.close_loop;
  // These reasons carry category/path or a redacted remote diagnostic. Keep them as data, not Markdown.
  const diagnostic = !state.skipped_reason && (state.merge_blocked_code === "exceptions" || state.merge_blocked_code === "merge_refused")
    ? state.merge_blocked_reason ?? "" : "";
  return { text: note(state, t, false), diagnostic, prUrl: state.skipped_reason ? "" : state.pr_url ?? "" };
}

/** Match a GitHub/enterprise pull-request address, then let the app's existing navigation handler open it. */
export function closeLoopPrHref(raw: string): string | null {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && !url.username && !url.password && !url.search && !url.hash
      && /^\/[^/]+\/[^/]+\/pull\/\d+\/?$/.test(url.pathname) ? url.href : null;
  } catch {
    return null;
  }
}
