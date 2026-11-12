import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorktreeIncludeConsent } from "@/components/settings/WorktreeIncludeConsent";
import { allowWorktreeInclude, getWorktreeIncludePending } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getWorktreeIncludePending: vi.fn(),
  allowWorktreeInclude: vi.fn(),
}));

const waiting = {
  project: "C:/work/app",
  digest: "d1",
  files: [".env", "config/local.json"],
  more: 3,
  bytes: 40,
};

describe("WorktreeIncludeConsent", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says nothing is waiting when nothing is", async () => {
    vi.mocked(getWorktreeIncludePending).mockResolvedValue({ pending: [] });
    renderWithProviders(<WorktreeIncludeConsent />);

    expect(await screen.findByText("No project is waiting for an answer.")).toBeInTheDocument();
  });

  it("shows what would be copied and sends the version it showed when allowed", async () => {
    vi.mocked(getWorktreeIncludePending).mockResolvedValue({ pending: [waiting] });
    vi.mocked(allowWorktreeInclude).mockResolvedValue({ pending: [] });
    renderWithProviders(<WorktreeIncludeConsent />);

    expect(await screen.findByText("C:/work/app wants to copy:")).toBeInTheDocument();
    expect(screen.getByText(".env")).toBeInTheDocument();
    expect(screen.getByText("and 3 more")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Allow copying for C:/work/app" }));

    await waitFor(() => expect(allowWorktreeInclude).toHaveBeenCalledWith("C:/work/app", "d1"));
    expect(await screen.findByText("No project is waiting for an answer.")).toBeInTheDocument();
  });
});

describe("WorktreeIncludeConsent — the whole list", () => {
  it("shows the first 20 and opens the rest on request", async () => {
    const files = Array.from({ length: 25 }, (_, i) => `.env.${String(i).padStart(2, "0")}`);
    vi.mocked(getWorktreeIncludePending).mockResolvedValue({
      pending: [{ project: "C:/work/app", digest: "d2", files, more: 0, bytes: 100 }],
    });
    renderWithProviders(<WorktreeIncludeConsent />);

    expect(await screen.findByText(".env.19")).toBeInTheDocument();
    expect(screen.queryByText(".env.24")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show all 25 files" }));

    expect(await screen.findByText(".env.24")).toBeInTheDocument();
  });
});
