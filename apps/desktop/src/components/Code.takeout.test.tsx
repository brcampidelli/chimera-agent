import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getCodeSession,
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
  type CodeTurnHandlers,
  type CodeTurnInput,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Getting the conversation out of the window, and being told when it ends.
 *
 * Two small things with one property in common: both are about the moment the user is NOT looking
 * at the screen. A transcript is read later, by someone who was not there; a notification exists
 * only for the person who walked away.
 */
describe("Code — taking the conversation out", () => {
  function finishedTurn(_req: CodeTurnInput, h: CodeTurnHandlers) {
    h.onSession?.("s1");
    h.onDone?.({
      answer: "the answer",
      steps: 1,
      stopped_reason: "final",
      tool_names: [],
      model: "m",
      prompt_tokens: 1,
      completion_tokens: 1,
      usd: 0,
      context_peak_tokens: 1,
      route_meta: null,
    } as never);
    return Promise.resolve();
  }

  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(finishedTurn as never);
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function askOnce() {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "hello{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    return user;
  }

  it("offers the export only once there is something to export", async () => {
    // Rendered ONCE: the first version mounted `<Code />` twice and every query then found two
    // composers, which is a test failing on its own setup rather than on the thing under test.
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "hello{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: /export/i })).toBeInTheDocument();
  });

  it("says so when the stored session held turns this window never saw", async () => {
    // The load-bearing case. Exporting from memory alone would hand somebody a transcript missing
    // the middle — worse than none, because a file gets believed.
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1",
      workspace: "/w",
      exchanges: [
        { you: "older", answer: "1" },
        { you: "older still", answer: "2" },
        { you: "hello", answer: "the answer" },
      ],
    } as never);
    const user = await askOnce();
    await user.click(await screen.findByRole("button", { name: /export/i }));

    expect(await screen.findByText(/2 earlier turn/i)).toBeInTheDocument();
  });

  it("exports anyway, and says so, when the stored session cannot be read", async () => {
    // A transcript from memory is worth more than none — but the user has to know which one they
    // got, because the difference is invisible in the file.
    vi.mocked(getCodeSession).mockRejectedValue(new Error("offline"));
    const user = await askOnce();
    await user.click(await screen.findByRole("button", { name: /export/i }));

    expect(await screen.findByText(/this window only/i)).toBeInTheDocument();
  });

  it("does not notify while the user is watching the window", async () => {
    // A notification for something already on screen is the fastest way to have every notification
    // muted — including the one that mattered.
    const ctor = vi.fn();
    vi.stubGlobal("Notification", Object.assign(ctor, { permission: "granted", requestPermission: vi.fn() }));
    vi.spyOn(document, "hasFocus").mockReturnValue(true);

    localStorage.setItem("chimera.notifyOnFinish", "1");
    await askOnce();
    await new Promise((r) => setTimeout(r, 20));

    expect(ctor).not.toHaveBeenCalled();
  });

  it("notifies when the window is not focused", async () => {
    const ctor = vi.fn();
    vi.stubGlobal("Notification", Object.assign(ctor, { permission: "granted", requestPermission: vi.fn() }));
    vi.spyOn(document, "hasFocus").mockReturnValue(false);

    localStorage.setItem("chimera.notifyOnFinish", "1");
    await askOnce();

    await waitFor(() => expect(ctor).toHaveBeenCalled());
  });

  it("stays quiet unless the user asked for it", async () => {
    // Off by default. An app that starts sending desktop notifications unasked is one people turn
    // off entirely.
    const ctor = vi.fn();
    vi.stubGlobal("Notification", Object.assign(ctor, { permission: "granted", requestPermission: vi.fn() }));
    vi.spyOn(document, "hasFocus").mockReturnValue(false);

    await askOnce(); // localStorage cleared in beforeEach
    await new Promise((r) => setTimeout(r, 20));

    expect(ctor).not.toHaveBeenCalled();
  });

  it("survives a webview with no Notification API at all", async () => {
    // ⚠️ macOS is the reason this test exists and the reason it is not a proof: WKWebView has
    // historically not implemented `Notification`, and there was no Mac to check on. What is
    // verified is that its ABSENCE costs nothing — the turn still completes normally.
    vi.stubGlobal("Notification", undefined);
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    localStorage.setItem("chimera.notifyOnFinish", "1");

    await askOnce();
    expect(await screen.findByText("the answer")).toBeInTheDocument();
  });
});
