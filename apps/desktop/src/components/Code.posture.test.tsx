import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

describe("Code — reach & approval", () => {
  beforeEach(() => {
    // The sentence names a directory the agent may edit, so it only exists once a project does.
    // Without one it used to name the app's own launch directory — announcing write access to a
    // folder nobody picked. Every test here is about what the sentence SAYS, so they all need a
    // project chosen.
    localStorage.setItem(WORKSPACE_KEY, "/repo");
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

  it("re-asks when the project changes, because the answer is about a folder", async () => {
    // This replaces a test that changed the posture with the selectors. The axes are fixed now, so
    // the claim it protected — "the sentence is re-derived rather than cached" — moved to the input
    // that CAN still change it. Caching this answer is the bug the endpoint was written to avoid: a
    // Docker daemon that died since the last call must change what the line says.
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await waitFor(() => expect(getPostureFacts).toHaveBeenCalled());

    const field = await screen.findByPlaceholderText(/folder path/i);
    await user.clear(field);
    await user.type(field, "/home/me/other");
    await user.click(screen.getByRole("button", { name: /open|abrir/i }));

    await waitFor(() => {
      const calls = vi.mocked(getPostureFacts).mock.calls;
      expect(calls[calls.length - 1][2]).toBe("/home/me/other");
    });
  });

  it("SENDS the posture with every turn rather than letting the server default it", async () => {
    // The selectors are gone; sending them is not. Omitting `posture` does not mean "apply the
    // default" — it resolves to no tool denials at all (the shell tools return to the registry) and
    // no pause under any circumstance, which is more permissive than any corner of the grid a user
    // could once have picked. Deleting a control must not quietly widen what the agent may do.
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "what does this do?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].posture).toEqual({
      reach: "workspace",
      approval: "suspicious",
    });
  });

  it("says nothing is written when the server reports read-only", async () => {
    // Asserted on the SERVER's answer, not on a selection — which is the point of the line: it
    // reports what is true on this machine rather than echoing back what was configured.
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
