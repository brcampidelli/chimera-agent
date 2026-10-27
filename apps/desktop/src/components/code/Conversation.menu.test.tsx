import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { AppShell, DRAWER_BELOW_PX } from "@/components/shell/AppShell";
import { AgentProvider } from "@/lib/agent-context";
import { streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";
import { NOTIFY_ON_FINISH_KEY } from "@/lib/notify";
import { openConversationMenuItem } from "@/test/conversationMenu";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

function mount() {
  return renderWithProviders(
    <Conversation workspace="/proj" openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }} profile={"balanced" as never}
      onHandOff={() => {}} onBatch={() => {}} onEdited={() => {}} busyElsewhere={false}
      controls={null} onOpenFile={() => {}} />,
  );
}

/** A turn that finishes without the server ever naming a session: the transcript has an exchange and
 *  the header has no saved conversation to act on. */
async function sendTurnWithoutSession() {
  vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
    h.onDone?.({
      answer: "pronto", steps: 1, stopped_reason: "final", tool_names: [], model: "",
      prompt_tokens: 1, completion_tokens: 1, usd: 0.01, tainted: false, memory_facts_used: 0,
      memory_layer: "", fused: false,
    } as never);
  });
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox"), "oi{Enter}");
  await screen.findByText("pronto");
  return user;
}

describe("the conversation's ⋯ menu", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(streamCodeTurn).mockReset();
  });

  it("offers Export and Clear only once there is an exchange, even without a saved session", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByTestId("conversation-menu"));
    expect(screen.queryByRole("menuitem", { name: /export/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Clear" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    const turnUser = await sendTurnWithoutSession();
    expect(await openConversationMenuItem(turnUser, /export/i)).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Clear" })).toBeInTheDocument();
  });

  it("the notify and auto-continue settings are checkboxes whose state and effect follow the click", async () => {
    // Radix holds `pointer-events: none` on the page for a moment after a menu item closes the menu;
    // the check is off so the test clicks the menu the way it is reopened, not after the animation.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    mount();

    await user.click(screen.getByTestId("conversation-menu"));
    const notify = screen.getByTestId("notify-toggle");
    expect(notify).toHaveAttribute("role", "menuitemcheckbox");
    expect(notify).toHaveAttribute("aria-checked", "false");
    await user.click(notify);
    expect(localStorage.getItem(NOTIFY_ON_FINISH_KEY)).not.toBeNull();

    await user.click(screen.getByTestId("conversation-menu"));
    expect(screen.getByTestId("notify-toggle")).toHaveAttribute("aria-checked", "true");
    const autoContinue = screen.getByTestId("auto-continue-toggle");
    expect(autoContinue).toHaveAttribute("role", "menuitemcheckbox");
    expect(autoContinue).toHaveAttribute("aria-checked", "false");
    await user.click(autoContinue);
    expect(localStorage.getItem("chimera.autoContinue")).toBe("1");

    await user.click(screen.getByTestId("conversation-menu"));
    expect(screen.getByTestId("auto-continue-toggle")).toHaveAttribute("aria-checked", "true");
  });
});

/** Below the drawer breakpoint the right panel is a dialog: the menu's toggle opens and closes that
 *  dialog, and does not touch the layout's mode, which says nothing about a drawer on screen. */
describe("the right-panel toggle in the drawer layout", () => {
  beforeEach(() => {
    localStorage.clear();
    window.matchMedia = vi.fn((query: string) => ({
      matches: query === `(max-width: ${DRAWER_BELOW_PX - 1}px)`,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  });

  it("opens the drawer from the menu and closes it from the menu again", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AgentProvider>
        <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail">rail</nav>}
          inspector={<aside aria-label="activity">panel</aside>}>
          <Conversation workspace="/proj" openFile={null}
            posture={{ reach: "workspace" as never, approval: "ask" as never }} profile={"balanced" as never}
            onHandOff={() => {}} onBatch={() => {}} onEdited={() => {}} busyElsewhere={false}
            controls={null} onOpenFile={() => {}} />
        </AppShell>
      </AgentProvider>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(await openConversationMenuItem(user, "Show side panel"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    // The dialog is modal, so the menu is not reachable while it is open; closing it is Escape or the
    // backdrop. The toggle then reads "show" again, and the same item opens it once more.
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(await openConversationMenuItem(user, "Show side panel"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
