import { useState } from "react";

import type { CrewRunInput } from "@/lib/api";

import { CrewForm } from "./CrewForm";
import { CrewRun } from "./CrewRun";

/**
 * Pick the crew, then watch it.
 *
 * Lifted out of `Orchestration`, where a crew could only be reached by first asking for a plan and
 * having the classifier hand you back a fallback note. That is a good recommendation and it was a
 * bad only-door: the crew is the right answer for most write-shaped work, so the most common answer
 * cost a top-model call to arrive at, and you could not simply ask for it.
 *
 * Two states, not one: the form is open (you are choosing the crew) or a crew is running. Collapsed
 * into one they would make the form vanish the instant Run is pressed, taking the roles you just
 * wrote with it — and that is exactly the state you want back when the check turns out to be the
 * thing that was wrong.
 */
export function CrewLauncher({
  task,
  verify,
  workspace,
  onBusy,
}: {
  task: string;
  /** The shared check command. It belongs to the console above rather than to this form, because it
   *  is a shell command run against the result — the same field the run and the lifecycle send. */
  verify: string;
  workspace: string;
  onBusy: (busy: boolean) => void;
}) {
  // Keyed by when it was confirmed, so a second crew is a new component rather than a re-run of
  // the last one's reducer.
  const [crew, setCrew] = useState<{ at: number; request: CrewRunInput } | null>(null);

  return (
    <div className="space-y-5">
      {crew ? null : (
        <CrewForm
          task={task}
          onRun={(workers) =>
            setCrew({
              at: Date.now(),
              request: { task, workspace, workers, verify: verify.trim() || null },
            })
          }
        />
      )}
      {crew ? <CrewRun key={crew.at} request={crew.request} onBusy={onBusy} /> : null}
    </div>
  );
}
