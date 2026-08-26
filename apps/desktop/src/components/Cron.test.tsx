import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Cron } from "@/components/Cron";
import { createCron, getCron } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCron: vi.fn(),
  createCron: vi.fn(),
  enableCron: vi.fn(),
  disableCron: vi.fn(),
  deleteCron: vi.fn(),
}));

const mockGetCron = vi.mocked(getCron);
const mockCreateCron = vi.mocked(createCron);

describe("Cron — create a schedule from the UI", () => {
  beforeEach(() => {
    mockGetCron.mockReset();
    mockCreateCron.mockReset();
    mockGetCron.mockResolvedValue([]);
    mockCreateCron.mockResolvedValue({} as never);
  });

  it("schedules a job from name + action + the default time, with no CLI", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "morning brief");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "summarise my email");
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    await vi.waitFor(() =>
      expect(mockCreateCron).toHaveBeenCalledWith(
        {
          name: "morning brief",
          schedule: "0 7 * * *",
          action: "summarise my email",
          // Which folder the job will work in — null here because this test chose no project.
          // Sent on creation rather than read when it fires: see Cron.workspace.test.tsx.
          workspace: null,
          // And where its answer goes. Null is "only the result file", which is what every
          // schedule did before this field was wired to anything: see Cron.deliver.test.tsx.
          deliver_to: null,
        },
        expect.anything(), // react-query passes a context object as the 2nd arg
      ),
    );
  });

  it("a preset fills the cron expression a lay user shouldn't have to write", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "hourly check");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "check the site");
    await user.click(screen.getByRole("button", { name: /Every hour/i }));
    await user.click(screen.getByRole("button", { name: /^Schedule$/i }));

    await vi.waitFor(() =>
      expect(mockCreateCron).toHaveBeenCalledWith(
        expect.objectContaining({ schedule: "0 * * * *" }),
        expect.anything(),
      ),
    );
  });

  it("won't submit until name, time and action are all filled", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    // name + time (default) present, action empty → button disabled
    await user.type(await screen.findByPlaceholderText(/name/i), "x");
    expect(screen.getByRole("button", { name: /Schedule/i })).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "do a thing");
    expect(screen.getByRole("button", { name: /Schedule/i })).toBeEnabled();
  });
});

/**
 * A schedule screen exists so nobody has to read the scheduler log.
 *
 * `consecutive_failures` and `last_error` have been on `/api/cron` the whole time; this screen
 * rendered neither. A job that had failed every hour for a week looked exactly like one that works
 * — same row, same badges — so the only way to find out was the log, which is the thing the screen
 * was supposed to replace.
 */
describe("Cron — a failing job says so", () => {
  const JOB = {
    id: "j1",
    name: "morning brief",
    schedule: "0 7 * * *",
    action: "summarise my email",
    trigger: "cron",
    created_by: "user",
    enabled: true,
    last_run: 1_760_000_000,
    next_run: 1_760_086_400,
    last_status: "error",
    last_error: "provider returned 401: invalid api key",
    consecutive_failures: 7,
  };

  beforeEach(() => {
    mockGetCron.mockReset();
    mockCreateCron.mockReset();
    mockCreateCron.mockResolvedValue({} as never);
  });

  it("shows the streak and the reason, not just the name", async () => {
    mockGetCron.mockResolvedValue([JOB] as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText("morning brief")).toBeInTheDocument();
    // The streak, because one failure is noise and seven is a broken job.
    expect(screen.getByText(/7× in a row/)).toBeInTheDocument();
    // And WHY, inline — a tooltip is found by accident.
    expect(screen.getByText(/invalid api key/)).toBeInTheDocument();
  });

  it("stays quiet about a healthy job", async () => {
    // The counterpart that makes the test above mean something: if the badge rendered for every
    // job it would carry no information, and a red mark on a working schedule is worse than none.
    mockGetCron.mockResolvedValue([
      { ...JOB, consecutive_failures: 0, last_status: "ok", last_error: null },
    ] as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText("morning brief")).toBeInTheDocument();
    expect(screen.queryByText(/in a row/)).not.toBeInTheDocument();
    expect(screen.queryByText(/invalid api key/)).not.toBeInTheDocument();
  });
});
