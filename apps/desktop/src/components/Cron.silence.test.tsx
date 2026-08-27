import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron } from "@/components/Cron";
import { getCron, getCronResults, getCronSilence } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCron: vi.fn(),
  getCronResults: vi.fn(),
  getCronSilence: vi.fn(),
  createCron: vi.fn(),
  enableCron: vi.fn(),
  disableCron: vi.fn(),
  deleteCron: vi.fn(),
}));

function job(over: Record<string, unknown> = {}) {
  return {
    id: "j1",
    name: "resumo do site",
    trigger: "cron",
    schedule: "0 7 * * *",
    action: "liste os arquivos",
    enabled: true,
    next_run: null,
    last_run: null,
    last_status: "ok",
    last_error: null,
    consecutive_failures: 0,
    created_by: "human",
    workspace: null,
    deliver_to: null,
    ...over,
  };
}

const QUIET = { overdue: [], failing: [], grace_seconds: 300 };

/**
 * What the schedule is not telling you.
 *
 * Every honesty mechanism in this app sits downstream of a run having happened — the verifier
 * judges a result, the receipt names what it cost. None of them gets a turn when the run never
 * occurred, and a schedule that produced nothing reads exactly like a schedule with nothing due.
 *
 * On the desktop that gap has one common cause and nothing on screen ever said it: the scheduler
 * lives inside the backend this window starts, so a job due while the app was closed simply did not
 * run. `Scheduler.overdue` could always answer this and only the CLI ever asked.
 */
describe("what did not run", () => {
  beforeEach(() => {
    vi.mocked(getCron).mockReset().mockResolvedValue([job()] as never);
    vi.mocked(getCronResults).mockReset().mockResolvedValue([] as never);
    vi.mocked(getCronSilence).mockReset().mockResolvedValue(QUIET as never);
  });

  it("names the schedules that never fired", async () => {
    vi.mocked(getCronSilence).mockResolvedValue({
      ...QUIET,
      overdue: [
        {
          id: "j1",
          name: "resumo do site",
          schedule: "0 7 * * *",
          due_at: 1_787_000_000,
          behind_seconds: 7200,
        },
      ],
    } as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText(/did not run/i)).toBeTruthy();
    expect(screen.getAllByText("resumo do site").length).toBeGreaterThan(0);
  });

  it("says why, because nobody would guess it", async () => {
    // The cause is the architecture: the schedule runs inside this app. Listing missed jobs
    // without that sentence reads as "your schedules are broken", which is a different and wrong
    // conclusion — and the one that makes somebody stop trusting the feature.
    vi.mocked(getCronSilence).mockResolvedValue({
      ...QUIET,
      overdue: [
        { id: "j1", name: "x", schedule: "0 7 * * *", due_at: 1_787_000_000, behind_seconds: 60 },
      ],
    } as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText(/runs inside this app/i)).toBeTruthy();
    expect(screen.getByText(/nothing is broken/i)).toBeTruthy();
  });

  it("stays silent when everything is on schedule", async () => {
    // The control, and the one that decides whether this is a feature or noise. A banner that is
    // always there is a banner nobody reads on the day it means something.
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText(/did not run/i)).toBeNull();
  });

  it("does not repeat what the row already says", async () => {
    // A job that runs on time and loses every time already carries a badge on its own row. The
    // banner is about the other silence — the one with no row to show it — and duplicating the
    // first would dilute the half that nothing showed at all.
    vi.mocked(getCron).mockResolvedValue([job({ consecutive_failures: 3 })] as never);
    vi.mocked(getCronSilence).mockResolvedValue({
      ...QUIET,
      failing: [
        { id: "j1", name: "resumo do site", consecutive_failures: 3, last_status: "error", last_error: "boom" },
      ],
    } as never);
    renderWithProviders(<Cron />);

    await screen.findByText("resumo do site");
    expect(screen.queryByText(/did not run/i)).toBeNull();
  });

  it("survives a backend that cannot answer the question", async () => {
    // The banner is a nicety on a screen whose job is listing schedules. If this query failing
    // took the list down, an unanswerable question would cost somebody the feature itself.
    vi.mocked(getCronSilence).mockRejectedValue(new Error("boom"));
    renderWithProviders(<Cron />);

    expect(await screen.findByText("resumo do site")).toBeTruthy();
  });
});
