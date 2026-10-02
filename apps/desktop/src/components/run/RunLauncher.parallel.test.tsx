import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskConsole } from "@/components/work/TaskConsole";
import { getPausedRuns, getRoleModels, streamRun } from "@/lib/api";
import { useRunSession } from "@/lib/run-session";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The Work screen starts a run in its project while another project's run works (2026-09-30).
 *
 * One run for the whole app meant the launcher's Run button was off in every project while any run
 * worked anywhere. Runs are one per project now, so what blocks this project's launcher is a run in
 * this project, and nothing else.
 */
function Elsewhere() {
  const run = useRunSession("/elsewhere");
  return (
    <button onClick={() => run.start({ task: "long job elsewhere", workspace: "/elsewhere", max_attempts: 3 })}>
      start elsewhere
    </button>
  );
}

describe("RunLauncher — beside a run in another project", () => {
  beforeEach(() => {
    vi.mocked(getPausedRuns).mockResolvedValue([]);
    vi.mocked(getRoleModels).mockResolvedValue({
      explore: "a", plan: "b", edit: "c", review: "d", fuse_plan: false, fuse_review: false,
    });
    vi.mocked(streamRun).mockImplementation(() => new Promise<void>(() => {}));
  });

  it("starts a run here while another project's run works", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Elsewhere />
        <TaskConsole workspace="/here" onOpenCode={() => {}} />
      </>,
    );
    await user.click(screen.getByText("start elsewhere"));
    await user.type(await screen.findByPlaceholderText(/task|tarefa/i), "fix the build");

    await user.click(screen.getByRole("button", { name: /^Run$|Executar/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamRun).mock.calls[1][0].workspace).toBe("/here");
  });
});
