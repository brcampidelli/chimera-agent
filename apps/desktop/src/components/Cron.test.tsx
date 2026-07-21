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
        { name: "morning brief", schedule: "0 7 * * *", action: "summarise my email" },
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
