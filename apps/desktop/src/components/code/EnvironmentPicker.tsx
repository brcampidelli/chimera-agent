import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Terminal } from "lucide-react";

import { Tooltip } from "@/components/ui/tooltip";
import {
  listCodeEnvironments,
  listCodeSessions,
  setCodeEnvironmentDefault,
  type CodeEnvironment,
} from "@/lib/api";
import { LOCAL_ENVIRONMENT } from "@/lib/environments";
import { useT, type TFunc } from "@/lib/i18n";

/** An environment's name in the reader's language: the built-in one is translated, a saved one is the owner's own word. */
export function environmentName(t: TFunc, env: { id: string; name: string }): string {
  return env.id === LOCAL_ENVIRONMENT ? t("env.local") : env.name;
}

/**
 * Where this conversation's commands run: chosen before the first message, fixed after it.
 *
 * Before the first turn it is a list of the saved environments (the machine itself first) and the
 * choice rides on that turn only; the server fixes it there and refuses a change. After it, the
 * picker is the conversation's own environment, read back from the list the sidebar already holds,
 * and cannot be changed: the transcript of a conversation that ran in one place and then another
 * would describe two machines.
 *
 * It draws nothing while there is nothing to choose — no saved environment — so an install that never
 * made one sees the composer it always had. Making and removing environments is Settings'.
 */
export function EnvironmentPicker({
  workspace,
  sessionId,
  started,
  value,
  onChange,
}: {
  workspace: string;
  sessionId: string | null;
  /** The conversation has sent a turn: its environment is fixed. */
  started: boolean;
  /** The id chosen on this screen, or "" for "whatever the project's default is". */
  value: string;
  onChange: (id: string) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["code-environments", workspace],
    queryFn: () => listCodeEnvironments(workspace || undefined),
  });
  const sessions = useQuery({
    queryKey: ["code-sessions"],
    queryFn: () => listCodeSessions(),
    enabled: started && sessionId !== null,
  });
  const makeDefault = useMutation({
    mutationFn: (id: string) => setCodeEnvironmentDefault(workspace, id === LOCAL_ENVIRONMENT ? "" : id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["code-environments"] }),
  });
  const known = list.data?.environments ?? [];
  const recorded = sessions.data?.find((s) => s.id === sessionId)?.environment ?? null;

  if (started) {
    // A conversation with no record is the local one: every conversation made before environments.
    const fixed = recorded ?? { id: LOCAL_ENVIRONMENT, name: "", kind: "local" };
    if (fixed.id === LOCAL_ENVIRONMENT) return null;
    const alive = known.some((e) => e.id === fixed.id);
    return (
      <Tooltip label={t("env.picker.locked")}>
        <span
          className="inline-flex items-center gap-1.5 rounded-chip border border-border px-2 py-1 text-xs text-muted-foreground"
          data-testid="environment-fixed"
        >
          <Terminal aria-hidden className="h-3.5 w-3.5" />
          <span className="sr-only">{t("env.picker.label")}: </span>
          {environmentName(t, fixed)}
          {alive ? null : <span>({t("env.removed")})</span>}
        </span>
      </Tooltip>
    );
  }

  if (known.length <= 1) return null;
  const projectDefault = list.data?.project_default ?? "";
  const shown = value || projectDefault || LOCAL_ENVIRONMENT;
  const label = (env: CodeEnvironment) =>
    env.available ? environmentName(t, env) : `${environmentName(t, env)} (${t("env.unavailable")})`;
  return (
    <div className="inline-flex items-center gap-1.5" data-testid="environment-picker">
      <Terminal aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
      <select
        className="field h-8 px-2 text-xs"
        aria-label={t("env.picker.label")}
        value={shown}
        onChange={(event) => onChange(event.target.value)}
      >
        {known.map((env) => (
          <option key={env.id} value={env.id} disabled={!env.available && env.id !== shown}>
            {label(env)}
          </option>
        ))}
      </select>
      {workspace && shown !== (projectDefault || LOCAL_ENVIRONMENT) ? (
        <button
          type="button"
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          disabled={makeDefault.isPending}
          onClick={() => makeDefault.mutate(shown)}
        >
          {t("env.picker.makeDefault")}
        </button>
      ) : null}
      {workspace && projectDefault && shown === projectDefault ? (
        <span className="text-xs text-muted-foreground">{t("env.picker.isDefault")}</span>
      ) : null}
    </div>
  );
}
