import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KanbanSquare, Play, Plus, ShieldAlert, Square, Trash2 } from "lucide-react";
import { addKanbanCard, approveProject, denyProject, getAgentRegistry, getKanban, getProject, getProjects, moveKanbanCard, removeKanbanCard, startProject, stepProject, streamKanbanRun } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { ProjectState, TaskCard } from "@/lib/types";

const COLUMN_ORDER = ["backlog", "doing", "review", "blocked", "done"];

function statusTone(s: string): "ok" | "accent" | "warn" | "bad" | "muted" {
  if (s === "done") return "ok";
  if (s === "running") return "accent";
  if (s === "awaiting_approval") return "warn";
  if (s === "escalated") return "bad";
  return "muted";
}

function ProjectRow({
  p,
  onChange,
  selected,
  onSelect,
  onStep,
  stepping,
}: {
  p: ProjectState;
  onChange: () => void;
  selected: boolean;
  onSelect: () => void;
  onStep: () => void;
  stepping: boolean;
}) {
  const t = useT();
  const approve = useMutation({
    mutationFn: (card?: string) => approveProject(p.id, card),
    onSuccess: onChange,
  });
  const deny = useMutation({ mutationFn: (card: string) => denyProject(p.id, card), onSuccess: onChange });
  const awaiting = p.status === "awaiting_approval";

  return (
    <div className={cn("px-4 py-3", selected && "bg-surface-2")}>
      <div className="flex items-center gap-2">
        {/* Selecting a project filters the board below to its own cards — which is what
            `/api/projects/{id}` returns and what nothing was calling. */}
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          className={cn("truncate font-mono text-sm hover:text-accent-ink", focusRing)}
        >
          {p.id}
        </button>
        <Badge tone={statusTone(p.status)}>{p.status.replace("_", " ")}</Badge>
        <span className="flex-1 text-xs text-muted-foreground">
          {t("tasks.iter", { a: p.iterations, b: p.max_iterations })}
        </span>
        {/* One iteration per press, and the label says so rather than promising completion. A
            project is finished when its SPEC says so, which can take several — and a button that
            implied otherwise would be making a claim only the spec gets to make.
            Absent once done: `done` is terminal, and offering to advance it invites someone to
            restart work they already accepted. */}
        {p.status !== "done" && !awaiting && (
          <Button size="sm" variant="ghost" disabled={stepping} onClick={onStep}>
            <Play className="h-3.5 w-3.5" /> {t("tasks.step")}
          </Button>
        )}
      </div>
      {p.note && <div className="mt-1 text-xs text-muted-foreground">{p.note}</div>}
      {awaiting && (
        <div className="mt-2 flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 shadow-inset">
          <ShieldAlert className="h-4 w-4 text-warn-foreground" />
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

/** Add a card, and say who works it.
 *
 * The lane is the interesting field and it is a free text box rather than a list of your agents:
 * `lane` has always been a plain string, an agent that does not exist yet is a perfectly good thing
 * to file work against (the card waits in the backlog until it does), and a dropdown would quietly
 * make "the agents you have right now" the limit of what you can plan for.
 */
/** Lanes the server always registers, whatever the agent registry holds.
 *
 *  Duplicated from `chimera/api/features.py`, and `tests/test_builtin_lanes_agree.py` fails if the
 *  two ever disagree — the server is the source of truth, this is a copy that cannot drift quietly.
 */
export const BUILT_IN_LANES = ["solve", "crew"];

function AddCard({
  onAdd,
  known,
}: {
  onAdd: (card: { title: string; lane: string }) => void;
  known: string[];
}) {
  const t = useT();
  const listId = useId();
  const [title, setTitle] = useState("");
  const [lane, setLane] = useState("solve");
  // Suggested, not enforced. A datalist offers the agents that exist without closing the field to
  // an agent that does not exist yet — which is the whole reason the lane is text and not a select.
  //
  // BUILT_IN_LANES is here because this warned about its own default. The field starts on "solve",
  // `known` is the AGENT registry, and a built-in lane is never in it — so the moment you registered
  // your first agent, a form you had not touched started saying the one lane that always works does
  // not exist. Backwards twice over: adding an agent is what triggered it, and the accusation was
  // aimed at the runner with no way to be missing.
  const trimmed = lane.trim();
  const unknown =
    trimmed !== "" && known.length > 0 && !known.includes(trimmed) && !BUILT_IN_LANES.includes(trimmed);

  return (
    <form
      className="flex flex-wrap items-center gap-2 px-3 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        const value = title.trim();
        if (!value) return;
        onAdd({ title: value, lane: lane.trim() || "solve" });
        setTitle("");
      }}
    >
      <input
        className="field h-7 min-w-48 flex-1 px-2 text-xs"
        placeholder={t("tasks.newCard")}
        aria-label={t("tasks.newCard")}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <input
        className="field h-7 w-32 px-2 font-mono text-xs"
        placeholder="solve"
        aria-label={t("tasks.lane")}
        list={listId}
        value={lane}
        onChange={(e) => setLane(e.target.value)}
      />
      <datalist id={listId}>
        {known.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
      {/* Said before the dispatch, not after it. The old first news that a lane had no runner was
          "0 worked", printed once the run was already over. */}
      {unknown && (
        <span className="text-xs text-warn-foreground">{t("tasks.laneUnregistered")}</span>
      )}
      <Button size="sm" type="submit" disabled={!title.trim()}>
        <Plus className="h-3.5 w-3.5" /> {t("tasks.add")}
      </Button>
    </form>
  );
}

function Board({
  columns,
  onMove,
  onRemove,
}: {
  columns: Record<string, TaskCard[]>;
  onMove: (id: string, column: string) => void;
  onRemove: (id: string) => void;
}) {
  const t = useT();
  const cols = COLUMN_ORDER.filter((c) => (columns[c]?.length ?? 0) > 0);
  if (cols.length === 0) return <EmptyState text={t("tasks.boardEmpty")} />;
  return (
    <div className="flex gap-3 overflow-x-auto p-3">
      {cols.map((col) => (
        <div key={col} className="w-56 shrink-0">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {/* Translated. These were rendered raw, so a board in Portuguese read "backlog /
                doing / review / blocked / done" — and so did one in Japanese. The card's `lane`
                below stays raw, deliberately: that is an agent id, the literal string the
                dispatcher matches on, and translating it would name something that does not
                exist. */}
            {t(`tasks.column.${col}`)} · {columns[col].length}
          </div>
          <div className="space-y-2">
            {columns[col].map((c) => (
              <div
                key={c.id}
                className={`group/card rounded-lg border border-hairline bg-card px-3 py-2.5 shadow-elev transition hover:brightness-105 ${
                  c.risk === "high" ? "ring-1 ring-bad/30" : ""
                }`}
              >
                <div className="text-sm">{c.title}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {/* Which agent picks this up is the question a board is asked, so it is on the
                      card rather than behind it. */}
                  <Badge tone="muted">{c.lane}</Badge>
                  {c.verify && <Badge tone="accent">{t("tasks.verified")}</Badge>}
                  {c.risk === "high" && <Badge tone="bad">{t("tasks.highRisk")}</Badge>}
                </div>
                <div className="mt-1.5 flex items-center gap-1 opacity-0 focus-within:opacity-100 group-hover/card:opacity-100">
                  <select
                    className="field h-6 flex-1 px-1 text-xs"
                    aria-label={t("tasks.moveCard", { title: c.title })}
                    value={c.column}
                    onChange={(e) => onMove(c.id, e.target.value)}
                  >
                    {COLUMN_ORDER.map((target) => (
                      <option key={target} value={target}>
                        {t(`tasks.column.${target}`)}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    aria-label={t("tasks.removeCard", { title: c.title })}
                    className="px-1 text-muted-foreground hover:text-bad"
                    onClick={() => onRemove(c.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Start a project from a spec.
 *
 * The field is a PATH, not spec text. The spec is the acceptance authority — the only thing that
 * decides whether the project is done — so it belongs in the repository, versioned and reviewable.
 * Writing one is a job for the coding conversation; pointing a project at it is this.
 */
function StartProject({ onStart }: { onStart: (spec: string) => void }) {
  const t = useT();
  const [spec, setSpec] = useState("");
  return (
    <form
      className="flex flex-wrap items-center gap-2 px-3 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        const value = spec.trim();
        if (!value) return;
        onStart(value);
        setSpec("");
      }}
    >
      <input
        className="field h-7 min-w-48 flex-1 px-2 font-mono text-xs"
        placeholder={t("tasks.specPath")}
        aria-label={t("tasks.specPath")}
        value={spec}
        onChange={(e) => setSpec(e.target.value)}
      />
      <Button size="sm" type="submit" disabled={!spec.trim()}>
        <Plus className="h-3.5 w-3.5" /> {t("tasks.startProject")}
      </Button>
    </form>
  );
}

const WORKERS_KEY = "chimera.kanban.workers";

/** The board's live dispatch: start it, stop it, and say what it is doing while it runs.
 *
 * A dispatch calls models for as long as the backlog has cards, so the button has to be a Stop as
 * well as a Play — and the board is refetched as each card lands rather than once at the end, since
 * watching a column empty is the whole reason someone presses it and stays.
 */
function useDispatch(onCard: () => void) {
  const t = useT();
  const [running, setRunning] = useState(false);
  const [line, setLine] = useState("");
  const [conflicts, setConflicts] = useState<string[]>([]);
  // Remembered, because the answer is a property of this board and this machine, not of one press.
  const [workers, setWorkers] = useState(() => Number(localStorage.getItem(WORKERS_KEY) ?? 1) || 1);
  const abort = useRef<AbortController | null>(null);

  const changeWorkers = (n: number) => {
    setWorkers(n);
    try {
      localStorage.setItem(WORKERS_KEY, String(n));
    } catch {
      /* private mode — the choice just does not survive the launch */
    }
  };

  const toggle = () => {
    if (running) {
      abort.current?.abort();
      abort.current = null;
      setRunning(false);
      setLine("");
      return;
    }
    const controller = new AbortController();
    abort.current = controller;
    setRunning(true);
    setLine(t("tasks.dispatching"));
    setConflicts([]);
    void streamKanbanRun(
      { workers },
      {
        onCard: (outcome) => {
          setLine(`${outcome.card_id} → ${outcome.moved_to}`);
          onCard();
        },
        // Kept on screen after the run: those cards succeeded and not all of their edits survived
        // the merge, which is the one thing a green board would otherwise hide.
        onConflict: setConflicts,
        onDone: (summary) => {
          setRunning(false);
          // The count of cards actually WORKED, which is not the count that was queued: a card
          // whose lane has no runner waits rather than failing, and saying "0 worked" is how
          // someone learns their agent is not registered.
          setLine(summary.error ?? t("tasks.dispatched", { n: summary.worked }));
          onCard();
        },
        onError: (message) => {
          setRunning(false);
          setLine(message);
        },
      },
      controller.signal,
    );
  };

  return { running, line, toggle, workers, changeWorkers, conflicts };
}

export function Tasks({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const refetchBoard = () => {
    void qc.invalidateQueries({ queryKey: ["kanban"] });
  };
  const add = useMutation({ mutationFn: addKanbanCard, onSuccess: refetchBoard });
  const move = useMutation({
    mutationFn: ({ id, column }: { id: string; column: string }) => moveKanbanCard(id, column),
    onSuccess: refetchBoard,
  });
  const remove = useMutation({ mutationFn: removeKanbanCard, onSuccess: refetchBoard });
  const refetchProjects = () => {
    void qc.invalidateQueries({ queryKey: ["projects"] });
    refetchBoard();
  };
  const start = useMutation({
    mutationFn: (spec: string) => startProject({ spec }),
    onSuccess: refetchProjects,
  });
  // One call per iteration, and the button says so. A "run to completion" button over an endpoint
  // that cannot be interrupted would be a promise this screen could not keep.
  const step = useMutation({ mutationFn: stepProject, onSuccess: refetchProjects });
  const dispatch = useDispatch(refetchBoard);
  const kanban = useQuery({ queryKey: ["kanban"], queryFn: getKanban });
  // The ids the board can actually dispatch to. A failure here is not the board's problem: the
  // suggestions simply go away and the field keeps taking whatever you type.
  const registry = useQuery({ queryKey: ["agent-registry"], queryFn: getAgentRegistry });
  const knownAgents = (registry.data ?? []).map((a) => a.id);
  // Which project's board to show. `null` is the global board — every card from every project,
  // which is the right default and a poor way to follow one piece of work.
  const [selected, setSelected] = useState<string | null>(null);
  const project = useQuery({
    queryKey: ["project", selected],
    queryFn: () => getProject(selected!),
    enabled: selected !== null,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["kanban"] });
  };

  return (
    <Screen title={t("tasks.title")} icon={<KanbanSquare className="h-5 w-5" />} embedded={embedded}>
      <Panel title={t("tasks.projects")}>
        {projects.isError ? (
          <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
        ) : projects.isLoading ? (
          <Spinner />
        ) : !projects.data || projects.data.length === 0 ? (
          <>
            <StartProject onStart={(spec) => start.mutate(spec)} />
            <EmptyState text={t("tasks.projectsEmpty")} />
          </>
        ) : (
          projects.data.map((p) => (
            <ProjectRow
              key={p.id}
              p={p}
              onChange={refresh}
              onStep={() => step.mutate(p.id)}
              stepping={step.isPending}
              selected={selected === p.id}
              // Clicking the selected project clears the filter, so there is always a way back to
              // the whole board without hunting for a "show all" control.
              onSelect={() => setSelected((cur: string | null) => (cur === p.id ? null : p.id))}
            />
          ))
        )}
        {projects.data && projects.data.length > 0 && (
          <StartProject onStart={(spec) => start.mutate(spec)} />
        )}
      </Panel>

      <Panel
        title={
          selected
            ? `${t("tasks.board")} · ${selected}`
            : t("tasks.board")
        }
        action={
          // Only on the whole board: a project's cards are dispatched by the project loop, which
          // has its own approval gate. A Run button here would step around it.
          selected ? undefined : (
            <div className="flex items-center gap-2">
              {dispatch.line && (
                <span className="max-w-56 truncate text-xs text-muted-foreground">
                  {dispatch.line}
                </span>
              )}
              {/* How many cards at once. Hidden while running, because changing it mid-dispatch
                  would set a number for the next run and read as if it applied to this one. */}
              {!dispatch.running && (
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  {t("tasks.workers")}
                  <select
                    className="rounded-chip border border-hairline bg-transparent px-1.5 py-0.5"
                    value={dispatch.workers}
                    onChange={(e) => dispatch.changeWorkers(Number(e.target.value))}
                  >
                    {[1, 2, 3, 4, 6, 8].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <Button size="sm" variant={dispatch.running ? "outline" : "ghost"} onClick={dispatch.toggle}>
                {dispatch.running ? (
                  <>
                    <Square className="h-3.5 w-3.5" /> {t("common.stop")}
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5" /> {t("tasks.run")}
                  </>
                )}
              </Button>
            </div>
          )
        }
      >
        {kanban.isError ? (
          <ErrorState error={kanban.error} onRetry={() => kanban.refetch()} />
        ) : kanban.isLoading || (selected && project.isLoading) ? (
          <Spinner />
        ) : (
          <>
            {/* Cards are added to the board, never to a project: a project's cards come from its
                spec, and one typed in by hand would be work its drift check does not know about. */}
            {dispatch.conflicts.length > 0 && (
              <p className="mb-3 rounded-chip border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
                {t("tasks.conflicts", { n: dispatch.conflicts.length })}{" "}
                <span className="font-mono">{dispatch.conflicts.slice(0, 4).join(", ")}</span>
              </p>
            )}
            {!selected && <AddCard onAdd={(card) => add.mutate(card)} known={knownAgents} />}
            <Board
              columns={(selected ? project.data?.columns : kanban.data) ?? {}}
              onMove={(id, column) => move.mutate({ id, column })}
              onRemove={(id) => remove.mutate(id)}
            />
          </>
        )}
      </Panel>
    </Screen>
  );
}
