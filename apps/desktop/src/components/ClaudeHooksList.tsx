import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { getClaudeHooks, trustClaudeHooks } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * The hooks Chimera loaded from Claude Code's settings files (`chimera/governance/claude_hooks.py`),
 * each with where it came from. Read-only except for one act: confirming a repository's hooks, which
 * the owner's screen does (the route refuses the token alone). A repository's hooks are NOT loaded
 * until confirmed, and again after any edit to those files; the list says "changed" when that is why.
 * A hook that cannot run on this machine is said to be so, because a guard that is listed but does not
 * run is a false comfort.
 */
export function ClaudeHooksList({
  repoOn,
  userOn,
  project,
}: {
  repoOn: boolean;
  userOn: boolean;
  project?: string;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const hooks = useQuery({
    queryKey: ["claude-hooks", repoOn, userOn, project ?? ""],
    queryFn: () => getClaudeHooks(project),
    retry: false,
  });
  const confirm = useMutation({
    mutationFn: (fingerprint: string) => trustClaudeHooks(project, fingerprint),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["claude-hooks"] }),
  });
  const data = hooks.data;
  if (!data) return null;
  const pendingSource = data.sources.find(
    (s) => s.origin === "repo" && s.exists && s.enabled && s.trust && s.trust !== "trusted",
  );
  return (
    <div className="border-t border-hairline px-4 py-3">
      <h3 className="text-sm font-semibold">{t("settings.hooks.loaded.title")}</h3>
      {pendingSource ? (
        <div className="mt-2 space-y-1.5 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span>
              {t("settings.hooks.origin.repo")}: {pendingSource.trust === "changed" ? t("settings.hooks.trust.changed") : t("settings.hooks.trust.new")}
            </span>
            <Button
              size="sm"
              onClick={() => confirm.mutate(data.fingerprint)}
              disabled={confirm.isPending || !data.fingerprint}
            >
              {t("settings.hooks.trust.confirm")}
            </Button>
          </div>
          {/* What the button confirms, shown BEFORE it is pressed: parsed for display, not loaded. */}
          <ul className="space-y-1" aria-label={t("settings.hooks.trust.pending")}>
            {data.pending.map((h) => (
              <li key={`${h.source}:${h.event}:${h.matcher}:${h.command}`}>
                <span className="font-medium">{h.event}</span>{" "}
                <span className="text-muted-foreground">{h.matcher}</span>{" "}
                <code className="break-all text-xs">{h.command}</code>
              </li>
            ))}
          </ul>
          {confirm.isError ? (
            <p role="alert" className="text-xs text-danger">
              {t("settings.hooks.trust.stale")}
            </p>
          ) : null}
        </div>
      ) : null}
      {data.hooks.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">{t("settings.hooks.loaded.empty")}</p>
      ) : (
        <>
          {(data.runnable_repo === false || data.runnable_user === false) && (
            <p role="status" className="mt-1 text-sm text-warn-foreground">
              {t("settings.hooks.wontRun")}
            </p>
          )}
          <ul className="mt-2 space-y-1.5">
            {data.hooks.map((h) => (
              <li key={`${h.source}:${h.event}:${h.matcher}:${h.command}`} className="text-sm">
                <span className="rounded-full border border-hairline px-2 py-0.5 text-xs">
                  {h.origin === "repo" ? t("settings.hooks.origin.repo") : t("settings.hooks.origin.user")}
                </span>{" "}
                <span className="font-medium">{h.event}</span>{" "}
                <span className="text-muted-foreground">{h.matcher}</span>{" "}
                <code className="break-all text-xs">{h.command}</code>
                {h.on_failure === "block" && (
                  <span className="text-muted-foreground"> · {t("settings.hooks.onFailureBlock")}</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {data.sources.some((s) => s.exists && !s.enabled) && (
        <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
          {data.sources
            .filter((s) => s.exists && !s.enabled)
            .map((s) => (
              <li key={s.path} className="break-all">
                {s.path} · {t("settings.hooks.sourceOff")}
              </li>
            ))}
        </ul>
      )}
      {data.sources.some((s) => s.error) && (
        <ul className="mt-2 space-y-0.5 text-xs text-danger">
          {data.sources
            .filter((s) => s.error)
            .map((s) => (
              <li key={s.path} className="break-all">
                {s.path}: {s.error}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
