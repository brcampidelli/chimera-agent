import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron } from "@/components/Cron";
import { createCron, getCron, getCronHistory, setCronPermissionMode } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCronSilence: vi.fn(),
  getCron: vi.fn(),
  getCronResults: vi.fn(),
  getCronHistory: vi.fn(),
  setCronPermissionMode: vi.fn(),
  createCron: vi.fn(),
  enableCron: vi.fn(),
  disableCron: vi.fn(),
  deleteCron: vi.fn(),
}));

const mockGetCron = vi.mocked(getCron);
const mockCreate = vi.mocked(createCron);
const mockHistory = vi.mocked(getCronHistory);
const mockSetMode = vi.mocked(setCronPermissionMode);

function job(over: Record<string, unknown> = {}) {
  return {
    id: "j1",
    name: "nightly",
    trigger: "cron",
    schedule: "0 3 * * *",
    action: "report",
    enabled: true,
    next_run: null,
    last_run: null,
    last_status: null,
    last_error: null,
    consecutive_failures: 0,
    created_by: "human",
    workspace: null,
    deliver_to: null,
    ...over,
  };
}

/**
 * A scheduled task's catch-up policy, permission mode and worktree, and the history of why a time
 * did or did not run. The server holds the rules (never wider than Settings, never through the
 * bridge); the screen has to send the three fields, show them on the row, and say each reason in
 * words.
 */
describe("Cron: the three new fields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCron.mockResolvedValue([]);
    mockCreate.mockResolvedValue({} as never);
  });

  it("sends nothing extra when none of the three is chosen", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "brief");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "summarise");
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    await vi.waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const body = mockCreate.mock.calls[0][0] as Record<string, unknown>;
    expect(Object.keys(body)).not.toEqual(expect.arrayContaining(["catch_up"]));
    expect(body).not.toHaveProperty("permission_mode");
    expect(body).not.toHaveProperty("worktree");
  });

  it("sends what the user chose", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "brief");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "summarise");
    await user.selectOptions(screen.getByRole("combobox", { name: /missed while the app was off/i }), "skip");
    await user.selectOptions(screen.getByRole("combobox", { name: /Permission mode/i }), "manual");
    await user.click(screen.getByRole("checkbox", { name: /worktree/i }));
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    await vi.waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith(
        expect.objectContaining({ catch_up: "skip", permission_mode: "manual", worktree: true }),
        expect.anything(),
      ),
    );
  });

  it("says so when the server refuses a mode wider than Settings", async () => {
    mockCreate.mockRejectedValue(new Error("the dont_ask mode lets a task do more; never wider"));
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "brief");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "summarise");
    await user.selectOptions(screen.getByRole("combobox", { name: /Permission mode/i }), "dont_ask");
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    expect(await screen.findByText(/wider than your Settings allow/i)).toBeTruthy();
  });
});

describe("Cron: a task's row and history", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the catch-up, mode and worktree on the row", async () => {
    mockGetCron.mockResolvedValue([
      job({ catch_up: "skip", permission_mode: "plan", worktree: true }),
    ] as never);
    renderWithProviders(<Cron />);

    expect(await screen.findByText(/skips missed runs/i)).toBeTruthy();
    expect(screen.getByText(/mode: Plan/i)).toBeTruthy();
    expect(screen.getByText("worktree")).toBeTruthy();
  });

  it("a task written before the fields existed renders with none of them", async () => {
    mockGetCron.mockResolvedValue([job()] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("nightly");
    expect(screen.queryByText(/skips missed runs/i)).toBeNull();
    expect(screen.queryByText(/mode: /i)).toBeNull();
  });

  it("changes the mode through the owner's route", async () => {
    mockGetCron.mockResolvedValue([job()] as never);
    mockSetMode.mockResolvedValue({} as never);
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.selectOptions(await screen.findByRole("combobox", { name: /Permission mode of this task/i }), "manual");

    await vi.waitFor(() => expect(mockSetMode).toHaveBeenCalledWith("j1", "manual"));
  });

  it("reads the history only when it is opened, and says each reason in words", async () => {
    mockGetCron.mockResolvedValue([job()] as never);
    mockHistory.mockResolvedValue([
      { at: 1_787_000_100, job_id: "j1", name: "nightly", outcome: "skipped", reason: "missed_too_old", status: "", scheduled_for: 1_786_000_000, detail: "", worktree: "" },
      { at: 1_787_000_000, job_id: "j1", name: "nightly", outcome: "note", reason: "worktree_kept", status: "", scheduled_for: null, detail: "", worktree: "/tmp/wt/x" },
    ] as never);
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.click(await screen.findByText("History"));

    expect(await screen.findByText(/Missed by more than 7 days/i)).toBeTruthy();
    expect(screen.getByText(/left work in a worktree/i)).toBeTruthy();
    expect(screen.getByText(/worktree: \/tmp\/wt\/x/)).toBeTruthy();
    expect(mockHistory).toHaveBeenCalledTimes(1);
  });

  it("does not fetch any history until a row is opened", async () => {
    mockGetCron.mockResolvedValue([job()] as never);
    renderWithProviders(<Cron />);

    await screen.findByText("nightly");

    expect(mockHistory).not.toHaveBeenCalled();
  });
});
