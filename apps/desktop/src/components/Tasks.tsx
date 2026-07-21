import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KanbanSquare, ShieldAlert } from "lucide-react";
import { approveProject, denyProject, getKanban, getProjects } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT } from "@/lib/i18n";
import type { ProjectState, TaskCard } from "@/lib/types";

const COLUMN_ORDER = ["backlog", "doing", "review", "blocked", "done"];

function statusTone(s: string): "ok" | "accent" | "warn" | "bad" | "muted" {
  if (s === "done") return "ok";
  if (s === "running") return "accent";
  if (s === "awaiting_approval") return "warn";
  if (s === "escalated") return "bad";
  return "muted";
}

function ProjectRow({ p, onChange }: { p: ProjectState; onChange: () => void }) {
  const t = useT();
  const approve = useMutation({
    mutationFn: (card?: string) => approveProject(p.id, card),
    onSuccess: onChange,
  });
  const deny = useMutation({ mutationFn: (card: string) => denyProject(p.id, card), onSuccess: onChange });
  const awaiting = p.status === "awaiting_approval";

  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="truncate font-mono text-sm">{p.id}</span>
        <Badge tone={statusTone(p.status)}>{p.status.replace("_", " ")}</Badge>
        <span className="text-xs text-muted-foreground">
          {t("tasks.iter", { a: p.iterations, b: p.max_iterations })}
        </span>
      </div>
      {p.note && <div className="mt-1 text-xs text-muted-foreground">{p.note}</div>}
      {awaiting && (
        <div className="mt-2 flex items-center gap-2 rounded-lg border border-[hsl(38_92%_50%/0.4)] bg-[hsl(38_92%_50%/0.08)] px-3 py-2 shadow-inset">
          <ShieldAlert className="h-4 w-4 text-[hsl(38_92%_42%)]" />
          <span className="flex-1 text-xs">
            {p.pending_card_id
              ? t("tasks.awaitingStep", { card: p.pending_card_id })
              : t("tasks.awaitingPlan")}
          </span>
          <Button size="sm" onClick={() => approve.mutate(p.pending_card_id ?? undefined)}>
            {p.pending_card_id ? t("tasks.approveStep") : t("tasks.approvePlan")}
          </Button>
          {p.pending_card_id && (
            <Button size="sm" variant="outline" onClick={() => deny.mutate(p.pending_card_id!)}>
              {t("tasks.deny")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function Board({ columns }: { columns: Record<string, TaskCard[]> }) {
  const t = useT();
  const cols = COLUMN_ORDER.filter((c) => (columns[c]?.length ?? 0) > 0);
  if (cols.length === 0) return <EmptyState text={t("tasks.boardEmpty")} />;
  return (
    <div className="flex gap-3 overflow-x-auto p-3">
      {cols.map((col) => (
        <div key={col} className="w-56 shrink-0">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {col} · {columns[col].length}
          </div>
          <div className="space-y-2">
            {columns[col].map((c) => (
              <div
                key={c.id}
                className={`rounded-lg border border-white/5 bg-card px-3 py-2.5 shadow-elev transition hover:brightness-105 ${
                  c.risk === "high" ? "ring-1 ring-bad/30" : ""
                }`}
              >
                <div className="text-sm">{c.title}</div>
                {c.risk === "high" && (
                  <div className="mt-1.5">
                    <Badge tone="bad">{t("tasks.highRisk")}</Badge>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Tasks() {
  const t = useT();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const kanban = useQuery({ queryKey: ["kanban"], queryFn: getKanban });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["kanban"] });
  };

  return (
    <Screen title={t("tasks.title")} icon={<KanbanSquare className="h-5 w-5" />}>
      <Panel title={t("tasks.projects")}>
        {projects.isError ? (
          <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
        ) : projects.isLoading ? (
          <Spinner />
        ) : !projects.data || projects.data.length === 0 ? (
          <EmptyState text={t("tasks.projectsEmpty")} />
        ) : (
          projects.data.map((p) => <ProjectRow key={p.id} p={p} onChange={refresh} />)
        )}
      </Panel>

      <Panel title={t("tasks.board")}>
        {kanban.isError ? (
          <ErrorState error={kanban.error} onRetry={() => kanban.refetch()} />
        ) : kanban.isLoading ? (
          <Spinner />
        ) : (
          <Board columns={kanban.data ?? {}} />
        )}
      </Panel>
    </Screen>
  );
}
