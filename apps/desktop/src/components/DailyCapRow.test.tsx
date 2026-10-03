import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DailyCapRow } from "@/components/DailyCapRow";
import { Usage } from "@/components/Usage";
import { ApiError } from "@/lib/api";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The day's dollar ceiling, on the screen that shows the dollars (study 29, P2.1).
 *
 * `CHIMERA_DAILY_USD_CAP` braked the scheduler for months with no way in but `.env`. The row's
 * hint carries the whole honesty of it: the cap stops SCHEDULED jobs and nothing else, and a person
 * who reads "daily cap" above their chat costs will believe otherwise unless the row says so.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ApiError: real.ApiError,
    getConfig: vi.fn(),
    patchConfig: vi.fn(),
    getUsage: vi.fn(),
  };
});

const api = await import("@/lib/api");

function config(cap: number | null) {
  return { spend: { daily_usd_cap: cap } } as unknown as Awaited<ReturnType<typeof api.getConfig>>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patchConfig).mockResolvedValue({ updated: ["CHIMERA_DAILY_USD_CAP"] });
});

describe("the daily cap row", () => {
  it("says it applies to scheduled tasks only", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config(null));
    render(<DailyCapRow />);

    expect(await screen.findByText(/applies to scheduled tasks only/i)).toBeInTheDocument();
    expect(screen.getByText(/chat and code turns are not stopped by it/i)).toBeInTheDocument();
  });

  it("shows the saved cap, and an empty field when there is none", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config(2.5));
    render(<DailyCapRow />);

    expect(await screen.findByDisplayValue("2.5")).toBeInTheDocument();
  });

  it("saves what was typed under the setting the scheduler reads", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config(null));
    render(<DailyCapRow />);
    const field = await screen.findByLabelText("Daily cap");

    await userEvent.type(field, "5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.patchConfig).toHaveBeenCalledWith({ CHIMERA_DAILY_USD_CAP: "5" }),
    );
  });

  it("removes the cap by saving the field empty", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config(3));
    render(<DailyCapRow />);
    const field = await screen.findByDisplayValue("3");

    await userEvent.clear(field);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.patchConfig).toHaveBeenCalledWith({ CHIMERA_DAILY_USD_CAP: "" }));
  });

  it("shows the server's refusal instead of pretending it saved", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config(null));
    vi.mocked(api.patchConfig).mockRejectedValue(
      new ApiError("CHIMERA_DAILY_USD_CAP must be more than zero", 400),
    );
    render(<DailyCapRow />);

    await userEvent.type(await screen.findByLabelText("Daily cap"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/more than zero/);
  });

  it("sits at the top of Cost & Usage even before any usage is recorded", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(config(null));
    vi.mocked(api.getUsage).mockResolvedValue({
      totals: { turns: 0 },
    } as unknown as Awaited<ReturnType<typeof api.getUsage>>);
    render(<Usage />);

    expect(await screen.findByText(/usage is recorded from now on/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Daily cap")).toBeInTheDocument();
  });
});
