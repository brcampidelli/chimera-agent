import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ShieldHalf } from "lucide-react";

import { getPostureFacts, type Approval, type Reach } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";

/** What the agent may do to your files, stated rather than configured.
 *
 * This replaced two three-value selectors. The selectors were asking the user to decide, before
 * typing anything, a question the system already answers well: edit inside the workspace, run no
 * shell commands, and stop for a verdict if the run consumed untrusted content. Those defaults still
 * travel on every request — omitting them is not neutral, it resolves to *no* tool denials and *no*
 * pause, which is more permissive than any corner of the grid someone could have picked.
 *
 * What could not be dropped is the sentence. It is the one thing this app says that a competitor
 * does not: not "here is your safety level" but "here is what is true on this machine right now",
 * asked of the sandbox rather than read off the config. So it stays, as a line above the composer
 * instead of a panel with two rows of buttons — a capability that appears because it is relevant,
 * which is the whole pattern being copied.
 *
 * The warning is the reason the query is never cached (`staleTime: 0` on the server's side of this,
 * and a fresh call here): a Docker daemon that died since the last answer must change the answer.
 */
function sentence(facts: NonNullable<ReturnType<typeof useFacts>["data"]>, t: TFunc): string {
  const parts = [
    facts.writes === "nothing"
      ? t("code.posture.saysNoWrites")
      : t("code.posture.saysWrites", { path: facts.workspace }),
    t(`code.posture.saysShell.${facts.shell}` as const),
    t(`code.posture.saysPause.${facts.pauses}` as const),
  ];
  return parts.join(" ");
}

function useFacts(reach: Reach, approval: Approval, workspace: string) {
  return useQuery({
    queryKey: ["posture", reach, approval, workspace],
    queryFn: () => getPostureFacts(reach, approval, workspace || null),
    staleTime: 0,
    gcTime: 0,
  });
}

export function PostureNote({
  workspace,
  reach,
  approval,
}: {
  workspace: string;
  reach: Reach;
  approval: Approval;
}) {
  const t = useT();
  const facts = useFacts(reach, approval, workspace);

  return (
    <div className="flex flex-col gap-1">
      {facts.data ? (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <ShieldHalf className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
          {sentence(facts.data, t)}
        </p>
      ) : facts.isError ? (
        // Silence here would read as "nothing to worry about", which is the opposite of what an
        // unknown posture means.
        <p className="text-xs text-bad">{t("code.posture.unknown")}</p>
      ) : null}
      {facts.data?.fell_back_to_host ? (
        // The one case where the honest answer contradicts what the user set up. Pre-emptive on
        // purpose: telling someone their sandbox was not running AFTER a shell command already ran
        // on their machine is a report, not a warning.
        <p className="flex items-start gap-1.5 text-xs text-bad">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("code.posture.fellBack")}
        </p>
      ) : null}
    </div>
  );
}
