import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { getClaudeHooks } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * A persistent warning on the Code screen, one line PER ORIGIN, when hooks the person wrote to guard
 * tool calls are NOT in force here: a `PreToolUse` hook that cannot run (no sandbox that isolates,
 * which is the Windows default) or a repository's hooks that were never confirmed or changed since.
 * Silence would read as "the guard is on". Nothing is shown when nothing is wrong, or when the answer
 * is not known (the server does not probe Docker for this screen).
 */
export function ClaudeHooksNotice({ workspace }: { workspace: string }) {
  const t = useT();
  const hooks = useQuery({
    queryKey: ["claude-hooks", "code", workspace],
    queryFn: () => getClaudeHooks(workspace || undefined),
    retry: false,
  });
  const data = hooks.data;
  if (!data) return null;
  const lines: { key: string; text: string }[] = [];
  for (const origin of ["repo", "user"] as const) {
    const label = origin === "repo" ? t("settings.hooks.origin.repo") : t("settings.hooks.origin.user");
    const runnable = origin === "repo" ? data.runnable_repo : data.runnable_user;
    const guards = data.hooks.some((h) => h.origin === origin && h.event === "PreToolUse");
    if (guards && runnable === false) {
      lines.push({ key: `run-${origin}`, text: t("code.hooks.cannotRun", { origin: label }) });
    }
    const source = data.sources.find((s) => s.origin === origin && s.enabled && s.trust && s.trust !== "trusted");
    if (source) {
      const state = source.trust === "changed" ? t("settings.hooks.trust.changed") : t("settings.hooks.trust.new");
      lines.push({ key: `trust-${origin}`, text: t("code.hooks.untrusted", { origin: label, state }) });
    }
  }
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-1" aria-label={t("settings.hooks.loaded.title")}>
      {lines.map((l) => (
        <li key={l.key} role="status" className="flex items-start gap-2 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>{l.text}</span>
        </li>
      ))}
    </ul>
  );
}
