import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkerCard } from "@/components/orchestration/WorkerCard";
import type { RejectReason, WorkerState } from "@/lib/orchestration-run";
import { renderWithProviders } from "@/test/utils";

/**
 * A dropped worker says WHY it was dropped, in the reader's language.
 *
 * The card named two reasons — `no_output` and `deadline` — and fell through to `worker.detail` for
 * anything else. Only `verifier` fills that field: it is the verifier's own objection, and it is
 * the only rejection with text behind it. Every other reason is a bare enum, so the card rendered
 * an EMPTY red line above "discarded from the answer" — telling a reader their worker was thrown
 * away and nothing whatsoever about why.
 *
 * It was invisible while the only unnamed reasons were unreachable. Then two changes made them
 * ordinary: cut-off workers stopped being folded into `no_output` (they carry `budget`,
 * `max_steps`, `tool_loop`), and Stop began abandoning the wait with `cancelled`. Both landed in
 * the blank.
 */

function worker(over: Partial<WorkerState> = {}): WorkerState {
  return {
    taskId: "sub-1",
    objective: "Read index.html and say what it does.",
    status: "rejected",
    stage: "",
    checksRun: [],
    reasked: false,
    tokens: 1336,
    summaryChars: 44,
    gaps: [],
    evidenceRefs: [],
    tier: "mid",
    reason: "",
    detail: "",
    ...over,
  } as WorkerState;
}

/** Every reason the backend can send, and what a reader must be able to read on the card. */
const REASONS: ReadonlyArray<[RejectReason, RegExp]> = [
  ["no_output", /had nothing to say|não tinha o que dizer/i],
  ["deadline", /deadline|prazo/i],
  ["budget", /budget|orçamento/i],
  ["max_steps", /step limit|limite de passos/i],
  ["tool_loop", /same tool|mesma ferramenta/i],
  ["cancelled", /ended the run|você parou/i],
];

describe("WorkerCard — a dropped worker says why", () => {
  it.each(REASONS)("names %s", (reason, expected) => {
    renderWithProviders(<WorkerCard worker={worker({ reason })} />);

    expect(screen.getByText(expected)).toBeTruthy();
  });

  it("leaves no reason rendering as a blank line", () => {
    // The property, rather than the six examples above: whatever the backend sends, the red line
    // has words in it. This is what would have caught the original defect the day it was created —
    // `budget` arrived from the backend with no branch here and nothing failed.
    for (const [reason] of REASONS) {
      const { container, unmount } = renderWithProviders(<WorkerCard worker={worker({ reason })} />);
      const lines = [...container.querySelectorAll("p")].filter((p) =>
        p.className.includes("text-bad-foreground"),
      );

      expect(lines.length, `${reason}: no rejection line at all`).toBe(1);
      expect(lines[0].textContent?.trim(), `${reason}: rendered an empty line`).not.toBe("");
      unmount();
    }
  });

  it("still prefers the verifier's own words when it has them", () => {
    // Guarding the guard. A map that answered for every reason would swallow the one rejection that
    // carries something specific — "claimed a file it never read" beats any fixed sentence.
    renderWithProviders(
      <WorkerCard worker={worker({ reason: "verifier", detail: "claimed a file it never read" })} />,
    );

    expect(screen.getByText(/claimed a file it never read/i)).toBeTruthy();
  });
});
