import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { approveSkill, getSkills, retireSkill } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { SkillStat } from "@/lib/types";

function statusTone(status: string): "ok" | "accent" | "warn" | "muted" {
  if (status === "active") return "ok";
  if (status === "provisional") return "accent";
  if (status === "pending") return "warn";
  return "muted";
}

export function Skills() {
  const t = useT();
  const qc = useQueryClient();
  const skills = useQuery({ queryKey: ["skills"], queryFn: getSkills });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["skills"] });
  const approve = useMutation({ mutationFn: approveSkill, onSuccess: invalidate });
  const retire = useMutation({ mutationFn: retireSkill, onSuccess: invalidate });

  const rows: SkillStat[] = skills.data?.stats ?? [];
  const candidates = new Set(skills.data?.retirement_candidates ?? []);

  return (
    <Screen title={t("skills.title")} icon={<Sparkles className="h-5 w-5" />}>
      <Panel title={t("skills.learned")}>
        {skills.isLoading ? (
          <Spinner />
        ) : rows.length === 0 ? (
          <EmptyState text={t("skills.empty")} />
        ) : (
          rows.map((s) => (
            <div key={s.name} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-sm">{s.name}</span>
                  <Badge tone={statusTone(s.status)}>{s.status}</Badge>
                  {s.provenance === "tainted" && <Badge tone="warn">tainted</Badge>}
                  {candidates.has(s.name) && <Badge tone="bad">retire?</Badge>}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {t("skills.stats", { uses: s.uses, wins: s.successes })}
                  {s.rate != null && ` · ${Math.round(s.rate * 100)}%`}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                {s.status === "pending" && (
                  <Button size="sm" onClick={() => approve.mutate(s.name)}>
                    {t("common.approve")}
                  </Button>
                )}
                {s.status !== "retired" && (
                  <Button size="sm" variant="outline" onClick={() => retire.mutate(s.name)}>
                    {t("skills.retire")}
                  </Button>
                )}
              </div>
            </div>
          ))
        )}
      </Panel>
    </Screen>
  );
}
