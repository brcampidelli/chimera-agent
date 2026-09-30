import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns } from "@/lib/api";
import { STORAGE_KEY } from "@/lib/layout/store";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The Code screen's left region, the conversation list, can be hidden and brought back (phase 1 of the
 * dynamic screen). Hidden, it leaves a tab on its edge; the choice is kept across launches.
 */
describe("Code — hiding the conversation list", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    localStorage.clear();
  });

  it("hides the list from its own button and leaves a tab that brings it back", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await user.click(await screen.findByRole("button", { name: "Hide the left sidebar" }));

    expect(screen.queryByRole("button", { name: /New conversation/ })).not.toBeInTheDocument();
    const tab = screen.getByRole("button", { name: "Show the left sidebar" });
    await waitFor(() => expect(tab).toHaveFocus());

    await user.click(tab);
    expect(await screen.findByRole("button", { name: /New conversation/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show the left sidebar" })).not.toBeInTheDocument();
  });

  it("keeps the list hidden across a remount, because the layout is stored", async () => {
    const user = userEvent.setup();
    const first = renderWithProviders(<Code />);
    await user.click(await screen.findByRole("button", { name: "Hide the left sidebar" }));
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").regions.left.visible).toBe(false);
    first.unmount();

    renderWithProviders(<Code />);

    expect(await screen.findByRole("button", { name: "Show the left sidebar" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New conversation/ })).not.toBeInTheDocument();
  });
});
