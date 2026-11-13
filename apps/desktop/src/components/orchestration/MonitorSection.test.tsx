import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MonitorSection } from "@/components/orchestration/MonitorSection";
import type { OrchFrame } from "@/lib/api";
import {
  applyCrewFrame,
  applyFrame,
  EMPTY_CREW,
  EMPTY_RUN,
  type RunMonitor,
} from "@/lib/orchestration-run";
import { renderWithProviders } from "@/test/utils";

/**
 * The cross-agent monitor's section of a crew or hierarchy run (P1-11, sub-item 4).
 *
 * The server sent the `monitor` frame and wrote it to the run log, and no screen read it: both
 * reducers dropped it in their `default` branch, so a split flow flagged by the monitor never
 * reached the owner. These pin that both reducers keep it and that the screen keeps "not evaluated"
 * apart from "nothing found" — a monitor that read nothing must never look like a clean run.
 */

function frame(seq: number, data: Record<string, unknown>): OrchFrame {
  return { seq, kind: "monitor", task_id: "", text: "", data };
}

const OFF = { evaluated: false, reason: "governance_off", agents: 0, findings: [] };
const CLEAN = { evaluated: true, reason: "", agents: 3, findings: [] };
const FOUND = {
  evaluated: true,
  reason: "",
  agents: 2,
  findings: [
    {
      kind: "cross-agent-taint",
      detail: "untrusted content read by worker-1 may flow into worker-2",
      agents: ["worker-1", "worker-2"],
    },
  ],
};

describe("the monitor frame in the reducers", () => {
  it.each([OFF, CLEAN, FOUND])("the hierarchy keeps it (%#)", (data) => {
    const state = applyFrame(EMPTY_RUN, frame(5, data));
    expect(state.seq).toBe(5);
    expect(state.monitor?.evaluated).toBe(data.evaluated);
    expect(state.monitor?.reason).toBe(data.reason);
    expect(state.monitor?.agents).toBe(data.agents);
    expect(state.monitor?.findings).toEqual(data.findings);
  });

  it.each([OFF, CLEAN, FOUND])("the crew keeps it (%#)", (data) => {
    const state = applyCrewFrame(EMPTY_CREW, frame(5, data));
    expect(state.seq).toBe(5);
    expect(state.monitor?.evaluated).toBe(data.evaluated);
    expect(state.monitor?.findings).toEqual(data.findings);
  });

  it("a replayed monitor frame is ignored like any other", () => {
    const once = applyFrame(EMPTY_RUN, frame(5, FOUND));
    expect(applyFrame(once, frame(5, OFF))).toBe(once);
  });

  it("no frame, no section: a run that fell back has none", () => {
    expect(EMPTY_RUN.monitor).toBeNull();
    expect(EMPTY_CREW.monitor).toBeNull();
  });
});

describe("MonitorSection", () => {
  it("renders nothing before the frame", () => {
    renderWithProviders(<MonitorSection monitor={null} />);
    expect(screen.queryByTestId("run-monitor")).toBeNull();
  });

  it("governance off reads as not evaluated, never as nothing found", () => {
    renderWithProviders(<MonitorSection monitor={OFF as RunMonitor} />);
    const el = screen.getByTestId("run-monitor");
    expect(el.dataset.state).toBe("not-evaluated");
    expect(el.textContent).toMatch(/Not evaluated: governance is off/);
    expect(el.textContent).not.toMatch(/nothing found/);
  });

  it("no record kept reads as not evaluated too", () => {
    renderWithProviders(<MonitorSection monitor={{ ...OFF, reason: "no_ledger" }} />);
    const el = screen.getByTestId("run-monitor");
    expect(el.dataset.state).toBe("not-evaluated");
    expect(el.textContent).toMatch(/Not evaluated: no worker left a record/);
  });

  it("an evaluated run with nothing flagged says how many workers it read", () => {
    renderWithProviders(<MonitorSection monitor={CLEAN} />);
    const el = screen.getByTestId("run-monitor");
    expect(el.dataset.state).toBe("clean");
    expect(el.textContent).toMatch(/Checked across 3 workers: nothing found/);
  });

  it("a finding is named, with its workers, and says nothing was stopped", () => {
    renderWithProviders(<MonitorSection monitor={FOUND} />);
    const el = screen.getByTestId("run-monitor");
    expect(el.dataset.state).toBe("found");
    expect(el.textContent).toMatch(/nothing was stopped/);
    expect(el.textContent).toMatch(/One worker read outside content and another acted on it/);
    expect(el.textContent).toMatch(/worker-1, worker-2/);
  });
});
