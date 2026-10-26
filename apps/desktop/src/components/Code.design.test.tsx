import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import {
  getDesign,
  getGitStatus,
  getPostureFacts,
  getRuns,
  listDesigns,
  streamCodeTurn,
  streamRun,
  type CodeTurnHandlers,
} from "@/lib/api";
import { queueAppCommand } from "@/lib/appCommands";
import type { CodeDesignFrame, CodeDesignSuggestion } from "@/lib/design/frames";
import { gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { sampleDocument } from "@/test/design-fixture";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const FRAME: CodeDesignFrame = { document_id: "doc_9", title: "Café landing", revision: 3, ops_applied: 4, action: "apply" };

/** A turn whose stream carries the two Chimera Design frames, then finishes as usual. */
function designTurn(suggestion: CodeDesignSuggestion | null, frame: CodeDesignFrame | null = null) {
  const rest = scriptTurn();
  return async (req: unknown, h: CodeTurnHandlers) => {
    if (suggestion) h.onDesignSuggestion?.(suggestion);
    if (frame) h.onDesign?.(frame);
    return rest(req, h);
  };
}

async function send(text: string) {
  const user = userEvent.setup();
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/Ask about this code/i), text);
  await user.click(screen.getByRole("button", { name: /Send/ }));
  return user;
}

beforeEach(() => {
  vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
  vi.mocked(getRuns).mockResolvedValue([]);
  vi.mocked(streamRun).mockImplementation(async () => {});
  vi.mocked(listDesigns).mockResolvedValue([]);
  vi.mocked(getDesign).mockResolvedValue({ ...sampleDocument(), id: "doc_9", title: "Café landing", revision: 3 });
});

describe("Chimera Design in the Code screen", () => {
  it("on a suggestion shows the card only, and the card opens the design list with the brief", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(designTurn({ level: "suggest", reason: "design_with_visual_signal" }));
    const user = await send("design a landing screen for the café");

    const open = await screen.findByRole("button", { name: "Open in Chimera Design" });
    expect(screen.getByText(/sounds like a visual design/)).toBeInTheDocument();
    // A suggestion never opens the canvas by itself.
    expect(screen.queryByRole("region", { name: "Chimera Design" })).toBeNull();
    expect(listDesigns).not.toHaveBeenCalled();

    await user.click(open);
    expect(await screen.findByRole("button", { name: "New design from this brief" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Chimera Design" })).toHaveTextContent("design a landing screen for the café");
  });

  it("on an explicit ask opens Chimera Design beside the conversation at once", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(designTurn({ level: "explicit", reason: "named" }));
    await send("abre o Chimera Design para a landing");
    expect(await screen.findByRole("button", { name: "New design from this brief" })).toBeInTheDocument();
    expect(screen.getByText(/open beside the conversation/)).toBeInTheDocument();
  });

  it("opens an agent-created design automatically, replaces a different design, and respects a mid-turn close", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(designTurn(null, FRAME));
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "make the hero bigger");
    await user.click(screen.getByRole("button", { name: /Send/ }));
    await screen.findByRole("application");
    await waitFor(() => expect(getDesign).toHaveBeenCalledWith("doc_9"));

    const second = { ...FRAME, document_id: "doc_10", title: "Another design" };
    vi.mocked(streamCodeTurn).mockImplementation(designTurn(null, second));
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "update another design");
    await user.click(screen.getByRole("button", { name: /Send/ }));
    await waitFor(() => expect(getDesign).toHaveBeenCalledWith("doc_10"));

    let releaseSecondFrame: (() => void) | undefined;
    const frameGate = new Promise<void>((resolve) => { releaseSecondFrame = resolve; });
    vi.mocked(streamCodeTurn).mockImplementation(async (req, handlers) => {
      handlers.onDesign?.(second);
      await frameGate;
      handlers.onDesign?.({ ...second, revision: 4 });
      return scriptTurn()(req, handlers);
    });
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "another change");
    await user.click(screen.getByRole("button", { name: /Send/ }));
    await screen.findByRole("application");
    const closeDesignButtons = screen.getAllByRole("button", { name: "Close Chimera Design" });
    await user.click(closeDesignButtons[closeDesignButtons.length - 1]);
    const loadsBeforeLateFrame = vi.mocked(getDesign).mock.calls.length;
    releaseSecondFrame?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(vi.mocked(getDesign).mock.calls).toHaveLength(loadsBeforeLateFrame);
  });

  it("shows a compact card for a design frame, and the card opens that design", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(designTurn(null, FRAME));
    const user = await send("make the hero bigger");
    const card = await screen.findByRole("button", { name: "Open design" });
    expect(card).toHaveTextContent("Café landing");
    expect(card).toHaveTextContent("Changed · revision 3");
    await user.click(await screen.findByRole("button", { name: "Close Chimera Design" }));
    const loadsBeforeClick = vi.mocked(getDesign).mock.calls.length;
    await user.click(card);
    await waitFor(() => {
      expect(vi.mocked(getDesign).mock.calls.length).toBeGreaterThan(loadsBeforeClick);
      expect(getDesign).toHaveBeenLastCalledWith("doc_9");
    });
    expect(await screen.findByRole("application")).toBeInTheDocument();
  });

  it("stacked, switches between the conversation and the canvas without unmounting either", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(designTurn(null, FRAME));
    const user = await send("make the hero bigger");
    await user.click(await screen.findByRole("button", { name: "Open design" }));
    await screen.findByRole("application");

    const main = document.querySelector("main") as HTMLElement;
    const pane = screen.getByRole("region", { name: "Chimera Design" });
    const column = pane.parentElement as HTMLElement;
    expect(main.className).toContain("max-lg:hidden");
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "half a thought");

    await user.click(screen.getByRole("tab", { name: "Conversation" }));
    expect(main.className).not.toContain("max-lg:hidden");
    expect(column.className).toContain("max-lg:hidden");
    // Nothing was unmounted: the draft and the canvas are both still there.
    expect(screen.getByPlaceholderText(/Ask about this code/i)).toHaveValue("half a thought");
    expect(screen.getByRole("application")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Chimera Design" }));
    expect(main.className).toContain("max-lg:hidden");
    expect(column.className).not.toContain("max-lg:hidden");
  });

  it("opens the design list from the palette's Open design", async () => {
    renderWithProviders(<Code />);
    act(() => queueAppCommand("openDesign"));
    expect(await screen.findByRole("region", { name: "Chimera Design" })).toBeInTheDocument();
    expect(await screen.findByText("No designs yet. Start one with New design.")).toBeInTheDocument();
  });

  it("opens the design a share link names, and takes the link off the address once read", async () => {
    window.history.replaceState({}, "", "/?design=doc_9");
    renderWithProviders(<Code />);
    await waitFor(() => expect(getDesign).toHaveBeenCalledWith("doc_9"));
    expect(await screen.findByRole("application")).toBeInTheDocument();
    expect(window.location.search).toBe("");
    window.history.replaceState({}, "", "/");
  });

  it("loads the canvas lazily, never with the screen", () => {
    const source = readFileSync(join(__dirname, "Code.tsx"), "utf8");
    expect(source).toMatch(/lazy\(\(\) => import\("@\/components\/design\/DesignPane"\)\)/);
    expect(source).not.toMatch(/^import [^\n]*\bDesignPane\b[^\n]*from "@\/components\/design\/DesignPane";/m);
  });
});

describe("the drawing status on the Code screen", () => {
  it("is not shown for a turn that never touches the design", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(designTurn(null, FRAME));
    const user = await send("make the hero bigger");
    await screen.findByRole("application");

    // A plain turn with the design open, held open: nothing on it draws, so nothing says it does.
    vi.mocked(streamCodeTurn).mockImplementation(() => new Promise<void>(() => {}));
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "explain the router");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument());
    expect(screen.queryByTestId("design-drawing-elapsed")).toBeNull();
  });

  it("shows while the agent draws and clears when the person stops the turn", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(async (_req, handlers) => {
      handlers.onDesign?.(FRAME);
      return new Promise<void>(() => {});
    });
    const user = await send("make the hero bigger");
    expect(await screen.findByTestId("design-drawing-elapsed")).toBeInTheDocument();
    expect(screen.getByText("Creating…")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /stop/i }));
    await waitFor(() => expect(screen.queryByTestId("design-drawing-elapsed")).toBeNull());
  });
});
