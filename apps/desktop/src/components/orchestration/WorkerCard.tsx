import { Check, Loader2, Minus, X } from "lucide-react";

import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { WorkerState } from "@/lib/orchestration-run";
import { cn } from "@/lib/utils";

/**
 * One subtask, as a card.
 *
 * The colour rail down the left edge is the one idea worth borrowing from open-maestri, whose
 * canvas ropes go grey → green → red as agents talk. There the rope carries meaning (it is the
 * permission graph); here the states are the same four and a 2px border is the whole cost.
 *
 * This is close to `AgentCard` on the batch board and is deliberately not merged with it. The
 * payloads have nothing in common beyond being rectangles — one holds attempts, a diff and a verify
 * command, this holds a verification stage and an envelope's size — and a component with a union of
 * both props would be harder to read than two files.
 */
const rails: Record<WorkerState["status"], string> = {
  queued: "bg-muted",
  running: "bg-accent",
  verified: "bg-ok",
  rejected: "bg-bad",
};

/** Written out rather than built with a template: `i18n.reachable.test.ts` greps for each key as
 *  a literal and lists anything it cannot find as dead. Fourth time this file pattern is needed. */
/**
 * Why the worker was dropped, in the reader's language.
 *
 * Only `verifier` carries a `detail` — it is the verifier's own objection, already specific. Every
 * other reason is a machine enum with no text behind it, and the card used to fall through to
 * `worker.detail` for anything it did not name: an EMPTY red line above "discarded", saying a
 * worker was thrown away and nothing about why. Reasons live longer than the branches that read
 * them, so this is a map rather than a chain — a new one added to `RejectReason` and not here shows
 * up as a missing translation, not as a blank.
 */
const REJECTED_KEY: Partial<Record<string, "orch.worker.noOutput" | "orch.worker.deadline" | "orch.worker.cutOff.budget" | "orch.worker.cutOff.spend" | "orch.worker.cutOff.max_steps" | "orch.worker.cutOff.tool_loop" | "orch.worker.cutOff.cancelled">> = {
  no_output: "orch.worker.noOutput",
  deadline: "orch.worker.deadline",
  budget: "orch.worker.cutOff.budget",
  spend: "orch.worker.cutOff.spend",
  max_steps: "orch.worker.cutOff.max_steps",
  tool_loop: "orch.worker.cutOff.tool_loop",
  cancelled: "orch.worker.cutOff.cancelled",
};

/** The gate's own name, for the tooltip that lists what ran.
 *
 *  The badge was translated and its title was not: it interpolated the raw enum, so a Portuguese
 *  screen read "o que foi checado: schema". Same defect the badge itself had, one attribute over.
 */
const GATE_KEY: Record<string, "orch.worker.gate.schema" | "orch.worker.gate.criteria" | "orch.worker.gate.spot"> = {
  schema: "orch.worker.gate.schema",
  criteria: "orch.worker.gate.criteria",
  spot: "orch.worker.gate.spot",
};

const CHECK_KEY: Record<string, "orch.worker.checked.schema" | "orch.worker.checked.criteria" | "orch.worker.checked.spot"> = {
  schema: "orch.worker.checked.schema",
  criteria: "orch.worker.checked.criteria",
  spot: "orch.worker.checked.spot",
};

export function WorkerCard({ worker }: { worker: WorkerState }) {
  const t = useT();
  const rejected = REJECTED_KEY[worker.reason];

  const icon =
    worker.status === "running" ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />
    ) : worker.status === "verified" ? (
      <Check className="h-3.5 w-3.5 text-ok" />
    ) : worker.status === "rejected" ? (
      <X className="h-3.5 w-3.5 text-bad" />
    ) : (
      <Minus className="h-3.5 w-3.5 text-muted-foreground" />
    );

  return (
    <article className="surface relative overflow-hidden pl-4">
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-0.5 transition-colors duration-200 ease-out",
          rails[worker.status],
        )}
      />
      <div className="space-y-2 p-3">
        <div className="flex items-start gap-2">
          <span className="mt-0.5 shrink-0">{icon}</span>
          <p className="min-w-0 flex-1 text-sm text-foreground">
            {worker.objective || worker.taskId}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Badge
            tone={
              worker.status === "verified"
                ? "ok"
                : worker.status === "rejected"
                  ? "bad"
                  : worker.status === "running"
                    ? "accent"
                    : "muted"
            }
          >
            {t(`orch.worker.${worker.status}`)}
          </Badge>
          {/* Which tier answered it. A subtask small enough to run inline is handled by the top
              model and charged as such; showing every card as a mid-tier worker would misprice
              the run in the only place the user can see it. */}
          {worker.tier ? <Badge>{t(`orch.worker.tier.${worker.tier}`)}</Badge> : null}
          {/* WHAT was checked, not a raw backend enum. This rendered `worker.stage` — the string
              "accepted", untranslated, in a pt-BR interface — and "accepted" only ever meant "no
              gate rejected". For ordinary output that is ONE gate: criteria needs `regex:` lines in
              an `output_format` a model writes as prose, and the spot check needs evidence refs
              that `build_envelope` fills only above the 8000-character cap. So a card read
              "verificado · accepted" over a verdict that had checked shape and nothing else.
              Naming the gate is the difference between a claim and a receipt. */}
          {worker.status === "verified" && worker.checksRun.length ? (
            <Badge
              tone={worker.checksRun.length > 1 ? "ok" : "muted"}
              title={t("orch.worker.checked.title", {
           n: worker.checksRun.map((g) => t(GATE_KEY[g] ?? "orch.worker.gate.schema")).join(" + "),
         })}
            >
              {t(CHECK_KEY[worker.checksRun[worker.checksRun.length - 1]] ?? "orch.worker.checked.schema")}
            </Badge>
          ) : null}
          {worker.reasked ? <Badge tone="warn">{t("orch.worker.reasked")}</Badge> : null}
          {worker.tokens > 0 ? (
            <span className="text-xs tabular-nums text-muted-foreground">
              {t("orch.worker.tokens", { n: worker.tokens })}
            </span>
          ) : null}
        </div>

        {worker.status === "rejected" ? (
          <div className="space-y-1">
            <p className="text-xs text-bad-foreground">
              {rejected ? t(rejected) : worker.detail}
            </p>
            {/* The line that keeps the answer honest. Without it a user watches four workers,
                reads an answer built from three, and has no way to know one was dropped. */}
            <p className="text-xs text-muted-foreground">{t("orch.worker.discarded")}</p>
          </div>
        ) : null}

        {/* Titled, because unlabelled these were a list of yellow lines with no way to tell
            whether they described the task, the answer, or something that went wrong. They are
            the worker's own account of what it could not reach — which is what makes them worth
            reading beside an answer built on top of them. */}
        {worker.status === "verified" && worker.gaps.length > 0 ? (
          <div className="space-y-0.5">
            <p className="text-xs text-muted-foreground">{t("orch.worker.gaps")}</p>
            <ul className="space-y-0.5 text-xs text-warn-foreground">
              {worker.gaps.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* A ref exists only when the output did not FIT the cap, so this line is really saying
            that the summary the synthesis read is a slice, and here is where the rest of it is.
            The path is a real file, which is what makes it worth printing rather than a hash. */}
        {worker.evidenceRefs.length > 0 ? (
          <p className="break-all font-mono text-xs text-muted-foreground">
            {t("orch.worker.evidence", { refs: worker.evidenceRefs.join(", ") })}
          </p>
        ) : null}
      </div>
    </article>
  );
}
