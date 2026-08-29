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
  // An external agent REPLACES the clauses rather than joining them. "Edits inside /project" is a
  // boundary when we own the file tools and a description of the calls we happened to see when we do
  // not — and one sentence cannot be both. What is still true is the checkpoint, so that is what
  // this promises instead.
  if (facts.external_agent) return t("code.posture.saysExternal", { path: facts.workspace });
  const parts = [
    facts.writes === "nothing"
      ? t("code.posture.saysNoWrites")
      : t("code.posture.saysWrites", { path: facts.workspace }),
    t(`code.posture.saysShell.${facts.shell}` as const),
    t(`code.posture.saysPause.${facts.pauses}` as const),
  ];
  return parts.join(" ");
}

function useFacts(
  reach: Reach,
  approval: Approval,
  workspace: string,
  surface: Surface,
  provider: string,
) {
  return useQuery({
    queryKey: ["posture", reach, approval, workspace, surface, provider],
    queryFn: () => getPostureFacts(reach, approval, workspace || null, surface, provider || null),
    // No project, no question. This probes the live sandbox on every call by design (a Docker daemon
    // that died has to change the answer), so asking it when there is nothing to say is a round-trip
    // spent on an answer that will not be rendered.
    enabled: Boolean(workspace),
    staleTime: 0,
    gcTime: 0,
  });
}

type Surface = "run" | "turn" | "chat";

export function PostureNote({
  workspace,
  reach,
  approval,
  surface = "turn",
  provider = "",
}: {
  workspace: string;
  reach: Reach;
  approval: Approval;
  /** Which surface is asking. A chat is assembled WITHOUT the taint ledger unless the user armed it,
   *  and that changes what is true — so it changes the sentence. */
  surface?: Surface;
  /** The external agent that would do the work, or "" for Chimera's own loop. It changes what every
   *  other fact MEANS, so it is part of the question and not a decoration on the answer. */
  provider?: string;
}) {
  const t = useT();
  const facts = useFacts(reach, approval, workspace, surface, provider);

  // No project chosen, nothing to say. The sentence names a directory the agent may edit, and with
  // no project it named the app's own launch directory — which on a fresh install is wherever the
  // launcher happened to point. Announcing write access to a folder the user never picked is the
  // opposite of what this line is for: it turned a statement of fact into a claim about a decision
  // nobody made. It appears when a project does.
  if (!workspace) return null;

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
        <p className="text-xs text-bad-foreground">{t("code.posture.unknown")}</p>
      ) : null}
      {facts.data?.unguarded ? (
        // The condition that makes a permissive default defensible. Nothing marks this conversation
        // after it reads untrusted content, so a page carrying a planted instruction can still get
        // a file written. Saying it is the whole difference between a trade-off and a trap.
        <p className="flex items-start gap-1.5 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("code.posture.unguarded")}
        </p>
      ) : null}
      {facts.data?.external_agent ? (
        // The sentence this whole feature has to be able to stand behind. These agents have file and
        // shell tools of their own, so the write region governs only the calls they route through
        // us. Claiming prevention here would be the one lie the product cannot afford; what is real
        // is the snapshot taken before the turn and the revert offered after it.
        <p className="flex items-start gap-1.5 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("code.posture.externalNote")}
        </p>
      ) : null}
      {facts.data?.fell_back_to_host ? (
        // The one case where the honest answer contradicts what the user set up. Pre-emptive on
        // purpose: telling someone their sandbox was not running AFTER a shell command already ran
        // on their machine is a report, not a warning.
        //
        // Which sentence depends on WHY. "A container was configured, but none is running" was the
        // only one there was, and once the default became `auto` it started appearing on machines
        // where nobody configured a container and no daemon would help — right conclusion, invented
        // reason, and it sent people to start Docker to fix something Docker does not fix.
        <p className="flex items-start gap-1.5 text-xs text-bad-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {facts.data.fell_back_reason === "no_os_sandbox"
            ? t("code.posture.fellBackNoSandbox")
            : t("code.posture.fellBack")}
        </p>
      ) : null}
    </div>
  );
}
