import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getCodeSession, getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { WORKSPACE_KEY, writeLastSession } from "@/lib/workspace";
import type { OutputStyle } from "@/lib/types";
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
    vi.mocked(getCodeSession).mockReset().mockResolvedValue({ id: "s1", workspace: "/w", exchanges: [] });
    writeLastSession("/repo", null);
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

  it("resumes a conversation in the style its last turn was written in", async () => {
    // Every app start resumes the project's last conversation. With the style held only on screen it
    // came back in Standard, and the next turn went out with a system prompt nobody had chosen.
    writeLastSession("/repo", "s7");
    vi.mocked(getCodeSession).mockResolvedValue(storedWith(["concise"]));
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    const group = await screen.findByRole("group", { name: /^Style$/ });
    await waitFor(() =>
      expect(within(group).getByRole("button", { name: "Concise" })).toHaveAttribute("aria-pressed", "true"),
    );
    await send(user);
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).toMatchObject({ style: "concise", session_id: "s7" });
  });

  it("resumes in the default when the last turn's receipt names no style", async () => {
    // An earlier turn in Concise does not outrank the later one that ran under the default.
    writeLastSession("/repo", "s7");
    vi.mocked(getCodeSession).mockResolvedValue(storedWith(["concise", undefined]));
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await screen.findByText("turn 2");
    await send(user);
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).not.toHaveProperty("style");
  });
});

/** A stored conversation whose turns' receipts carried these styles, in order. */
function storedWith(styles: (OutputStyle | undefined)[]) {
  return {
    id: "s7",
    workspace: "/repo",
    exchanges: styles.map((style, i) => ({
      you: `turn ${i + 1}`,
      answer: "ok",
      tools: [],
      edits: [],
      done: {
        answer: "ok", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 0,
        completion_tokens: 0, usd: null, context_peak_tokens: 0, route_meta: null,
        ...(style ? { style, style_version: 1 } : {}),
      },
      verified: null,
    })),
  } as never;
}
