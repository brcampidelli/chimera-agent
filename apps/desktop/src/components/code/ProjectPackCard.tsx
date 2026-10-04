import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackageCheck } from "lucide-react";

import { acceptProjectPack, getProjectPack, revokeProjectPack } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { ProjectPack } from "@/lib/types";

/**
 * Which sentence describes the pack's standing — the first gate that is not met, in order.
 *
 * `held` comes before the file's own error: when the file changed, broke or vanished after the
 * owner accepted it, the version they accepted keeps applying from their record (a change to the
 * file never lifts a narrowing), and "it does not apply" would be the opposite of the truth. The
 * error, if any, is shown on its own line beside it.
 */
function stateKey(pack: ProjectPack): string {
  if (pack.held) return pack.enabled ? "code.pack.state.held" : "code.pack.state.acceptedOff";
  if (pack.error) return "code.pack.state.error";
  if (pack.changed) return "code.pack.state.changed";
  if (!pack.accepted) return pack.enabled ? "code.pack.state.pending" : "code.pack.state.off";
  return pack.enabled ? "code.pack.state.applied" : "code.pack.state.acceptedOff";
}

function List({ label, names }: { label: string; names: string[] | null | undefined }) {
  if (!names || names.length === 0) return null;
  return (
    <p>
      <span className="text-foreground">{label}:</span>{" "}
      <span className="font-mono">{names.join(", ")}</span>
    </p>
  );
}

/**
 * "In this project" — the folder's `.chimera/pack.json`, under the project bar (study 29, P7.6).
 *
 * A pack can only take away from what the owner switched on, and the card's job is to make that
 * visible rather than asserted: what it keeps, what it hides here, what it named and could not have
 * (a bundle that is not on, a server that is not configured — clamped, never activated), which tools
 * it denies, and which keys it asked for that a pack cannot set. The file can arrive in a clone, so
 * it applies only after the owner accepts it — the digest this card read, so a file that changed in
 * between is refused rather than accepted unseen.
 *
 * Nothing is rendered for a folder without a pack: most have none, and an empty card on every
 * project would be a box to read past.
 */
export function ProjectPackCard({ workspace }: { workspace: string }) {
  const t = useT();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const pack = useQuery({
    queryKey: ["project-pack", workspace],
    queryFn: () => getProjectPack(workspace),
    enabled: Boolean(workspace),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["project-pack", workspace] });
    void qc.invalidateQueries({ queryKey: ["skills-effective"] });
  };
  const accept = useMutation({
    mutationFn: (digest: string) => acceptProjectPack(workspace, digest),
    onSuccess: refresh,
  });
  const revoke = useMutation({ mutationFn: () => revokeProjectPack(workspace), onSuccess: refresh });

  const data = pack.data;
  // A held pack is shown even with the file gone: a narrowing nobody can see is one nobody can lift.
  if (!workspace || !data || (!data.present && !data.held)) return null;
  const failed = accept.error ?? revoke.error;
  const readable = !data.error;
  const shellDenied = (data.tools_denied ?? []).includes("run_shell");

  return (
    <div className="space-y-1 border-b border-hairline px-5 py-2 text-xs text-muted-foreground">
      <div className="flex flex-wrap items-center gap-2">
        <PackageCheck className="h-4 w-4 shrink-0 text-accent" />
        <span className="font-semibold text-foreground">{t("code.pack.title")}</span>
        <Badge tone={data.applied ? "ok" : data.error || data.changed ? "warn" : "muted"}>
          {t(data.applied ? "code.pack.badge.applied" : "code.pack.badge.notApplied")}
        </Badge>
        <span>{t(stateKey(data), { error: data.error ?? "" })}</span>
        {data.held && data.error ? <span>{data.error}</span> : null}
        {/* Which runs "applied" reaches. The pack is read where the app assembles a run's tools;
            scheduled jobs, the terminal and the bots assemble their own and read no pack — neither
            its tools nor its skills — and a card that said "runs in this project" was wrong there. */}
        {data.applied ? <span>{t("code.pack.reach")}</span> : null}
        <span className="ml-auto flex gap-2">
          {readable ? (
            <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {t("code.pack.details")}
            </Button>
          ) : null}
          {readable && data.present && !data.accepted ? (
            <Button
              size="sm"
              variant="outline"
              disabled={accept.isPending || !data.digest}
              onClick={() => accept.mutate(data.digest ?? "")}
            >
              {t("code.pack.accept")}
            </Button>
          ) : null}
          {data.accepted || data.held ? (
            <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate()}>
              {t("code.pack.revoke")}
            </Button>
          ) : null}
        </span>
      </div>
      {failed ? (
        <p className="text-bad-foreground">{failed instanceof Error ? failed.message : String(failed)}</p>
      ) : null}
      {open && readable ? (
        <div className="space-y-1 pl-6">
          <p>{t("code.pack.only")}</p>
          <List label={t("code.pack.skillsKept")} names={data.skills_kept} />
          <List label={t("code.pack.skillsHidden")} names={data.skills_hidden} />
          <List label={t("code.pack.skillsNotActive")} names={data.skills_not_active} />
          <List label={t("code.pack.mcpKept")} names={data.mcp_kept} />
          <List label={t("code.pack.mcpHidden")} names={data.mcp_hidden} />
          <List label={t("code.pack.mcpNotConfigured")} names={data.mcp_not_configured} />
          <List label={t("code.pack.toolsDenied")} names={data.tools_denied} />
          {/* Said next to the list, because the cost is not obvious from the name: without the
              shell the agent cannot run the tests itself. The verifier that decides the receipt is
              not a tool and a pack cannot reach it. */}
          {shellDenied ? <p className="text-warn-foreground">{t("code.pack.shellNote")}</p> : null}
          <List label={t("code.pack.ignored")} names={data.ignored} />
        </div>
      ) : null}
    </div>
  );
}
