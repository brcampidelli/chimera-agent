import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TABS, Work } from "@/components/Work";
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

  // Every tab, from the constant rather than a hand-kept copy. Adding the fifth tab is exactly
  // how this broke the first time: the union type gained "lifecycle", `choose` wrote it into the
  // URL, `TABS` did not list it, and the typechecker had nothing to say because they are two
  // separate declarations of the same fact.
  it.each(TABS.filter((x) => x !== "run"))("opens on ?tab=%s", async (tab) => {
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

  it("has a URL name for every tab it renders", async () => {
    // The invariant, and the one the loop above cannot check: `it.each(TABS)` simply generates
    // fewer cases when TABS loses an entry, so deleting "lifecycle" from it made the suite SMALLER
    // and still green. Counting the rendered controls against the list fails in both directions —
    // a tab with no URL name, and a URL name with no tab.
    at("");
    renderWithProviders(<Work />);

    expect(await screen.findAllByRole("tab")).toHaveLength(TABS.length);
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
