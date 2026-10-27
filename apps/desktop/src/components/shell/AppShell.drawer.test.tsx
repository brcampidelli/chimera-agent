import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as RadixDialog from "@radix-ui/react-dialog";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell, DRAWER_BELOW_PX } from "@/components/shell/AppShell";
import { edgeTabId } from "@/components/shell/RegionToggle";
import { AgentProvider } from "@/lib/agent-context";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
}));

/** A window narrower than the drawer breakpoint, or wider, as the browser would answer the query. */
function windowIs(narrow: boolean) {
  window.matchMedia = vi.fn((query: string) => ({
    matches: narrow && query === `(max-width: ${DRAWER_BELOW_PX - 1}px)`,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

/** A dialog opened from inside the panel, to prove Escape goes to the topmost layer only. */
function NestedDialog() {
  return (
    <RadixDialog.Root>
      <RadixDialog.Trigger>open inner</RadixDialog.Trigger>
      <RadixDialog.Portal>
        <RadixDialog.Content aria-describedby={undefined}>
          <RadixDialog.Title>inner</RadixDialog.Title>
          <p>inner body</p>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

function mount(inspector = <aside aria-label="activity">panel</aside>) {
  return renderWithProviders(
    <AgentProvider>
      <AppShell
        viewKey="code"
        viewLabel="Code"
        rail={<nav aria-label="rail">rail</nav>}
        inspector={inspector}
      >
        <p>conversation</p>
      </AppShell>
    </AgentProvider>,
  );
}

const edgeTab = () => document.getElementById(edgeTabId("right")) as HTMLElement;

/**
 * Below the drawer breakpoint the right panel is a modal dialog over the conversation. It starts
 * closed behind its edge tab; opening it moves focus inside, Escape closes it (and only it), and
 * closing returns focus to the edge tab. At and above the breakpoint nothing changes.
 */
describe("the right panel below the drawer breakpoint", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts closed, leaving its edge tab, when the window is narrow", () => {
    windowIs(true);
    mount();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("activity")).not.toBeInTheDocument();
    expect(edgeTab()).toBeInTheDocument();
  });

  it("opens as a modal dialog from the edge tab, with focus moved inside it", async () => {
    windowIs(true);
    const user = userEvent.setup();
    mount();

    await user.click(edgeTab());
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByLabelText("activity")).toBeInTheDocument();
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("closes on Escape and returns focus to the edge tab", async () => {
    windowIs(true);
    const user = userEvent.setup();
    mount();

    await user.click(edgeTab());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(edgeTab());
  });

  it("closes from the backdrop, which is not a tab stop", async () => {
    windowIs(true);
    const user = userEvent.setup();
    mount();

    await user.click(edgeTab());
    const backdrop = document.querySelector(".backdrop") as HTMLElement;
    expect(backdrop).toBeInTheDocument();
    expect(backdrop.getAttribute("tabindex")).toBeNull();
    await user.click(backdrop);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Escape closes only the topmost dialog: a dialog opened from the panel leaves the drawer open", async () => {
    windowIs(true);
    const user = userEvent.setup();
    mount(
      <aside aria-label="activity">
        <NestedDialog />
      </aside>,
    );

    await user.click(edgeTab());
    await user.click(screen.getByRole("button", { name: "open inner" }));
    expect(screen.getByRole("dialog", { name: "inner" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "inner" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("keeps the panel as a column, open, when the window is wide", () => {
    windowIs(false);
    mount();

    expect(screen.getByLabelText("activity")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
