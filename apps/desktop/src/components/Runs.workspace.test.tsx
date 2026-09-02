import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskConsole } from "@/components/work/TaskConsole";
import { streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

// The shared mock: this screen mounts the launcher, which mounts the model pickers, which fetch
// on sight. Hand-listing the routes meant discovering each one through a crash.
vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The run has to work in the project the screen is showing.
 *
 * The launcher on the Work tab was once mounted with no workspace at all, so it showed an empty
 * folder box promising to fall back to "the app's workspace" — which on an installed build is the
 * app's own install directory, holding its `.env`.
 *
 * That box is gone: the console shows the project instead of asking for it, because a second folder
 * field is a second answer to one question. Which moves what has to be asserted. Checking that a
 * field is hidden was only ever a proxy for the thing that matters, and it stopped being one the
 * moment the field stopped existing — so these check what actually travels to the server.
 */
describe("the run knows which project it is in", () => {
  beforeEach(() => {
    vi.mocked(streamRun).mockReset().mockImplementation(async () => {});
  });

  async function runIn(workspace: string) {
    const user = userEvent.setup();
    renderWithProviders(<TaskConsole workspace={workspace} onOpenCode={() => {}} />);
    await user.type(await screen.findByLabelText(/the task/i), "fix the loader");
    await user.click(screen.getByRole("button", { name: /^run$/i }));
    return vi.mocked(streamRun).mock.calls[0][0];
  }

  it("sends the project the screen is showing", async () => {
    expect(await runIn("/projects/cafe-aurora")).toMatchObject({
      workspace: "/projects/cafe-aurora",
    });
  });

  it("sends nothing rather than an empty string when no project was chosen", async () => {
    // `readWorkspace()` returns "" when the key is absent. Passed straight through, "" is a
    // workspace the server would try to resolve; `null` is the absence of one, and the two are
    // different requests. The worst of both was hiding the field AND sending the empty string.
    expect(await runIn("")).toMatchObject({ workspace: null });
  });

  it("shows the project instead of asking for it", async () => {
    renderWithProviders(
      <TaskConsole workspace="/projects/cafe-aurora" onOpenCode={() => {}} />,
    );

    expect(await screen.findByText("/projects/cafe-aurora")).toBeInTheDocument();
    // The control for the two above: a screen that neither showed the folder nor asked for it
    // would still pass them, and would be the original defect with the evidence removed.
    expect(screen.queryByLabelText(/workspace|folder path/i)).toBeNull();
  });
});
