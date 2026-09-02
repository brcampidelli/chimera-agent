import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VersionBadge } from "@/components/VersionBadge";
import { getVersion } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getVersion: vi.fn() }));

/**
 * The same React build is served two ways, and the update advice was written for only one of them.
 *
 * The installed bundle carries a complete signed updater — `tauri-plugin-updater` checks GitHub at
 * launch, verifies against the embedded pubkey, asks, installs and restarts. The badge told that
 * user "There's no in-place auto-update yet" and handed them a pip command, which updates the
 * Python package rather than the app on their screen. In a browser that same command is right.
 */
const NEWER = {
  version: "0.48.0rc10",
  latest: "0.49.0",
  update_available: true,
  notes_url: "https://example.test/releases/0.49.0",
};

async function open() {
  const user = userEvent.setup();
  renderWithProviders(<VersionBadge />);
  await user.click(await screen.findByRole("button", { name: /available/i }));
  return user;
}

describe("the version badge", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getVersion).mockResolvedValue(NEWER as never);
  });

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  });

  it("gives a browser session the package command, because there it is the right one", async () => {
    await open();

    expect(screen.getByText(/viewing this in a browser/i)).toBeInTheDocument();
    expect(screen.getByText(/pip install -U/)).toBeInTheDocument();
  });

  it("does not hand the installed app a command for a different copy of the software", async () => {
    // What Tauri injects into the page it serves. Set before render, exactly as the shell does.
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};

    await open();

    // The pip command updates the Python package. The user is looking at the bundle.
    expect(screen.queryByText(/pip install -U/)).toBeNull();
  });

  it("names the thing that starts the update, instead of asking a question it cannot answer",
    async () => {
      (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};

      await open();

      // The heading used to read "A new version is available. Update?" over a panel whose only two
      // buttons are "View release" and "Dismiss". Inside the bundle there is no third option to
      // offer: this panel is in the webview, on the sidecar's http origin, with no IPC to the Rust
      // updater. A question mark promises an answer that does not exist.
      expect(screen.queryByText(/Update\?/)).toBeNull();
      // And it points at what DOES start one.
      expect(screen.getByText(/Check for updates/)).toBeInTheDocument();
      expect(screen.getByText(/tray menu/i)).toBeInTheDocument();
    });

  it("still asks in a browser, where the question has an answer", async () => {
    // The control. Dropping the question everywhere would pass the test above and take away a
    // reasonable prompt from the one place the panel really can tell you what to do next.
    await open();

    expect(screen.getByText(/Update\?/)).toBeInTheDocument();
  });

  it("stays quiet when there is nothing newer", async () => {
    vi.mocked(getVersion).mockResolvedValue({
      version: "0.48.0rc10",
      latest: null,
      update_available: false,
    } as never);

    renderWithProviders(<VersionBadge />);

    await screen.findByText("v0.48.0rc10");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
