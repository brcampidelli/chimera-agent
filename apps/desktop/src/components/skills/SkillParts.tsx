import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";

import { approveSkill, checkSkillBundleUpdate, installSkillBundle, retireSkill, setSkillBundleStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Spinner } from "@/components/ui/panel";
import { day, shortSha } from "@/components/SkillsActiveNow";
import { useT, type TFunc } from "@/lib/i18n";
import type { SkillBundle, SkillStat } from "@/lib/types";

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Everything a switch, install, update or removal changes: both lists, and what reaches the prompt. */
export function useSkillRefresh(): () => void {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: ["skill-catalog"] });
    void client.invalidateQueries({ queryKey: ["skill-bundles"] });
    void client.invalidateQueries({ queryKey: ["skills-effective"] });
  };
}

export function learnedTone(status: string): "ok" | "accent" | "warn" | "muted" {
  if (status === "active") return "ok";
  if (status === "provisional") return "accent";
  if (status === "pending") return "warn";
  return "muted";
}

/** Translated, not echoed. The backend's value is an identifier; a badge is read by a person. An
 *  unknown state falls back to the raw value rather than to an empty badge — a new state from a
 *  newer backend should look odd, not invisible. */
export function learnedStatus(t: TFunc, status: string): string {
  const key = `skills.status.${status}`;
  return t(key) === key ? status : t(key);
}

/** The badges a learned card carries: its state, and two facts that were English literals in every
 *  language once — "unverified" (Memory's word for the same store flag) and a retirement hint, in
 *  amber because it is a suggestion, not a failure. */
export function LearnedBadges({ stat, candidate }: { stat: SkillStat; candidate: boolean }) {
  const t = useT();
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone={learnedTone(stat.status)}>{learnedStatus(t, stat.status)}</Badge>
      {stat.provenance === "tainted" && <Badge tone="warn">{t("memory.unverified")}</Badge>}
      {candidate && <Badge tone="warn">{t("skills.retireCandidate")}</Badge>}
    </div>
  );
}

/**
 * Approve, reactivate, retire. Retiring was a one-way door once: the backend documents `approve` as
 * the transition that un-retires, but the only button that called it was gated on `pending`, so a
 * retired card had no control beside it. Same route, different word — this is putting your own back
 * to work, not approving a stranger's card.
 */
export function LearnedActions({ stat }: { stat: SkillStat }) {
  const t = useT();
  const client = useQueryClient();
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ["skills"] });
    void client.invalidateQueries({ queryKey: ["skills-effective"] });
  };
  const approve = useMutation({ mutationFn: (name: string) => approveSkill(name), onSuccess: invalidate });
  const retire = useMutation({ mutationFn: (name: string) => retireSkill(name), onSuccess: invalidate });
  return (
    <div className="flex flex-wrap justify-end gap-2">
      {stat.status === "pending" && (
        <Button size="sm" onClick={() => approve.mutate(stat.name)}>
          {t("common.approve")}
        </Button>
      )}
      {stat.status === "retired" && (
        <Button size="sm" onClick={() => approve.mutate(stat.name)}>
          {t("skills.reactivate")}
        </Button>
      )}
      {stat.status !== "retired" && (
        <Button size="sm" variant="outline" onClick={() => retire.mutate(stat.name)}>
          {t("skills.retire")}
        </Button>
      )}
    </div>
  );
}

/**
 * On and Off for an installed skill. Off is not uninstalled: trying several and leaving two running
 * is the normal way to use these. An upload not read in this session cannot be turned on — "it stays
 * off until you read it" is a promise about the switch, so the switch keeps it.
 */
export function BundleSwitch({ bundle, unread = false }: { bundle: Pick<SkillBundle, "name" | "status">; unread?: boolean }) {
  const t = useT();
  const refresh = useSkillRefresh();
  const name = bundle.name ?? "";
  const toggle = useMutation({
    mutationFn: (on: boolean) => setSkillBundleStatus(name, on ? "active" : "inactive"),
    onSuccess: refresh,
  });
  const on = bundle.status === "active";
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        size="sm"
        variant={on ? "primary" : "outline"}
        disabled={toggle.isPending || (!on && unread)}
        title={!on && unread ? t("skills.upload.readFirst") : undefined}
        onClick={() => toggle.mutate(!on)}
      >
        {on ? t("catalog.on") : t("catalog.off")}
      </Button>
      {toggle.error ? <p className="text-xs text-bad-foreground">{errorText(toggle.error)}</p> : null}
    </div>
  );
}

/** Whether the source's copy of an installed skill is newer, asked only when a person clicks.
 *  Updating is the install route with `force`, and it lands the skill switched off: the text is
 *  new, and having read the old one is not having read this one. */
export function UpdateCheck({ name }: { name: string }) {
  const t = useT();
  const refresh = useSkillRefresh();
  const check = useMutation({ mutationFn: () => checkSkillBundleUpdate(name) });
  const update = useMutation({
    mutationFn: () => installSkillBundle(name, true),
    onSuccess: () => {
      check.reset();
      refresh();
    },
  });
  const found = check.data;
  const failed = check.error ?? update.error;

  return (
    <div className="space-y-1">
      {found ? (
        <p className="text-xs text-muted-foreground">
          {found.changed === false
            ? t("catalog.update.same", { current: shortSha(found.current_ref) })
            : t(found.changed ? "catalog.update.newer" : "catalog.update.unknown", {
                latest: shortSha(found.latest_ref),
                date: day(found.latest_date),
                current: shortSha(found.current_ref),
              })}
        </p>
      ) : null}
      {failed ? <p className="text-xs text-bad-foreground">{errorText(failed)}</p> : null}
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" disabled={check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? <Spinner /> : <RefreshCw className="h-4 w-4" />}
          {t("catalog.update.check")}
        </Button>
        {found && found.changed !== false ? (
          <Button size="sm" variant="outline" disabled={update.isPending} onClick={() => update.mutate()}>
            {update.isPending ? <Spinner /> : <Download className="h-4 w-4" />}
            {t("catalog.update.apply")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
