import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WeeklyReviewRow } from "@/components/WeeklyReviewRow";
import type { WeeklyReview } from "@/lib/types";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The weekly review's switch on the Settings screen. It shipped as two terminal commands (propose,
 * then `cron enable`); the row is one switch, and it says where the review will be read — a host,
 * or Automation's results — rather than leaving "on" to imply it is sent somewhere.
 */
vi.mock("@/lib/api", () => ({ getWeeklyReview: vi.fn(), putWeeklyReview: vi.fn() }));

const api = await import("@/lib/api");

const NONE: WeeklyReview = { proposed: false, job_id: "", enabled: false, posts_to: "" };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getWeeklyReview).mockResolvedValue(NONE);
  vi.mocked(api.putWeeklyReview).mockImplementation(async (enabled) => ({
    proposed: true,
    job_id: "j1",
    enabled,
    posts_to: "",
  }));
});

describe("the weekly review's switch", () => {
  it("is off while the job does not exist, and switching it on asks for it", async () => {
    render(<WeeklyReviewRow />);

    const control = await screen.findByRole("switch", { name: "Weekly review" });
    expect(control).toHaveAttribute("aria-checked", "false");
    await userEvent.click(control);

    expect(api.putWeeklyReview).toHaveBeenCalledWith(true);
    expect(await screen.findByText(/Posts nowhere yet/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Weekly review" })).toHaveAttribute("aria-checked", "true");
  });

  it("switching it off pauses the job", async () => {
    vi.mocked(api.getWeeklyReview).mockResolvedValue({ ...NONE, proposed: true, job_id: "j1", enabled: true });
    render(<WeeklyReviewRow />);

    await userEvent.click(await screen.findByRole("switch", { name: "Weekly review" }));

    expect(api.putWeeklyReview).toHaveBeenCalledWith(false);
  });

  it("names where it posts by host only", async () => {
    vi.mocked(api.getWeeklyReview).mockResolvedValue({
      proposed: true,
      job_id: "j1",
      enabled: true,
      posts_to: "https://discord.com/…",
    });
    render(<WeeklyReviewRow />);

    expect(await screen.findByText("Posts to https://discord.com/….")).toBeInTheDocument();
  });

  it("is a job that is proposed but still disabled shown as off", async () => {
    vi.mocked(api.getWeeklyReview).mockResolvedValue({ ...NONE, proposed: true, job_id: "j1", enabled: false });
    render(<WeeklyReviewRow />);

    expect(await screen.findByRole("switch", { name: "Weekly review" })).toHaveAttribute("aria-checked", "false");
  });
});
