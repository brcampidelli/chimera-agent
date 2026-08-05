import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRoleModels,
  getRuns,
  streamCodeTurn,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, roleModels, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

describe("Code — models by role", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getRoleModels).mockResolvedValue(roleModels());
  });

  it("says routing is unmeasured, on the screen, next to the control", async () => {
    // Every competitor that claims something like this is also unmeasured. Shipping the selector
    // without saying so would put this project back in the company it spent a month getting out of.
    renderWithProviders(<Code />);
    expect(await screen.findByText(/NOT yet measured/)).toBeInTheDocument();
    expect(screen.getByText(/bench\/role_routing/)).toBeInTheDocument();
  });

  it("shows the model each role resolves to, from the server", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.click(await screen.findByText("which model does what"));

    expect(await screen.findByText("vendor/weak")).toBeInTheDocument(); // explore
    expect(screen.getByText("vendor/mid")).toBeInTheDocument(); // edit
  });

  it("marks a panel on the tool-free turns and nowhere else", async () => {
    // A "fuse" on the coding loop would never fire — the router sends any turn carrying tool
    // schemas to a single model — and would report that it had. Plan and review have no tools.
    vi.mocked(getRoleModels).mockResolvedValue(roleModels({ fuse_plan: true, fuse_review: true }));
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.click(await screen.findByText("which model does what"));

    await waitFor(() => expect(screen.getAllByText("· panel")).toHaveLength(2));
  });

  it("gives Verify no model at all, and says why", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.click(await screen.findByText("which model does what"));

    expect(
      await screen.findByText(/runs your command — no model, nothing to choose/),
    ).toBeInTheDocument();
  });

  it("re-resolves the roles when the profile changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await waitFor(() => expect(getRoleModels).toHaveBeenCalledWith("balanced"));

    await user.click(screen.getByRole("button", { name: "economy" }));
    await waitFor(() => expect(getRoleModels).toHaveBeenCalledWith("economy"));
  });

  it("sends the chosen profile with a conversation turn", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.click(screen.getByRole("button", { name: "max" }));
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "refactor this");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].profile).toBe("max");
  });
});
