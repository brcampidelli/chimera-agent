import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The style chip in the composer's settings (study 29, P4.5): per conversation, starting on the
 * default, which sends nothing. The assertions are about the REQUEST — a chip that changes colour and
 * sends the same body would be a promise the screen does not keep.
 */
describe("Code — the conversation's output style", () => {
  beforeEach(() => {
    localStorage.setItem(WORKSPACE_KEY, "/repo");
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockReset().mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  async function send(user: ReturnType<typeof userEvent.setup>, text = "oi") {
    await user.type(await screen.findByPlaceholderText(/Ask about this code/i), text);
    await user.click(screen.getByRole("button", { name: /^Send$/i }));
  }

  it("starts on the standard style and sends no style at all", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    const group = await screen.findByRole("group", { name: /^Style$/ });
    expect(within(group).getByRole("button", { name: "Standard" })).toHaveAttribute("aria-pressed", "true");
    await send(user);

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).not.toHaveProperty("style");
  });

  it("sends the picked style with the next message, and a new conversation starts on the default again", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    const group = await screen.findByRole("group", { name: /^Style$/ });
    await user.click(within(group).getByRole("button", { name: "Concise" }));
    expect(within(group).getByRole("button", { name: "Concise" })).toHaveAttribute("aria-pressed", "true");
    await send(user);
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).toMatchObject({ style: "concise" });

    await user.click(screen.getByRole("button", { name: /new conversation/i }));
    await send(user, "again");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0]).not.toHaveProperty("style");
  });
});
