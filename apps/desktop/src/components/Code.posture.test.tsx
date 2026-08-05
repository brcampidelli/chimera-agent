import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

describe("Code — reach & approval", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("starts at the deliberate default, not at the permissive corner", async () => {
    // Full reach with no questions has to be somewhere a user CHOSE to be. Two selectors instead of
    // one slider exist so it cannot be somewhere they slid past.
    renderWithProviders(<Code />);
    await waitFor(() => expect(getPostureFacts).toHaveBeenCalled());
    expect(vi.mocked(getPostureFacts).mock.calls[0].slice(0, 2)).toEqual([
      "workspace",
      "suspicious",
    ]);
  });

  it("says what the posture means here rather than echoing the setting back", async () => {
    vi.mocked(getPostureFacts).mockResolvedValue(
      postureFacts({ shell: "isolated", pauses: "tainted" }),
    );
    renderWithProviders(<Code />);

    expect(await screen.findByText(/Edits inside \/repo\./)).toBeInTheDocument();
    expect(screen.getByText(/Commands run in a container/)).toBeInTheDocument();
  });

  it("says YOUR machine, loudly, when a configured container is not running", async () => {
    // The one case where the honest answer contradicts the user's setup. Reading the config here
    // instead of the live sandbox is exactly how "I thought it was sandboxed" happens.
    vi.mocked(getPostureFacts).mockResolvedValue(
      postureFacts({ shell: "host", fell_back_to_host: true }),
    );
    renderWithProviders(<Code />);

    expect(await screen.findByText(/Commands run on YOUR machine\./)).toBeInTheDocument();
    expect(screen.getByText(/A container was configured, but none is running/)).toBeInTheDocument();
  });

  it("re-asks what the new posture means when either axis changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await waitFor(() => expect(getPostureFacts).toHaveBeenCalled());

    const lastCall = () => {
      const calls = vi.mocked(getPostureFacts).mock.calls;
      return calls[calls.length - 1];
    };

    await user.click(screen.getByRole("button", { name: "workspace + shell" }));
    await waitFor(() => expect(lastCall()[0]).toBe("workspace_shell"));

    await user.click(screen.getByRole("button", { name: "never" }));
    await waitFor(() => expect(lastCall()[1]).toBe("never"));
  });

  it("sends the chosen posture with a conversation turn", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.click(screen.getByRole("button", { name: "read only" }));
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "what does this do?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].posture).toEqual({
      reach: "read_only",
      approval: "suspicious",
    });
  });

  it("says nothing is written under read-only", async () => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts({ writes: "nothing", shell: "none" }));
    renderWithProviders(<Code />);

    expect(await screen.findByText(/Reads only — changes nothing\./)).toBeInTheDocument();
  });

  it("reports an unknown posture instead of staying silent", async () => {
    // Silence would read as "nothing to worry about", which is the opposite of what not knowing
    // how far the agent can reach actually means.
    vi.mocked(getPostureFacts).mockRejectedValue(new Error("500"));
    renderWithProviders(<Code />);

    expect(
      await screen.findByText(/Could not determine what this posture means here/),
    ).toBeInTheDocument();
  });
});
