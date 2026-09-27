import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobsPanel } from "@/components/JobsPanel";
import { listJobs, stopJob } from "@/lib/api";
import type { BackgroundJob } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ listJobs: vi.fn(), stopJob: vi.fn() }));

function job(over: Partial<BackgroundJob>): BackgroundJob {
  return {
    id: "a1b2c3d4e5f6",
    command: "python bench/verified_cascade/run.py --stage s0",
    cwd: "/projects/chimera",
    pid: 4242,
    started_at: 1_790_000_000,
    log: "/home/.chimera/jobs/a1b2c3d4e5f6.log",
    state: "running",
    exit_code: null,
    finished_at: null,
    reported: false,
    tail: "task 12/40 ok",
    ...over,
  } as BackgroundJob;
}

/**
 * A background job outlives the turn that started it and the turn's Stop does not reach it, so the
 * side panel is where a person sees it and stops it. What matters: a running job can be stopped
 * from here and the request names that job; an ended one offers no Stop; and with no jobs at all
 * the panel is absent rather than an empty box.
 */
describe("the background jobs panel", () => {
  beforeEach(() => {
    vi.mocked(listJobs).mockReset();
    vi.mocked(stopJob).mockReset();
  });

  it("shows a running job with its command and output, and stops it", async () => {
    vi.mocked(listJobs).mockResolvedValue({ jobs: [job({})] });
    vi.mocked(stopJob).mockResolvedValue(job({ state: "cancelled" }));
    const user = userEvent.setup();
    renderWithProviders(<JobsPanel />);

    expect(await screen.findByText("Background jobs")).toBeInTheDocument();
    expect(screen.getByText("python bench/verified_cascade/run.py --stage s0")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("task 12/40 ok")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Stop job a1b2c3d4e5f6" }));
    await waitFor(() => expect(vi.mocked(stopJob)).toHaveBeenCalledWith("a1b2c3d4e5f6"));
  });

  it("offers no Stop for a job that already ended, and says how it ended", async () => {
    vi.mocked(listJobs).mockResolvedValue({
      jobs: [
        job({ id: "f1", state: "finished", exit_code: 1 }),
        job({ id: "t1", state: "timed_out", exit_code: -9 }),
        job({ id: "l1", state: "lost" }),
      ],
    });
    renderWithProviders(<JobsPanel />);

    expect(await screen.findByText(/finished · exit 1/)).toBeInTheDocument();
    expect(screen.getByText(/hit the time limit/)).toBeInTheDocument();
    expect(screen.getByText("lost")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Stop job/ })).toBeNull();
  });

  it("renders nothing when there are no jobs", async () => {
    vi.mocked(listJobs).mockResolvedValue({ jobs: [] });
    const { container } = renderWithProviders(<JobsPanel />);

    await waitFor(() => expect(vi.mocked(listJobs)).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Background jobs")).toBeNull();
  });
});
