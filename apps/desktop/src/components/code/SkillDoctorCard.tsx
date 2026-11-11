import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useNum, useT, type TFunc } from "@/lib/i18n";
import type { SkillCost, SkillDoctor } from "@/lib/types";

/**
 * The card `/skill-doctor` leaves in the conversation: each skill a run is told about, what it costs in
 * context, and whether anything shows it was used. Nothing on it switches a skill off. "Open Skills"
 * takes the person to the Skills screen, where switching off is their own click.
 *
 * "Injected into N run(s)" counts the runs a skill was credited in. A skill with no record says so
 * ("No record") and is never called idle: nothing counted it, which is not the same as zero. A card on
 * probation is still being measured, so it is never suggested for switching off.
 */

// Every key is written out here, not assembled at the call site: the reachability test reads literals.
const ORIGIN_KEY: Record<string, string> = {
  project: "code.skillDoctor.origin.project",
  user: "code.skillDoctor.origin.user",
  plugin: "code.skillDoctor.origin.plugin",
  catalog: "code.skillDoctor.origin.catalog",
  learned: "code.skillDoctor.origin.learned",
};

const SUGGESTION_KEY: Record<string, string> = {
  disable: "code.skillDoctor.sugg.idle",
  keep: "code.skillDoctor.sugg.injected",
  no_record: "code.skillDoctor.sugg.no_record",
  reading_off: "code.skillDoctor.sugg.reading_off",
  probation: "code.skillDoctor.sugg.probation",
  since_promotion: "code.skillDoctor.sugg.sincePromotion",
};

function originLabel(t: TFunc, origin: string): string {
  return t(ORIGIN_KEY[origin] ?? "code.skillDoctor.origin.user");
}

function injectedLabel(t: TFunc, skill: SkillCost, num: (n: number) => string): string {
  return skill.injected_runs === null
    ? t("code.skillDoctor.uses.none")
    : t("code.skillDoctor.injected.count", { n: num(skill.injected_runs) });
}

function suggestionTone(suggestion: string): "bad" | "ok" | "muted" {
  if (suggestion === "disable") return "bad";
  if (suggestion === "keep") return "ok";
  return "muted";
}

export function SkillDoctorCard({ report, onOpenSkills }: { report: SkillDoctor; onOpenSkills: () => void }) {
  const t = useT();
  const num = useNum();
  return (
    <section aria-label={t("code.skillDoctor.title")} className="surface space-y-2 p-3 text-sm">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{t("code.skillDoctor.title")}</h3>
        <span className="text-muted-foreground tabular-nums">
          {t("code.skillDoctor.perTurn", { n: num(report.tokens_per_turn) })}
        </span>
      </header>

      {report.cards_read ? (
        <p className="text-xs text-muted-foreground tabular-nums">
          {t("code.skillDoctor.perTask", { n: num(report.cards_tokens_per_task_max) })}
        </p>
      ) : null}

      {report.skills.length === 0 ? (
        <p className="text-muted-foreground">{t("code.skillDoctor.none")}</p>
      ) : (
        <ul className="space-y-1">
          {report.skills.map((skill) => (
            <li
              key={`${skill.kind}:${skill.name}`}
              className="flex flex-wrap items-center gap-2 rounded-chip px-2 py-1"
            >
              <span className="font-mono text-xs">{skill.name}</span>
              <Badge tone="muted">{originLabel(t, skill.origin)}</Badge>
              <span className="text-xs text-muted-foreground">
                {skill.every_turn ? t("code.skillDoctor.kind.bundle") : t("code.skillDoctor.kind.card")}
              </span>
              <span className="text-xs tabular-nums">{num(skill.tokens)}</span>
              <span className="text-xs text-muted-foreground">{injectedLabel(t, skill, num)}</span>
              <Badge tone={suggestionTone(skill.suggestion)}>
                {t(SUGGESTION_KEY[skill.suggestion] ?? "code.skillDoctor.sugg.no_record")}
              </Badge>
            </li>
          ))}
        </ul>
      )}

      <footer className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">{t("code.skillDoctor.note")}</span>
        <Button variant="ghost" size="sm" onClick={onOpenSkills}>
          {t("code.skillDoctor.open")}
        </Button>
      </footer>
    </section>
  );
}
