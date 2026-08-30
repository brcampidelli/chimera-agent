/** The gate a scheduled job can opt into — and could not, from anywhere, until this field existed.
 *
 * `CronJob` has carried `verify` and `max_attempts` since the scheduled run got its harness, and the
 * dispatch arms the loop only when `verify` is non-empty. Nothing could write either one: not this
 * screen, not `POST /api/cron`, not `chimera cron add`. So for every user `verify` was permanently
 * `""`, the gate never armed, and the change kept its accounting half and none of the rest.
 *
 * Found by installing the release and trying to schedule a job that edits code.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Cron } from "@/components/Cron";
import { createCron, getCron } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCronSilence: vi.fn(),
  getCron: vi.fn(),
  createCron: vi.fn(),
  enableCron: vi.fn(),
  disableCron: vi.fn(),
  deleteCron: vi.fn(),
}));

const mockGetCron = vi.mocked(getCron);
const mockCreateCron = vi.mocked(createCron);

describe("Cron — the gate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCron.mockResolvedValue([]);
    mockCreateCron.mockResolvedValue({} as never);
  });

  it("sends the verify command the user typed, so the dispatch can arm its loop", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "nightly fix");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "fix the failing test");
    await user.type(screen.getByPlaceholderText(/test command/i), "pytest -q");
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    await vi.waitFor(() =>
      expect(mockCreateCron).toHaveBeenCalledWith(
        expect.objectContaining({ verify: "pytest -q" }),
        expect.anything(),
      ),
    );
  });

  it("only asks for a retry when there is a gate to judge it", async () => {
    // Without a gate nothing can tell a failed attempt from a finished one, so a second attempt
    // would just do the work twice and keep whichever answer came last.
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "morning brief");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "summarise my email");
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    await vi.waitFor(() =>
      expect(mockCreateCron).toHaveBeenCalledWith(
        expect.objectContaining({ verify: "", max_attempts: 1 }),
        expect.anything(),
      ),
    );
  });

  it("raises max_attempts once a gate exists", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Cron />);

    await user.type(await screen.findByPlaceholderText(/name/i), "nightly fix");
    await user.type(screen.getByPlaceholderText(/what should Chimera do/i), "fix the failing test");
    await user.type(screen.getByPlaceholderText(/test command/i), "pytest -q");
    await user.click(screen.getByRole("button", { name: /Schedule/i }));

    await vi.waitFor(() =>
      expect(mockCreateCron).toHaveBeenCalledWith(
        expect.objectContaining({ max_attempts: 2 }),
        expect.anything(),
      ),
    );
  });

  it("the field is optional, and says so where a lay user reads it", async () => {
    // Most schedules are reports that change no files, and the diff gate fails an attempt that
    // changed none — so arming this for everyone would break every report job. The label has to
    // carry that, because a required-looking field is one people fill in with something wrong.
    renderWithProviders(<Cron />);

    // Two fields on this form say "(optional)", so the assertion has to name THIS one — matching
    // loosely would pass on the delivery webhook and prove nothing about the gate.
    const field = await screen.findByPlaceholderText(/test command/i);

    expect(field).toHaveAttribute("placeholder", expect.stringMatching(/optional/i));
    expect(field).toHaveAttribute("placeholder", expect.stringMatching(/no gate/i));
  });
});

describe("Cron — a job that has a gate says so", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateCron.mockResolvedValue({} as never);
  });

  function job(over: Record<string, unknown> = {}) {
    return {
      id: "j1", name: "nightly tests", trigger: "cron", schedule: "0 4 * * *",
      action: "run the tests", enabled: true, next_run: 1, last_run: null,
      last_status: null, last_error: null, consecutive_failures: 0,
      created_by: "human", workspace: "/w", deliver_to: null,
      verify: "", max_attempts: 1, ...over,
    } as never;
  }

  it("shows the command that judges the job", async () => {
    // Found by creating one through the API and reading the list: the gate round-tripped and the
    // card said nothing about it, so a checked job and an unchecked one were the same row — and
    // the gate is what decides whether the work is kept at all.
    mockGetCron.mockResolvedValue([job({ verify: "python -m pytest -q", max_attempts: 2 })]);

    renderWithProviders(<Cron />);

    expect(await screen.findByText(/python -m pytest -q/)).toBeTruthy();
  });

  it("says nothing at all when there is no gate", async () => {
    // The absence has to be legible too. A row that always shows a gate line — empty, or reading
    // "none" — trains people to stop reading it, and then the one that matters is invisible.
    mockGetCron.mockResolvedValue([job({ verify: "" })]);

    renderWithProviders(<Cron />);

    await screen.findByText(/nightly tests/);
    expect(screen.queryByText(/gated by/i)).toBeNull();
  });
});
