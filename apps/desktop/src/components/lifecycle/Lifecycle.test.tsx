import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { STAGES, stageState } from "@/components/lifecycle/Lifecycle";
import { TaskConsole } from "@/components/work/TaskConsole";
import { cancelLifecycle, streamLifecycle } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  streamLifecycle: vi.fn(),
  cancelLifecycle: vi.fn(),
}));

type Handlers = Parameters<typeof streamLifecycle>[1];

/** Drive the stream by hand: the handlers are the whole contract with the route. */
function feed(script: (h: Handlers) => void | Promise<void>) {
  vi.mocked(streamLifecycle).mockImplementation(async (_req, h) => {
    await script(h);
  });
}

const stage = (name: string, passed = true, output = "") => ({ name, output, passed });

/**
 * plan → build → test → review, watched rather than waited for.
 *
 * `LifecycleCrew` has been working and tested for a long time and only the CLI could reach it. What
 * it adds over an ordinary run is that the test gate is a step you can see fail — so a screen that
 * collected the four stages and showed them together at the end would have shipped the capability
 * and thrown away the reason for it.
 */
describe("the lifecycle screen", () => {
  beforeEach(() => {
    vi.mocked(streamLifecycle).mockReset();
    vi.mocked(cancelLifecycle).mockReset().mockResolvedValue({ ok: true } as never);
  });

  it("shows all four stages before any of them has run", async () => {
    // Named up front, not accumulated. A list that grows a row at a time says where you are and
    // never how far there is to go, and the build stage alone can take minutes.
    feed(() => {});
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);

    await userEvent.type(screen.getByLabelText(/the task/i), "add a greeting");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    // Scoped to the stage list. Unscoped, "plan" and "test" also match the sentence above the
    // task box that says what this mode does — which is prose about the stages, not the stages.
    const stages = within(await screen.findByRole("list"));
    for (const name of STAGES) {
      expect(stages.getByText(new RegExp(name, "i"))).toBeTruthy();
    }
  });

  it("renders a stage as it lands, not at the end", async () => {
    let handlers: Handlers | null = null;
    vi.mocked(streamLifecycle).mockImplementation(
      async (_req, h) =>
        new Promise<void>(() => {
          handlers = h;
        }),
    );
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(handlers).not.toBeNull());
    handlers!.onStage?.(stage("plan", true, "1. write the greeting"));

    // The stream has not ended, and the plan is already on screen.
    expect(await screen.findByText(/write the greeting/)).toBeTruthy();
  });

  it("says what will judge the run before any work starts", async () => {
    // "No verify command — a model reads the answer" has always been true whenever the box was
    // empty, and a screen that does not say so lets an approving paragraph pass for a passing test.
    feed((h) => h.onVerify?.({ command: "", source: "none" } as never));
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(await screen.findByText(/a model/i)).toBeTruthy();
  });

  it("names the command when there is one", async () => {
    // The control. Two branches that print the same thing carry no information.
    feed((h) => h.onVerify?.({ command: "pytest -q", source: "you" } as never));
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(await screen.findByText(/pytest -q/)).toBeTruthy();
  });

  it("distinguishes a stop from a failure", async () => {
    // Reporting a halt as "did not pass" tells somebody their code is broken when nothing ever
    // tested it. Two different facts, and the wrong one is actively misleading.
    feed((h) => {
      h.onStage?.(stage("plan"));
      h.onDone?.({ success: false, answer: "", cancelled: true });
    });
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(await screen.findByText(/nothing judged this work/i)).toBeTruthy();
    expect(screen.queryByText(/did not pass its test/i)).toBeNull();
  });

  it("says plainly when the work failed its test", async () => {
    feed((h) => h.onDone?.({ success: false, answer: "", cancelled: false }));
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(await screen.findByText(/did not pass its test/i)).toBeTruthy();
  });

  it("can stop a run, and targets the run it started", async () => {
    let handlers: Handlers | null = null;
    vi.mocked(streamLifecycle).mockImplementation(
      async (_req, h) =>
        new Promise<void>(() => {
          handlers = h;
        }),
    );
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));
    await waitFor(() => expect(handlers).not.toBeNull());
    handlers!.onRunId?.("abc123");

    await userEvent.click(await screen.findByRole("button", { name: /stop/i }));

    expect(cancelLifecycle).toHaveBeenCalledWith("abc123");
  });

  it("says what Stop can and cannot do", async () => {
    // An in-flight model call cannot be interrupted and is billed. A Stop button that implies
    // otherwise is a promise the machinery does not keep.
    vi.mocked(streamLifecycle).mockImplementation(async () => new Promise<void>(() => {}));
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(await screen.findByText(/already in flight finishes and is billed/i)).toBeTruthy();
  });

  it("runs in the project folder it was given", async () => {
    feed(() => {});
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(streamLifecycle).toHaveBeenCalled());
    expect(vi.mocked(streamLifecycle).mock.calls[0][0].workspace).toBe("/ws");
  });

  it("surfaces an error instead of pretending the run is still going", async () => {
    feed((h) => h.onError?.("the run failed"));
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/the task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /start/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/the run failed/i);
  });

  it("will not start without a task", () => {
    renderWithProviders(<TaskConsole workspace="/ws" initialMode="lifecycle" onOpenCode={() => {}} />);
    expect(screen.getByRole("button", { name: /start/i })).toHaveProperty("disabled", true);
  });
});

describe("stageState", () => {
  it("marks the first unreported stage as the one being worked", () => {
    // The crew is strictly sequential, so this is a fact about the pipeline, not a guess about
    // the clock — and it is what lets the screen show progress during the long build stage, when
    // no frame arrives for minutes.
    expect(stageState("build", [stage("plan")], true)).toBe("active");
    expect(stageState("test", [stage("plan")], true)).toBe("waiting");
  });

  it("shows a failed stage as failed even though later stages still ran", () => {
    // `review` runs after a failed `test` on purpose — its opinion is advisory and the executable
    // gate already decided. Colouring the whole run by its last stage would report a failed build
    // as fine.
    expect(stageState("test", [stage("test", false)], false)).toBe("failed");
  });

  it("does not claim anything is working once the run has ended", () => {
    // A spinner left on a stage that never reported is a screen saying work continues after the
    // stream closed.
    expect(stageState("review", [stage("plan")], false)).toBe("waiting");
  });
});
