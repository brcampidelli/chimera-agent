import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Work } from "@/components/Work";
import { getFsTree, getGitStatus, getRuns, streamRun } from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A tab that writes itself into the URL has to be readable back out of it.
 *
 * `choose` already wrote `?tab=git`, and `readTab` recognised `orchestration` and nothing else. So
 * switching to Git put a URL in the address bar that, reopened or reloaded, landed on Runs — the
 * address said one thing and the screen showed another, which is worse than having no deep link.
 */
function at(tab: string) {
  window.location.hash = tab ? `#/work?tab=${tab}` : "#/work";
}

describe("Work — the tab in the address", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(streamRun).mockImplementation(async () => {});
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it.each(["git", "worth", "orchestration"])("opens on ?tab=%s", async (tab) => {
    at(tab);

    renderWithProviders(<Work />);

    await waitFor(() =>
      expect(screen.getByRole("tab", { selected: true })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    const selected = screen.getByRole("tab", { selected: true });
    expect(selected.textContent?.toLowerCase()).not.toBe("runs");
  });

  it("falls back to Runs for a tab name that is not one", async () => {
    // A hand-edited or stale URL must not leave the screen blank.
    at("nonsense");

    renderWithProviders(<Work />);

    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent(/runs/i);
  });

  it("puts the tab it switched to into the address", async () => {
    at("");
    renderWithProviders(<Work />);

    await userEvent.click(await screen.findByRole("tab", { name: /git/i }));

    expect(window.location.hash).toContain("tab=git");
  });
});
