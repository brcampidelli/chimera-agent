import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, GitPullRequest, Loader2 } from "lucide-react";

import { getPullRequestReadiness, openPullRequest } from "@/lib/api";
import type { PullRequestReadiness } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/** Reasons that mean "there is nothing to offer here": no remote to push to, or no GitHub CLI to
 *  open the pull request with. The button only exists with both (study 29, P8.1). */
const SILENT = new Set(["not_repo", "no_origin", "no_gh"]);

/** The words for each reason the server can give. Spelled out so the translation checks can see
 *  every key; an unknown reason falls back to the generic line rather than disappearing. */
const REASON_KEYS = new Map<string, string>(
  Object.entries({
    detached: "code.pr.reason.detached",
    bad_branch: "code.pr.reason.badBranch",
    no_base: "code.pr.reason.noBase",
    default_branch: "code.pr.reason.defaultBranch",
    nothing_ahead: "code.pr.reason.nothingAhead",
    gh_signed_out: "code.pr.reason.ghSignedOut",
    push_elsewhere: "code.pr.reason.pushElsewhere",
    gh_unknown_host: "code.pr.reason.ghUnknownHost",
    remote_unreachable: "code.pr.reason.remoteUnreachable",
  }),
);

/** The subject of the newest commit, without its short hash — a title the owner can keep or edit. */
function suggestedTitle(state: PullRequestReadiness): string {
  const first = state.commits[0] ?? "";
  const space = first.indexOf(" ");
  return space >= 0 ? first.slice(space + 1) : first;
}

/**
 * "Open pull request" for the branch the workspace is on.
 *
 * The press IS the approval, so the form shows what the press publishes before it can be pressed:
 * the branch and its base, where the push goes (origin's push URL, credential removed by the server),
 * whether it updates a branch that already exists there, the commits, the diff summary, and how many
 * changed files are NOT in the push. The request carries what was shown (`head`, `remote`,
 * `remote_head`); a branch, a destination or a remote branch that changed since is refused by the
 * server, not pushed to. The server also refuses the default branch and never forces — nothing here
 * needs to.
 */
export function PullRequestCard({ workspace }: { workspace: string }) {
  const t = useT();
  const qc = useQueryClient();
  const titleId = useId();
  const bodyId = useId();
  // Asked once a minute at most: it runs `gh auth status`, which is a process and a network call.
  const q = useQuery({
    queryKey: ["pull-request", workspace],
    queryFn: () => getPullRequestReadiness(workspace || null),
    staleTime: 60_000,
    retry: false,
  });
  const [composing, setComposing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState(false);
  const send = useMutation({
    mutationFn: (state: PullRequestReadiness) =>
      openPullRequest({
        workspace,
        title: title.trim(),
        body,
        head: state.head,
        remote: state.remote,
        remote_head: state.remote_head,
        draft,
      }),
    onSuccess: (res) => {
      if (res.ok) setComposing(false);
      void qc.invalidateQueries({ queryKey: ["pull-request", workspace] });
    },
  });

  const state = q.data;
  if (!state || SILENT.has(state.reason)) return null;

  if (!state.ready) {
    const key = REASON_KEYS.get(state.reason);
    return (
      <p className="px-4 pb-2 text-xs text-muted-foreground">
        {key ? t(key) : t("code.pr.reason.other")}
      </p>
    );
  }

  const result = send.data;
  return (
    <div className="space-y-2 px-4 pb-3">
      {!composing ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setTitle(suggestedTitle(state));
              setComposing(true);
              send.reset();
            }}
          >
            <GitPullRequest className="h-3.5 w-3.5" />
            {t("code.pr.open")}
          </Button>
          <span className="font-mono text-xs text-muted-foreground">
            {state.branch} → {state.base} · {t("code.pr.ahead", { n: state.ahead })}
          </span>
        </div>
      ) : (
        <div className="space-y-2 rounded-chip bg-surface-2 p-3">
          <p className="text-xs text-muted-foreground">{t("code.pr.publishes")}</p>
          <p className="break-all font-mono text-xs">
            {state.branch} → {state.base} · {state.remote}
          </p>
          <ul className="space-y-0.5 font-mono text-xs text-muted-foreground">
            {state.commits.map((line) => (
              <li key={line} className="truncate" title={line}>
                {line}
              </li>
            ))}
          </ul>
          {state.diffstat ? (
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">
              {state.diffstat}
            </pre>
          ) : null}
          {state.remote_head ? (
            <p className="text-xs text-warn-foreground">
              {t("code.pr.updatesBranch", {
                branch: state.branch,
                from: state.remote_head.slice(0, 12),
                to: state.head.slice(0, 12),
              })}
            </p>
          ) : null}
          {state.uncommitted > 0 ? (
            <p className="text-xs text-warn-foreground">
              {t("code.pr.uncommitted", { n: state.uncommitted })}
            </p>
          ) : null}
          <label htmlFor={titleId} className="block text-xs font-medium">
            {t("code.pr.title")}
          </label>
          <input
            id={titleId}
            className="field h-8 w-full px-2.5 text-xs"
            value={title}
            maxLength={256}
            onChange={(e) => setTitle(e.target.value)}
            disabled={send.isPending}
          />
          <label htmlFor={bodyId} className="block text-xs font-medium">
            {t("code.pr.body")}
          </label>
          <textarea
            id={bodyId}
            className="field min-h-20 w-full px-2.5 py-1.5 text-xs"
            value={body}
            maxLength={60000}
            onChange={(e) => setBody(e.target.value)}
            disabled={send.isPending}
          />
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              className="h-3 w-3 accent-accent"
              checked={draft}
              onChange={(e) => setDraft(e.target.checked)}
              disabled={send.isPending}
            />
            {t("code.pr.draft")}
          </label>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={!title.trim() || send.isPending}
              onClick={() => send.mutate(state)}
            >
              {send.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              {t("code.pr.confirm")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setComposing(false)} disabled={send.isPending}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}
      <div role="status" className="text-xs">
        {result?.ok ? (
          <span className="inline-flex items-center gap-1 text-ok-foreground">
            {t("code.pr.opened")}{" "}
            {result.url ? (
              <a
                href={result.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 underline"
              >
                {result.url} <ExternalLink className="h-3 w-3" />
              </a>
            ) : null}
          </span>
        ) : result ? (
          <span className="text-bad-foreground">
            {t("code.pr.failed")} {result.error}
          </span>
        ) : send.isError ? (
          <span className="text-bad-foreground">{t("code.pr.failed")}</span>
        ) : null}
      </div>
    </div>
  );
}
