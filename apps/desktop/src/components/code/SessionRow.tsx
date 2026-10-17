import { useId, useMemo, type ReactNode } from "react";
import { Archive, Braces, Check, CopyPlus, GitBranch, GitPullRequest, Trash2 } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import type { CodeSessionMeta, CodeSessionState } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** How each state reads on a row: a dot, and the words a screen reader and a tooltip give it.
 *
 *  Idle draws nothing — an indicator at zero is noise, the rule `PendingApprovals` follows too. The
 *  colours are the status tokens: a question waiting is a warning, a failed turn is bad, edits to
 *  look at are the accent drawn hollow, so "something to see" never reads as "something wrong". The
 *  hollow dot is also the sidebar's "unread": a turn that ended with edits you have not looked at is
 *  exactly what unread would mean here, so there is no second flag to drift from it. */
export const STATE_DOT: Record<Exclude<CodeSessionState, "idle">, { label: string; dot: string }> = {
  running: { label: "code.sessions.running", dot: "bg-accent" },
  blocked: { label: "code.sessions.state.waiting", dot: "bg-warn" },
  failed: { label: "code.sessions.state.failed", dot: "bg-bad" },
  review: { label: "code.sessions.state.review", dot: "border border-accent" },
  done: { label: "code.sessions.state.done", dot: "bg-ok" },
};

/** Row actions shown under the pointer or while the row holds keyboard focus. `hidden` rather than
 *  `opacity-0`: invisible buttons that still take their width were what truncated every title a
 *  third of the way across a 240px column. Focus reveals them, so Tab still reaches each one. */
const REVEAL =
  "hidden px-1 text-muted-foreground group-hover/session:inline-flex group-focus-within/session:inline-flex";

/** "3 h ago", in the chosen language rather than the machine's. Memoised per language because it
 *  renders once per row of a list that can hold hundreds. */
function useAgo(): (seconds: number) => string {
  const { lang } = useI18n();
  return useMemo(() => {
    const fmt = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "narrow" });
    return (seconds: number) => {
      const diff = seconds - Date.now() / 1000;
      const abs = Math.abs(diff);
      if (abs < 60) return fmt.format(0, "minute");
      if (abs < 3600) return fmt.format(Math.round(diff / 60), "minute");
      if (abs < 86400) return fmt.format(Math.round(diff / 3600), "hour");
      if (abs < 86400 * 30) return fmt.format(Math.round(diff / 86400), "day");
      if (abs < 86400 * 365) return fmt.format(Math.round(diff / (86400 * 30)), "month");
      return fmt.format(Math.round(diff / (86400 * 365)), "year");
    };
  }, [lang]);
}

export interface SessionRowActions {
  onResume: (session: CodeSessionMeta) => void;
  onDone: (session: CodeSessionMeta, done: boolean) => void;
  onFork: (session: CodeSessionMeta) => void;
  onInspect: (session: CodeSessionMeta) => void;
  onArchive: (session: CodeSessionMeta) => void;
  onDelete: (session: CodeSessionMeta) => void;
  pending: { done: boolean; fork: boolean; archive: boolean };
}

/** One conversation in the sidebar: two lines, the title and what tells two similar ones apart.
 *
 * The second line is the branch (only for a linked worktree — a main checkout's stamped branch goes
 * stale the moment someone switches it, a worktree's is what the task was given) and how long ago it
 * was last touched. A row, not one big button: the actions are buttons themselves, and a button
 * inside a button is invalid markup that browsers resolve by dropping one of them, usually silently.
 *
 * `extra` is a slot for more row actions (a rename, say), placed before the destructive ones so a
 * new action never lands next to Delete by accident.
 */
export function SessionRow({
  session,
  state,
  active,
  actions,
  extra,
}: {
  session: CodeSessionMeta;
  state: CodeSessionState;
  active: boolean;
  actions: SessionRowActions;
  extra?: ReactNode;
}) {
  const t = useT();
  const ago = useAgo();
  const dot = state === "idle" ? null : STATE_DOT[state];
  const name = session.title || t("code.sessions.untitled");
  const pullRequestCount = session.pull_request_count ?? 0;
  const branch = session.checkout_kind === "worktree" ? session.branch : null;
  const when = session.updated_at > 0 ? ago(session.updated_at) : "";
  const metaId = useId();
  const hasMeta = Boolean(branch || when);
  return (
    <div className="group/session flex items-start">
      <button
        type="button"
        onClick={() => actions.onResume(session)}
        title={name}
        // Named by the title alone, described by the second line: a conversation is called what it
        // is called, and "3 h ago" is a fact about it, not part of its name.
        aria-label={name}
        aria-describedby={hasMeta ? metaId : undefined}
        aria-current={active ? "true" : undefined}
        className={cn(
          "flex min-w-0 flex-1 flex-col px-3 py-1 pl-6 text-left transition-colors duration-1 ease-out",
          focusRing,
          active ? "bg-accent/15 text-accent-ink" : "text-foreground/80 hover:text-foreground",
        )}
      >
        <span className="flex w-full min-w-0 items-center gap-1.5 text-sm">
          {dot ? (
            <span
              role="status"
              aria-label={t(dot.label)}
              title={t(dot.label)}
              className={cn("inline-block h-1.5 w-1.5 shrink-0 rounded-full", dot.dot)}
            />
          ) : (
            // The same width with nothing in it, so titles start in one column with or without a dot.
            <span aria-hidden className="inline-block h-1.5 w-1.5 shrink-0" />
          )}
          <span className="truncate">{name}</span>
        </span>
        {hasMeta ? (
          <span id={metaId} className="flex w-full min-w-0 items-center gap-1 pl-3 text-xs text-muted-foreground">
            {branch ? (
              <>
                <GitBranch aria-hidden className="h-3 w-3 shrink-0" />
                <span className="min-w-0 truncate font-mono">{branch}</span>
              </>
            ) : null}
            {branch && when ? <span aria-hidden>·</span> : null}
            {when ? <span className="shrink-0">{when}</span> : null}
          </span>
        ) : null}
      </button>
      {/* The PRs opened from this conversation; the link is the latest. Always shown: it is state. */}
      {pullRequestCount > 0 && session.pull_request ? (
        <a
          href={session.pull_request.url}
          target="_blank"
          rel="noreferrer"
          aria-label={t("code.sessions.pullRequests", { n: pullRequestCount })}
          title={t("code.sessions.pullRequests", { n: pullRequestCount })}
          className={cn(
            "inline-flex items-center gap-0.5 px-1 py-1.5 text-xs tabular-nums text-muted-foreground hover:text-foreground",
            focusRing,
          )}
        >
          <GitPullRequest className="h-3 w-3" aria-hidden />
          <span aria-hidden>{pullRequestCount}</span>
        </a>
      ) : null}
      <RowActions session={session} state={state} name={name} actions={actions} extra={extra} />
    </div>
  );
}

function RowActions({
  session,
  state,
  name,
  actions,
  extra,
}: {
  session: CodeSessionMeta;
  state: CodeSessionState;
  name: string;
  actions: SessionRowActions;
  extra?: ReactNode;
}) {
  const t = useT();
  const working = state === "running" || state === "blocked";
  const button = (cls: string) => cn(REVEAL, "py-1.5 hover:text-foreground", focusRing, cls);
  return (
    <div className="flex shrink-0 items-start">
      {/* "Done" is the owner's word, never inferred: offered on a conversation at rest, and taken
          back the same way. The server refuses it while it works. A done row keeps its check. */}
      {working ? null : (
        <button
          type="button"
          aria-pressed={state === "done"}
          aria-label={t(state === "done" ? "code.sessions.reopenOne" : "code.sessions.doneOne", { name })}
          disabled={actions.pending.done}
          className={button(state === "done" ? "inline-flex" : "")}
          onClick={() => actions.onDone(session, state !== "done")}
        >
          <Check className="h-3 w-3" />
        </button>
      )}
      <button
        type="button"
        aria-label={t("code.sessions.forkOne", { name })}
        disabled={actions.pending.fork}
        className={button("")}
        onClick={() => actions.onFork(session)}
      >
        <CopyPlus className="h-3 w-3" />
      </button>
      <button
        type="button"
        aria-label={t("code.sessions.jsonOne", { name })}
        className={button("")}
        onClick={() => actions.onInspect(session)}
      >
        <Braces className="h-3 w-3" />
      </button>
      {extra}
      {/* Not offered while it works or waits: the server refuses those, and the archive is a
          collapsed section where that state would go unseen. */}
      {working ? null : (
        <button
          type="button"
          aria-label={t("code.sessions.archiveOne", { name })}
          disabled={actions.pending.archive}
          className={button("")}
          onClick={() => actions.onArchive(session)}
        >
          <Archive className="h-3 w-3" />
        </button>
      )}
      {/* Deleting a conversation has existed on the server since the list did, and reached the
          screen only as "Clear" — which acts on the conversation you have OPEN. */}
      <button
        type="button"
        aria-label={t("code.sessions.deleteOne", { name })}
        className={button("pr-2 hover:text-bad-foreground")}
        onClick={() => actions.onDelete(session)}
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}
