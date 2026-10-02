import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { HideRegionButton } from "@/components/shell/RegionToggle";
import { AgentProvider } from "@/lib/agent-context";
import { useLayout } from "@/lib/layout/context";
import type { LayoutAction } from "@/lib/layout/model";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
}));

/**
 * Phase 1 of the dynamic screen: the rail and the right panel can be hidden, and a hidden one leaves a
 * tab on its edge that brings it back. The agent's state is not hidden with the panel: the status bar
 * stays, which is one of the five things the owner confirmed never disappear.
 */
let dispatch: ((a: LayoutAction) => boolean) | null = null;

function Shell({ inspector = true }: { inspector?: boolean }) {
  dispatch = useLayout().dispatch;
  return (
    <AppShell
      viewKey="code"
      viewLabel="Code"
      rail={<nav aria-label="rail">rail</nav>}
      inspector={
        inspector ? (
          <aside aria-label="activity">
            <HideRegionButton side="right" />
          </aside>
        ) : undefined
      }
    >
      <p>conversation</p>
    </AppShell>
  );
}

function mount(inspector = true) {
  return renderWithProviders(
    <AgentProvider>
      <Shell inspector={inspector} />
    </AgentProvider>,
  );
}

describe("hiding and showing the side regions of the shell", () => {
  beforeEach(() => {
    localStorage.clear();
    dispatch = null;
  });

  it("shows the rail and the right panel by default, with no edge tabs", () => {
    mount();

    expect(screen.getByRole("navigation", { name: "rail" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "activity" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Show the/ })).not.toBeInTheDocument();
  });

  it("hides the right panel from its own button, hands focus to the tab, and the tab brings it back", async () => {
    mount();

    await userEvent.click(screen.getByRole("button", { name: "Hide the right panel" }));

    expect(screen.queryByRole("complementary", { name: "activity" })).not.toBeInTheDocument();
    const tab = screen.getByRole("button", { name: "Show the right panel" });
    await waitFor(() => expect(tab).toHaveFocus());

    await userEvent.click(tab);
    const back = screen.getByRole("complementary", { name: "activity" });
    // It slides back in from its own edge, and only because it came back.
    expect(back.parentElement).toHaveClass("region-enter-right");
  });

  it("keeps the status bar when the right panel is hidden", async () => {
    mount();

    await userEvent.click(screen.getByRole("button", { name: "Hide the right panel" }));

    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  it("hides the rail and leaves a tab for it", async () => {
    mount();

    act(() => void dispatch?.({ type: "set-region", region: "rail", visible: false }));

    expect(screen.queryByRole("navigation", { name: "rail" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show the screen rail" }));
    expect(screen.getByRole("navigation", { name: "rail" })).toBeInTheDocument();
  });

  it("does not animate a panel that was simply there when the screen opened", () => {
    mount();

    expect(screen.getByRole("complementary", { name: "activity" }).parentElement).not.toHaveClass("region-enter-right");
  });

  it("shows no right-hand tab on a screen that has no right panel", () => {
    mount(false);

    act(() => void dispatch?.({ type: "set-region", region: "right", visible: false }));

    expect(screen.queryByRole("button", { name: "Show the right panel" })).not.toBeInTheDocument();
  });

  it("lists a hidden region in the status bar's hidden-panels tray", async () => {
    mount();

    await userEvent.click(screen.getByRole("button", { name: "Hide the right panel" }));

    expect(screen.getByRole("button", { name: "Hidden: 1" })).toBeInTheDocument();
  });

  it("hides a screen's own left sidebar with the left region, and its tab brings it back (phase 6)", async () => {
    function WithContext() {
      dispatch = useLayout().dispatch;
      return (
        <AppShell
          viewKey="edit"
          viewLabel="Edit"
          rail={<nav aria-label="rail">rail</nav>}
          context={<aside aria-label="editor sidebar">files</aside>}
        >
          <p>editor</p>
        </AppShell>
      );
    }
    renderWithProviders(
      <AgentProvider>
        <WithContext />
      </AgentProvider>,
    );
    expect(screen.getByRole("complementary", { name: "editor sidebar" })).toBeInTheDocument();

    act(() => void dispatch?.({ type: "set-region", region: "left", visible: false }));
    expect(screen.queryByRole("complementary", { name: "editor sidebar" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show the left sidebar" }));
    const back = screen.getByRole("complementary", { name: "editor sidebar" });
    expect(back.parentElement).toHaveClass("region-enter-left");
  });
});

