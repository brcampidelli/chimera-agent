import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Orchestration } from "@/components/orchestration/Orchestration";
import { previewHierarchy, streamHierarchy } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { HierarchyPreview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  previewHierarchy: vi.fn(),
  streamHierarchy: vi.fn(),
  cancelOrchestration: vi.fn(),
}));

const mockPreview = vi.mocked(previewHierarchy);
const mockStream = vi.mocked(streamHierarchy);

function plan(over: Partial<HierarchyPreview> = {}): HierarchyPreview {
  return {
    shape: "sequential_write",
    profitable_estimate: false,
    estimate_margin: 0,
    would_fall_back: true,
    fell_back_reason: "shape",
    subtasks: [],
    workers: 0,
    budget_per_worker: 0,
    sources: 0,
    decompose_spent: false,
    ...over,
  };
}

async function preview(over: Partial<HierarchyPreview> = {}) {
  const user = userEvent.setup();
  mockPreview.mockResolvedValue(plan(over));
  renderWithProviders(<Orchestration workspace="/repo" onOpenCode={vi.fn()} />);
  await user.type(screen.getByLabelText(/tarefa|task|aufgabe/i), "Implement the retry");
  await user.click(screen.getByRole("button", { name: /see the plan/i }));
  await waitFor(() => expect(mockPreview).toHaveBeenCalled());
  return user;
}

/**
 * The single-agent outcome is the one this screen is most able to get wrong.
 *
 * `classify_task` sends anything with write intent — implement, fix, refactor, create — down the
 * single-agent path, which in a coding tool is most of what anyone types. If that reads as a
 * failure, the screen tells the majority of users it is broken every time they use it.
 */
describe("when the orchestrator picks one agent", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not announce it as an alert", async () => {
    await preview();

    // Nothing here is an error, so nothing may interrupt a screen reader as though one occurred.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says what the task is, not what the feature could not do", async () => {
    await preview();

    expect(screen.getByText(/writes or edits files/i)).toBeInTheDocument();
    // No apology, no "unfortunately", no "could not".
    expect(screen.queryByText(/could not|unable|unfortunately|sorry/i)).toBeNull();
  });

  it("still offers to run the task", async () => {
    await preview();

    // The task remains runnable. A plan that ends in a dead end is not a plan.
    expect(screen.getByRole("button", { name: /run with one agent/i })).toBeInTheDocument();
  });

  it("offers the Code screen for write-shaped work, which is what it is for", async () => {
    const onOpenCode = vi.fn();
    const user = userEvent.setup();
    mockPreview.mockResolvedValue(plan());
    renderWithProviders(<Orchestration workspace="/repo" onOpenCode={onOpenCode} />);
    await user.type(screen.getByLabelText(/task/i), "Implement the retry");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));
    await waitFor(() => expect(mockPreview).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: /open in code/i }));
    expect(onOpenCode).toHaveBeenCalled();
  });

  it("does not offer Code for a task that is merely short", async () => {
    await preview({ shape: "simple", fell_back_reason: "shape" });

    // A one-line question is not writing work, and sending it to the editor would be noise.
    expect(screen.queryByRole("button", { name: /open in code/i })).toBeNull();
    expect(screen.getByText(/short and direct/i)).toBeInTheDocument();
  });

  it("explains a thin margin as arithmetic about size, not as a refusal", async () => {
    await preview({ shape: "parallel_read", fell_back_reason: "unprofitable" });

    expect(screen.getByText(/costs more than it saves/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("never starts a run just because the plan was asked for", async () => {
    await preview();

    // The cheap action stays cheap. Running is a second, deliberate click.
    expect(mockStream).not.toHaveBeenCalled();
  });

  it("does not leave the previous task's plan on screen while a new one loads", async () => {
    const user = await preview();
    // A decompose call takes seconds. Found in the browser: the old plan stayed up beside the
    // new text, long enough to read it and act on it as though it described the new task.
    let release!: (plan: HierarchyPreview) => void;
    mockPreview.mockReturnValue(new Promise((resolve) => (release = resolve)));

    await user.clear(screen.getByLabelText(/task/i));
    await user.type(screen.getByLabelText(/task/i), "Compare README.md and CHANGELOG.md");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));

    expect(screen.queryByText(/writes or edits files/i)).toBeNull();
    release(plan({ shape: "simple", fell_back_reason: "shape" }));
    await waitFor(() => expect(screen.getByText(/short and direct/i)).toBeInTheDocument());
  });
});
