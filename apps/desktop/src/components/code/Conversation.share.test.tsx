import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  openNetworkShare,
  shareSession,
  streamCodeTurn,
  streamSessionLive,
  type CodeTurnHandlers,
  type SessionLiveFrame,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The owner's side of a shared conversation.
 *
 * What is pinned: a conversation with a link opens the live window and a conversation without one
 * does not; a turn a guest started arrives as its own exchange, named, and its answer streams into
 * it; the owner's own turn — whose frames come twice, on its stream and on the conversation's — is
 * drawn once; a question a guest's turn raises lands on THIS screen's card; the presence line names
 * who is here; a reopened conversation names the guest on the turn the guest asked; and the Share
 * panel says the sentence before a link can be made.
 */

function mount(resume?: string) {
  return renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      resumeSession={resume}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={() => {}}
    />,
  );
}

function live(seq: number, event: string, turnId: string, author: string, payload: Record<string, unknown> = {}): SessionLiveFrame {
  return { session_seq: seq, event, turn_id: turnId, author, payload };
}

/** Capture the live window's frame handler, keeping the stream "open" until the test ends. */
function openLiveWindow() {
  let onFrame: ((f: SessionLiveFrame) => void) | null = null;
  vi.mocked(streamSessionLive).mockImplementation((_sid, _since, handler) => {
    onFrame = handler;
    return new Promise<string | null>(() => {});
  });
  return () => onFrame;
}

describe("the owner's side of a shared conversation", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(getCodeSession).mockReset().mockResolvedValue({ id: "s1", workspace: "/w", exchanges: [] });
    localStorage.clear();
  });

  it("opens the live window only for a conversation that has a link", async () => {
    mount("s1");
    await waitFor(() => expect(listShares).toHaveBeenCalledWith("s1"));
    await new Promise((r) => setTimeout(r, 20));
    expect(streamSessionLive).not.toHaveBeenCalled();

    vi.mocked(listShares).mockResolvedValue({
      shares: [{ token: "tok", session_id: "s2", created_at: 1, label: "Ana", url: null }],
    });
    mount("s2");
    await waitFor(() => expect(streamSessionLive).toHaveBeenCalledWith("s2", 0, expect.any(Function), expect.anything()));
  });

  it("draws a guest's turn as its own exchange, named, and streams its answer into it", async () => {
    vi.mocked(listShares).mockResolvedValue({
      shares: [{ token: "tok", session_id: "s1", created_at: 1, label: "", url: null }],
    });
    const handler = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(handler()).not.toBeNull());

    act(() => {
      handler()!(live(1, "turn_started", "t-guest", "Ana", { message: "and the logout?" }));
      handler()!(live(2, "token", "t-guest", "Ana", { text: "Done: " }));
      handler()!(live(3, "token", "t-guest", "Ana", { text: "logout added." }));
    });
    expect(screen.getByText("and the logout?")).toBeInTheDocument();
    expect(screen.getByTestId("exchange-author")).toHaveTextContent("Ana");
    expect(screen.getByText("Done: logout added.")).toBeInTheDocument();

    act(() => {
      handler()!(live(4, "done", "t-guest", "Ana", { answer: "Done: logout added.", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 1, completion_tokens: 1, usd: null, author: "Ana" }));
      // The same frame again — a replay after a cut — changes nothing.
      handler()!(live(4, "done", "t-guest", "Ana", { answer: "Done: logout added.", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 1, completion_tokens: 1, usd: null, author: "Ana" }));
      handler()!(live(1, "turn_started", "t-guest", "Ana", { message: "and the logout?" }));
    });
    expect(screen.getAllByText("and the logout?")).toHaveLength(1);
  });

  it("draws the owner's own turn once, though its frames arrive on both streams", async () => {
    vi.mocked(listShares).mockResolvedValue({
      shares: [{ token: "tok", session_id: "s1", created_at: 1, label: "", url: null }],
    });
    const handler = openLiveWindow();
    let turnHandlers: CodeTurnHandlers | null = null;
    vi.mocked(streamCodeTurn).mockImplementation(async (_req, h) => {
      turnHandlers = h;
      h.onSession?.("s1", "t-own");
      h.onToken?.("Hello ");
    });
    mount("s1");
    await waitFor(() => expect(handler()).not.toBeNull());
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "say hello{Enter}");
    await waitFor(() => expect(turnHandlers).not.toBeNull());

    act(() => {
      handler()!(live(1, "turn_started", "t-own", "", { message: "say hello" }));
      handler()!(live(2, "token", "t-own", "", { text: "Hello " }));
    });
    expect(screen.getAllByText("say hello")).toHaveLength(1);
    expect(screen.queryByTestId("exchange-author")).not.toBeInTheDocument();
    expect(screen.getAllByText("Hello")).toHaveLength(1);
  });

  it("puts a question a guest's turn raised on this screen's card, and names who is here", async () => {
    vi.mocked(listShares).mockResolvedValue({
      shares: [{ token: "tok", session_id: "s1", created_at: 1, label: "", url: null }],
    });
    const handler = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(handler()).not.toBeNull());
    act(() => {
      handler()!(live(1, "presence", "", "", { names: ["owner", "Ana"] }));
      handler()!(live(2, "turn_started", "t-guest", "Ana", { message: "delete the cache" }));
      handler()!(live(3, "approval", "t-guest", "Ana", {
        id: "q1", action: "rm -rf .cache", reason: "review", asked_at: 1, wait_seconds: 300, decision: "review",
      }));
    });
    expect(screen.getByTestId("presence-line")).toHaveTextContent("Ana");
    expect(screen.getByTestId("presence-line")).not.toHaveTextContent("owner");
    // The card the owner answers; the guest app has no route to.
    expect(screen.getByText("rm -rf .cache")).toBeInTheDocument();
  });

  it("names the guest on a reopened conversation's turn, from the receipt", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1",
      workspace: "/w",
      exchanges: [
        { you: "fix the login", answer: "done", tools: [], edits: [], done: { answer: "done", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 1, completion_tokens: 1, usd: null } as never, verified: null },
        { you: "and the logout", answer: "also done", tools: [], edits: [], done: { answer: "also done", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 1, completion_tokens: 1, usd: null, author: "Ana" } as never, verified: null },
      ],
    });
    mount("s1");
    await waitFor(() => expect(screen.getByText("and the logout")).toBeInTheDocument());
    expect(screen.getAllByTestId("exchange-author")).toHaveLength(1);
    expect(screen.getByTestId("exchange-author")).toHaveTextContent("Ana");
  });

  it("says the sentence in the Share panel, and minting a link is one click after it", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/w",
      exchanges: [{ you: "hi", answer: "hello", tools: [], edits: [], done: null, verified: null }],
    });
    vi.mocked(shareSession).mockResolvedValue({ token: "new-tok", session_id: "s1", created_at: 1, label: "", url: null });
    mount("s1");
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("share-button"));
    const panel = await screen.findByTestId("share-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId("share-warning")).toHaveTextContent(/anything you can ask it/i);
    expect(screen.getByTestId("share-door-closed")).toBeInTheDocument();
    expect(openNetworkShare).not.toHaveBeenCalled();

    vi.mocked(listShares).mockResolvedValue({
      shares: [{ token: "new-tok", session_id: "s1", created_at: 1, label: "", url: null }],
    });
    await user.click(screen.getByRole("button", { name: /new link/i }));
    await waitFor(() => expect(shareSession).toHaveBeenCalledWith("s1", ""));
    await waitFor(() => expect(screen.getByTestId("share-list")).toBeInTheDocument());
    // No address until the door is open — and the live window opens now that a link exists.
    expect(screen.getByText(/no address until/i)).toBeInTheDocument();
    await waitFor(() => expect(streamSessionLive).toHaveBeenCalled());
  });
});
