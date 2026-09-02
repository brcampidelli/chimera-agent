import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskConsole } from "@/components/work/TaskConsole";
import { previewHierarchy, streamCrew, streamLifecycle, streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { HierarchyPreview } from "@/lib/types";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** A write-shaped task: what `classify_task` sends down the single-agent path, and what the crew
 *  exists for. The preview's own verdict, which is now allowed to move the mode strip. */
const WRITE_PLAN: HierarchyPreview = {
  shape: "sequential_write",
  profitable_estimate: false,
  estimate_margin: 0,
  would_fall_back: true,
  fell_back_reason: "shape",
  subtasks: [],
  workers: 0,
  budget_per_worker: 0,
  sources: 0,
  plan_id: "",
  decompose_spent: false,
};

function render(mode?: "single" | "lifecycle" | "hierarchy" | "crew") {
  renderWithProviders(
    <TaskConsole workspace="/repo" initialMode={mode} onOpenCode={() => {}} />,
  );
  return userEvent.setup();
}

const task = () => screen.getByLabelText(/the task/i);
const check = () => screen.getByLabelText(/the check/i);
const mode = (name: RegExp) => screen.getByRole("radio", { name });

/**
 * One task, typed once, and buttons for what happens to it.
 *
 * Four ways to run a task were four screens, each asking for the task again — so the choice of how
 * came before the task existed, and trying the second way meant retyping. These are the properties
 * that only exist because the four became one, and each of them is a way the merge could go wrong
 * quietly: a field that looks shared and is not, a field that is dropped without saying so, a mode
 * change that kills a run somebody is paying for.
 */
describe("the task console", () => {
  beforeEach(() => {
    vi.mocked(streamRun).mockReset().mockImplementation(async () => {});
    vi.mocked(streamLifecycle).mockReset().mockImplementation(async () => {});
    vi.mocked(streamCrew).mockReset().mockImplementation(() => new Promise<void>(() => {}));
    vi.mocked(previewHierarchy).mockReset();
  });

  it("keeps what was typed when the mode changes", async () => {
    const user = render();

    await user.type(task(), "conserte o carrinho");
    await user.type(check(), "pytest -q");
    await user.click(mode(/four stages/i));

    // The whole point of the merge. Four screens each with their own box meant the second way to
    // run a task cost typing it a second time, which is why nobody tried the second way.
    expect(task()).toHaveValue("conserte o carrinho");
    expect(check()).toHaveValue("pytest -q");
  });

  it("hands the task and the check to whichever mode runs them", async () => {
    const user = render();

    await user.type(task(), "conserte o carrinho");
    await user.type(check(), "pytest -q");
    await user.click(mode(/four stages/i));
    await user.click(screen.getByRole("button", { name: /start/i }));

    // Typed under one mode and sent by another. Without this the fields would look shared and be
    // four copies again — the same screen, the same defect, one indirection further down.
    await waitFor(() => expect(streamLifecycle).toHaveBeenCalled());
    expect(vi.mocked(streamLifecycle).mock.calls[0][0]).toMatchObject({
      task: "conserte o carrinho",
      verify: "pytest -q",
      workspace: "/repo",
    });
  });

  it("changing the mode starts nothing", async () => {
    // The control for the two above. A console that ran something on every mode click would pass
    // "the task survived" and cost money doing it.
    const user = render();

    await user.type(task(), "conserte o carrinho");
    await user.click(mode(/four stages/i));
    await user.click(mode(/one agent/i));

    expect(streamRun).not.toHaveBeenCalled();
    expect(streamLifecycle).not.toHaveBeenCalled();
  });

  it("does not offer a check to the mode that cannot run one", async () => {
    const user = render();

    await user.click(mode(/split it up/i));

    // The hierarchy mounts its workers tool-free — they read and answer and never touch a file.
    // A check field there would be a control with nowhere to send what you put in it.
    expect(screen.queryByLabelText(/the check/i)).toBeNull();
  });

  it("says the check is not sent, rather than dropping it in silence", async () => {
    const user = render();

    await user.type(check(), "pytest -q");
    await user.click(mode(/split it up/i));

    // A field vanishing with text in it looks like the text went somewhere. This is the one mode
    // where it does not, and a form that implies it sent something it did not send is worse than
    // one that asks twice — the same rule as prose that describes what the code does not do.
    expect(screen.getByText(/is not sent/i)).toBeInTheDocument();
  });

  it("keeps quiet about the check when there is nothing to drop", async () => {
    // The control: a warning that shows whether or not anything would be lost is decoration, and
    // decoration is what teaches people to stop reading warnings.
    const user = render();

    await user.click(mode(/split it up/i));

    expect(screen.queryByText(/is not sent/i)).toBeNull();
  });

  it("will not change mode under a run somebody is paying for", async () => {
    const user = render("lifecycle");
    // Never resolves: a run that is still going, which is the state the lock exists for.
    vi.mocked(streamLifecycle).mockImplementation(() => new Promise<void>(() => {}));

    await user.type(task(), "x");
    await user.click(screen.getByRole("button", { name: /start/i }));

    // Switching would unmount the running mode, and unmounting kills the stream while the tokens
    // keep being spent — the same money, no longer watched.
    await waitFor(() => expect(mode(/one agent/i)).toBeDisabled());
    // And it says so. A control that is disabled for a reason nobody can read is a broken control.
    expect(screen.getByText(/stop it or let it finish/i)).toBeInTheDocument();
  });

  it("lets the mode change again once the run is over", async () => {
    // The control for the lock: a flag that is never cleared would pass the test above and leave
    // the strip dead for the rest of the session.
    const user = render("lifecycle");

    await user.type(task(), "x");
    await user.click(screen.getByRole("button", { name: /start/i }));

    await waitFor(() => expect(mode(/one agent/i)).toBeEnabled());
    expect(screen.queryByText(/stop it or let it finish/i)).toBeNull();
  });

  it("selects the crew when the plan says the task writes files", async () => {
    vi.mocked(previewHierarchy).mockResolvedValue(WRITE_PLAN);
    const user = render("hierarchy");

    await user.type(task(), "implemente o retry");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /build a crew/i })).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /build a crew/i }));

    // The note has always been able to say "this writes files, that goes to a crew". Saying it was
    // all it could do: the crew was two clicks deeper, behind the preview that had just produced
    // this verdict. Recommending it can now also select it.
    expect(mode(/competing attempts/i)).toBeChecked();
    expect(check()).toBeInTheDocument();
  });
});
