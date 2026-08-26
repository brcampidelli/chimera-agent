import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Runs } from "@/components/Runs";
import { getRuns } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

// The shared mock: this screen mounts the launcher, which mounts the model pickers, which fetch
// on sight. Hand-listing the routes meant discovering each one through a crash.
vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The launcher on the Work tab was mounted with no workspace at all.
 *
 * `RunLauncher` hides its folder field when it is given one — "Code already knows where it is
 * working", says its own prop. Work knows too: it reads the same stored choice to filter the run
 * list right beside it, and to label the Git panel one tab over. It just never handed it down, so
 * the screen showed an empty folder box promising to fall back to "the app's workspace" — which on
 * an installed build is the app's own install directory, holding its `.env`.
 */
describe("the run launcher knows which project it is in", () => {
  beforeEach(() => {
    vi.mocked(getRuns).mockReset().mockResolvedValue([]);
  });

  it("hides the folder field when the tab already knows the project", async () => {
    renderWithProviders(<Runs embedded workspace="/projects/cafe-aurora" />);

    await screen.findByRole("button", { name: /run/i });
    expect(screen.queryByLabelText(/workspace|folder path/i)).toBeNull();
  });

  it("still asks when nothing has been chosen", async () => {
    // The control. Hiding the field unconditionally would pass the test above and leave a user with
    // no project silently launching runs into whatever the backend decides — which is the defect,
    // not the fix.
    renderWithProviders(<Runs embedded />);

    await screen.findByRole("button", { name: /run/i });
    expect(screen.getByLabelText(/workspace|folder path/i)).toBeTruthy();
  });

  it("an empty stored choice counts as no choice", async () => {
    // `readWorkspace()` returns "" when the key is absent, and "" passed straight through would
    // hide the field while sending nothing — the worst of both.
    renderWithProviders(<Runs embedded workspace="" />);

    await screen.findByRole("button", { name: /run/i });
    expect(screen.getByLabelText(/workspace|folder path/i)).toBeTruthy();
  });
});
