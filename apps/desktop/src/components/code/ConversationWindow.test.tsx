import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { ConversationWindow } from "@/components/code/ConversationWindow";
import {
  getCodeSession,
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { I18nProvider } from "@/lib/i18n";
import { STORAGE_KEY } from "@/lib/layout/store";
import { TooltipProvider } from "@/components/ui/tooltip";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Two conversations at once, each in its own window (the "later" item of the review of several
 * conversations at once, 2026-09-30).
 *
 * The server keeps turns of different conversations apart; the screen had one centre column. So a
 * conversation can open in a window of its own, drawn by the same page asked `?conversation=<id>`.
 * What must hold: the window draws THAT conversation, in the project it belongs to, and sends its
 * turns there; it does not write the person's layout; and the main screen opens it by that address.
 */
describe("a conversation in a window of its own", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s9",
      workspace: "/projects/shop",
      exchanges: [],
    } as unknown as Awaited<ReturnType<typeof getCodeSession>>);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ session: "s9" }));
  });

  it("draws that conversation and sends its turns to it, in its own project", async () => {
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<ConversationWindow sessionId="s9" />);

    await waitFor(() => expect(getCodeSession).toHaveBeenCalledWith("s9"));
    await user.type(await screen.findByPlaceholderText(/^Ask about this code/), "continue");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).toMatchObject({
      session_id: "s9",
      workspace: "/projects/shop",
    });
    expect(document.title).toBe("shop · Chimera");
  });

  it("keeps the style the conversation was last answered in", async () => {
    // The same conversation opened in its own window: written the way it was, not quietly reset.
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s9",
      workspace: "/projects/shop",
      exchanges: [
        {
          you: "explain the cart",
          answer: "ok",
          tools: [],
          edits: [],
          done: {
            answer: "ok", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 0,
            completion_tokens: 0, usd: null, context_peak_tokens: 0, route_meta: null,
            style: "explanatory", style_version: 1,
          },
          verified: null,
        },
      ],
    } as unknown as Awaited<ReturnType<typeof getCodeSession>>);
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<ConversationWindow sessionId="s9" />);

    await screen.findByText("explain the cart");
    await user.type(await screen.findByPlaceholderText(/^Ask about this code/), "and the checkout");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    const calls = vi.mocked(streamCodeTurn).mock.calls;
    expect(calls[calls.length - 1][0]).toMatchObject({ style: "explanatory" });
  });

  it("reads the person's layout and writes none of it", async () => {
    // Mounted as `main.tsx` mounts it: no layout of the app's around it, only its own. The test
    // wrapper brings one that writes on mount, which would hide whether the window's does.
    localStorage.removeItem(STORAGE_KEY);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <TooltipProvider>
            <ConversationWindow sessionId="s9" />
          </TooltipProvider>
        </I18nProvider>
      </QueryClientProvider>,
    );
    await screen.findByPlaceholderText(/^Ask about this code/);

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("offers no further window from inside one", async () => {
    // A stored exchange, so the header's buttons are drawn at all: with none, the absence below
    // would hold for any window, whatever it offered.
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s9",
      workspace: "/projects/shop",
      exchanges: [{ you: "hi", answer: "hello", tools: [], edits: [], done: null, verified: null }],
    } as unknown as Awaited<ReturnType<typeof getCodeSession>>);
    renderWithProviders(<ConversationWindow sessionId="s9" />);

    expect(await screen.findByRole("button", { name: /export/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open in a new window/i })).not.toBeInTheDocument();
  });

  it("is opened from the main screen by the conversation's address", async () => {
    const opened = vi.spyOn(window, "open").mockReturnValue(null);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ session: "s1" }));
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await user.click(await screen.findByRole("button", { name: /Open in a new window/i }));

    // With a size: without one the shell opened it at the webview's default, measured live on
    // 2026-09-30 as too short for the transcript to get any height under the composer.
    expect(opened).toHaveBeenCalledWith(
      `${window.location.origin}/?conversation=s1`,
      "chimera-conversation-s1",
      expect.stringMatching(/width=\d+,height=\d+/),
    );
    opened.mockRestore();
  });
});
