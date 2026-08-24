import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkerCard } from "@/components/orchestration/WorkerCard";
import type { WorkerState } from "@/lib/orchestration-run";
import { renderWithProviders } from "@/test/utils";

/**
 * The card names the check that ran. It used to print the backend's enum.
 *
 * `stage` is one of `schema | criteria | spot | accepted`, and `accepted` means "no gate rejected"
 * — which for ordinary output is ONE gate. Criteria only exists when `output_format` carries
 * `regex:` lines, and that field is prose written by the decomposing model; the spot check needs
 * `evidence_refs`, which `build_envelope` fills only when the output overruns the 8000-character
 * cap. Both are skipped by construction.
 *
 * So the card read **"verificado · accepted"** — the word untranslated, in a pt-BR interface — over
 * a verdict that had checked shape and nothing else. Reproduced live: a worker cut off at a
 * 400-token cap returned the string "delegation budget exhausted: 1336/400 tokens", 44 characters,
 * zero evidence, and got that badge.
 */

function worker(over: Partial<WorkerState> = {}): WorkerState {
  return {
    taskId: "sub-1",
    objective: "Read index.html and say what it does.",
    status: "verified",
    stage: "accepted",
    checksRun: ["schema"],
    reasked: false,
    tokens: 1200,
    summaryChars: 400,
    gaps: [],
    evidenceRefs: [],
    tier: "mid",
    reason: "",
    detail: "",
    ...over,
  } as WorkerState;
}

describe("WorkerCard — what was checked", () => {
  it("says shape only when only the shape was checked", () => {
    renderWithProviders(<WorkerCard worker={worker()} />);

    expect(screen.getByText(/shape only|só o formato/i)).toBeTruthy();
    // The word the backend uses for "nothing rejected" must not reach the screen as a verdict.
    expect(screen.queryByText("accepted")).toBeNull();
  });

  it("says so when a real check ran", () => {
    renderWithProviders(<WorkerCard worker={worker({ checksRun: ["schema", "spot"] })} />);

    expect(screen.getByText(/spot check|amostragem/i)).toBeTruthy();
  });

  it("does not dress one gate as a pass", () => {
    // Tone carries as much as the word. A single gate is `muted`; more than one earns `ok`, which
    // is what the green a reader trusts should mean.
    const { container } = renderWithProviders(<WorkerCard worker={worker()} />);
    const badge = [...container.querySelectorAll("span")].find((s) =>
      /shape only|só o formato/i.test(s.textContent ?? ""),
    );

    expect(badge?.className).toContain("text-muted-foreground");
    expect(badge?.className).not.toContain("text-ok");
  });

  it("shows nothing rather than something wrong when no gate is reported", () => {
    // An older backend, a replayed run from disk, a frame that lost the field. Printing a badge
    // there would be inventing a verdict; the honest answer is to say nothing about it.
    renderWithProviders(<WorkerCard worker={worker({ checksRun: [] })} />);

    expect(screen.queryByText(/shape only|só o formato/i)).toBeNull();
    expect(screen.queryByText("accepted")).toBeNull();
  });
});
