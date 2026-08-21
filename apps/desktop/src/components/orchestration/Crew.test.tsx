import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Orchestration } from "@/components/orchestration/Orchestration";
import {
  cancelOrchestration,
  getApproaches,
  previewHierarchy,
  streamCrew,
  type HierarchyStreamHandlers,
  type OrchFrame,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { HierarchyPreview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  previewHierarchy: vi.fn(),
  streamHierarchy: vi.fn(),
  streamCrew: vi.fn(),
  cancelOrchestration: vi.fn(),
  getApproaches: vi.fn(),
}));

const mockPreview = vi.mocked(previewHierarchy);
const mockCrew = vi.mocked(streamCrew);
const mockApproaches = vi.mocked(getApproaches);

/** A stand-in catalogue. Small and local on purpose: these tests are about what the form does
 *  with a catalogue, not about which approaches the backend happens to ship this week. */
const CATALOGUE = {
  approaches: [
    { id: "minimal", instruction: "Make the SMALLEST change that solves the task." },
    { id: "rewrite", instruction: "Rewrite the unit that owns this problem." },
  ],
  default: ["minimal", "rewrite"],
};

/** A write-shaped task: what `classify_task` sends down the single-agent path, and what a crew
 *  exists for. */
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

function frame(seq: number, kind: string, data: Record<string, unknown> = {}, taskId = ""): OrchFrame {
  return { seq, kind, task_id: taskId, text: "", data };
}

/** Get as far as the crew form: ask for a plan on a write-shaped task, then press the crew door. */
async function openCrewForm() {
  const user = userEvent.setup();
  mockPreview.mockResolvedValue(WRITE_PLAN);
  mockApproaches.mockResolvedValue(CATALOGUE);
  renderWithProviders(<Orchestration workspace="/repo" onOpenCode={vi.fn()} />);
  await user.type(screen.getByLabelText(/task/i), "implemente o retry");
  await user.click(screen.getByRole("button", { name: /see the plan/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /build a crew/i })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /build a crew/i }));
  // The catalogue arrives over the wire, so the seeded roles are not there on first paint.
  await waitFor(() =>
    expect(screen.getByLabelText(/approach for worker 1/i)).toHaveValue("minimal"),
  );
  return user;
}

/** Fill the form and run, returning the captured stream handlers. */
async function runCrew(user: ReturnType<typeof userEvent.setup>, verify = "pytest -q") {
  let captured!: HierarchyStreamHandlers;
  mockCrew.mockImplementation((_req, handlers: HierarchyStreamHandlers) => {
    captured = handlers;
    return new Promise<void>(() => {});
  });
  if (verify) await user.type(screen.getByLabelText(/the check/i), verify);
  // No instruction typed: the two seeded approaches already carry theirs, which is the change
  // this form makes — two blank boxes invited two ways of saying the same thing.
  await user.click(screen.getByRole("button", { name: /run the crew/i }));
  await waitFor(() => expect(mockCrew).toHaveBeenCalled());
  return () => captured;
}

function send(handlers: HierarchyStreamHandlers, ...frames: OrchFrame[]) {
  act(() => {
    for (const f of frames) handlers.onFrame?.(f);
  });
}

describe("the crew", () => {
  beforeEach(() => vi.clearAllMocks());

  it("is offered for the work the hierarchy refuses", async () => {
    await openCrewForm();

    // The whole point. A write-shaped task used to get a note pointing at another screen; the
    // crew is the shape that fits it, and it is on the same screen that just said no.
    expect(screen.getByLabelText(/the check/i)).toBeInTheDocument();
  });

  it("says what happens without a check, before you run", async () => {
    await openCrewForm();

    // Not a footnote: with no check, everyone who did not crash merges, and two workers touching
    // one file both lose it — so a crew without a check usually lands nothing at all.
    expect(screen.getByText(/two workers touching the same file both lose it/i)).toBeInTheDocument();
  });

  it("refuses two workers with the same name before asking the server", async () => {
    const user = await openCrewForm();

    for (const n of [1, 2]) {
      await user.selectOptions(screen.getByLabelText(new RegExp(`approach for worker ${n}`, "i")), "custom");
      await user.type(screen.getByLabelText(new RegExp(`name of worker ${n}`, "i")), "meu");
      await user.type(screen.getByLabelText(new RegExp(`instruction for worker ${n}`, "i")), "faça");
    }

    // The name is how each worker's results are reported; two of them would put two workers on
    // one card. The server refuses it too — this just saves the round trip.
    expect(screen.getByText(/share a name/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run the crew/i })).toBeDisabled();
  });

  it("fills in the instruction the chosen approach will actually send", async () => {
    const user = await openCrewForm();

    await user.selectOptions(screen.getByLabelText(/approach for worker 1/i), "rewrite");

    // Verbatim and editable, not a translated paraphrase: a summary of a prompt can drift from
    // the prompt, and then the screen is describing something other than what it sends.
    expect(screen.getByLabelText(/instruction for worker 1/i)).toHaveValue(
      "Rewrite the unit that owns this problem.",
    );
  });

  it("warns when both workers were given the same approach", async () => {
    const user = await openCrewForm();

    await user.selectOptions(screen.getByLabelText(/approach for worker 2/i), "minimal");

    // The intuition it corrects: two tries is not better odds here. They write the same change,
    // both pass the check, and the one-file-one-owner rule discards both.
    expect(screen.getByText(/discards both/i)).toBeInTheDocument();
    // And it says THAT rather than "two workers share a name", which is only how the collision
    // shows up — a catalogue worker is named by its approach.
    expect(screen.queryByText(/share a name/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run the crew/i })).toBeDisabled();
  });

  it("shows which checkout each worker writes in", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", { workspace: "/tmp/wt-a", instruction: "mínima" }, "conservador"),
      frame(3, "crew_worker_started", { workspace: "/tmp/wt-b", instruction: "reescreva" }, "direto"),
    );

    // Invisible before: the worktrees are created, used and removed without ever being named.
    expect(screen.getByText("/tmp/wt-a")).toBeInTheDocument();
    expect(screen.getByText("/tmp/wt-b")).toBeInTheDocument();
  });

  it("says why a worker was discarded, and what the check printed", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", { workspace: "/tmp/wt-a" }, "conservador"),
      frame(3, "crew_worker_rejected", { reason: "verify", detail: "1 failed: test_desconto" }, "conservador"),
    );

    expect(screen.getByText(/discarded/i)).toBeInTheDocument();
    // The output, not just the fact: a crew whose workers all fail the same check is a crew
    // whose check is wrong, and that is only visible if the output is.
    expect(screen.getByText(/1 failed: test_desconto/)).toBeInTheDocument();
    expect(screen.getByText(/nothing from this worker reached your files/i)).toBeInTheDocument();
  });

  it("shows what a discarded worker wrote, since its checkout is already gone", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", {}, "minimal"),
      frame(3, "crew_worker_rejected", { reason: "verify", detail: "1 failed" }, "minimal"),
      frame(4, "crew_worker_produced", { files: [], lost: ["frete.py"], answer: "troquei o cálculo", landed: false }, "minimal"),
    );

    // The worktree is removed when the run ends, so this list is the only surviving account of
    // an attempt that was thrown away — and "wrote a file" and "wrote a file and lost it" are
    // not the same sentence.
    expect(screen.getByText("frete.py")).toBeInTheDocument();
    expect(screen.getByText(/^Files it wrote, and that were thrown away$/)).toBeInTheDocument();
    expect(screen.queryByText(/^Files it wrote, and that landed$/)).not.toBeInTheDocument();
  });

  it("does not claim a worker landed files that another worker contested", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", {}, "minimal"),
      frame(3, "crew_worker_verified", { verified_by: "pytest -q" }, "minimal"),
      // It passed. It still lost the file, because the other worker passed on it too.
      frame(4, "crew_worker_produced", { files: [], lost: ["frete.py"], landed: false }, "minimal"),
      frame(5, "crew_done", { merged: 0, conflicts: ["frete.py"], is_repo: true }),
    );

    // The reading this screen exists to prevent: a card saying the files landed, sitting right
    // above a panel saying none did.
    expect(screen.queryByText(/^Files it wrote, and that landed$/)).not.toBeInTheDocument();
    expect(screen.getByText(/NEITHER version was kept/i)).toBeInTheDocument();
  });

  it("says a contested file was kept by NEITHER worker", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "conflict", { path: "carrinho.py" }),
      frame(3, "crew_done", { merged: 0, conflicts: ["carrinho.py"], is_repo: true }),
    );

    expect(screen.getByText("carrinho.py")).toBeInTheDocument();
    // "One of them won" would be the wrong reading, and the expensive one: the file is untouched.
    expect(screen.getByText(/NEITHER version was kept/i)).toBeInTheDocument();
  });

  it("warns when there was no isolation at all", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_done", { merged: 1, conflicts: [], is_repo: false }),
    );

    // Outside a git repository every sentence about separate checkouts stops being true.
    expect(screen.getByText(/not a git repository/i)).toBeInTheDocument();
  });

  it("says plainly when nothing landed", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_done", { merged: 0, conflicts: [], is_repo: true }),
    );

    // A crew that produced nothing must not look like a crew that finished quietly.
    expect(screen.getByText(/Nothing landed/i)).toBeInTheDocument();
  });

  it("stops the crew by its run id", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);
    vi.mocked(cancelOrchestration).mockResolvedValue({ ok: true, cancelled: true });

    send(handlers(), frame(1, "run", { run_id: "c1" }), frame(2, "crew_worker_started", {}, "conservador"));
    await user.click(screen.getByRole("button", { name: /^stop$/i }));

    expect(cancelOrchestration).toHaveBeenCalledWith("c1");
  });

  it("does not present a merge as approved when the check decided nothing", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    // `pytest` that is not installed exits 127, and `VerificationResult` reports that as
    // `passed=True` on purpose — the work is not punished for our inability to check it. Reading
    // only `passed` turned "the command does not exist" into "the command approved this", on a
    // screen whose entire premise is that the test picks the winner.
    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", {}, "conservador"),
      frame(
        3,
        "crew_worker_verified",
        { verified_by: "", abstained: true, detail: "pytest: command not found" },
        "conservador",
      ),
    );

    expect(screen.getByText(/reached no verdict/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing approved it/i)).toBeInTheDocument();
  });

  it("still says a real pass is a pass", async () => {
    // Or the test above would pass against a version that had stopped trusting any check at all.
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", {}, "conservador"),
      frame(3, "crew_worker_verified", { verified_by: "pytest -q" }, "conservador"),
    );

    expect(screen.queryByText(/reached no verdict/i)).toBeNull();
  });

  it("tells a check that could not run apart from one that failed", async () => {
    const user = await openCrewForm();
    const handlers = await runCrew(user);

    send(
      handlers(),
      frame(1, "run", { run_id: "c1" }),
      frame(2, "crew_worker_started", {}, "conservador"),
      frame(
        3,
        "crew_worker_rejected",
        { reason: "verify", detail: "verification could not run: [Errno 2] No such file or directory" },
        "conservador",
      ),
    );

    // These send you to two different places. "Your check failed" points at the code the worker
    // wrote; a check that never executed points at the command or the folder — and reporting the
    // second as the first is how an afternoon gets spent reading a diff that was fine.
    expect(screen.getByText(/could not run here/i)).toBeInTheDocument();
    expect(screen.queryByText(/did not pass/i)).not.toBeInTheDocument();
  });

  it("does not show its own cleanup as a failure", async () => {
    const user = await openCrewForm();
    let captured!: HierarchyStreamHandlers;
    mockCrew.mockImplementation((_req, handlers: HierarchyStreamHandlers) => {
      captured = handlers;
      return new Promise<void>(() => {});
    });
    await user.type(screen.getByLabelText(/the check/i), "pytest -q");
    await user.type(screen.getByLabelText(/instruction for worker 1/i), "a");
    await user.type(screen.getByLabelText(/instruction for worker 2/i), "b");
    await user.click(screen.getByRole("button", { name: /run the crew/i }));
    await waitFor(() => expect(mockCrew).toHaveBeenCalled());

    // Leaving the screen aborts the fetch — that is this component's own teardown, and
    // "signal is aborted without reason" was reaching the user as though the run had broken.
    act(() => captured.onError?.("signal is aborted without reason"));

    expect(screen.queryByText(/aborted/i)).not.toBeInTheDocument();
  });

  it("sends the check and the roles the form was given", async () => {
    const user = await openCrewForm();
    await runCrew(user, "npm test");

    expect(mockCrew).toHaveBeenCalledWith(
      expect.objectContaining({
        verify: "npm test",
        workspace: "/repo",
        // Named by the approach they were built from, which is what the cards are keyed by.
        workers: [
          expect.objectContaining({ name: "minimal", instruction: CATALOGUE.approaches[0].instruction }),
          expect.objectContaining({ name: "rewrite", instruction: CATALOGUE.approaches[1].instruction }),
        ],
      }),
      expect.anything(),
      expect.anything(),
    );
  });
});
