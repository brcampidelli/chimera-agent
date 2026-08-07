import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The per-file diff, streamed as the agent writes it.
 *
 * This used to be asserted against the run panel that sat folded under the conversation. That panel
 * is gone — it was a second implementation of the Work screen's launcher, with fewer features — and
 * the claims moved to the surface that still makes them: the conversation, which renders one real
 * unified diff per edit inside the exchange that produced it.
 *
 * What DID go with the panel is watching a *run's* edits arrive as diffs; a run is now followed from
 * the status bar and read afterwards from its receipt, where the diffs are. That is a real
 * reduction, recorded here rather than quietly dropped.
 */
async function turnEditing(edits: { path: string; patch: string }[]) {
  const user = userEvent.setup();
  vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ edits }));
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/^Ask about this code/), "make the change{Enter}");
  return user;
}

/** The edited paths, in DOM order — each is the button that opens the file in the viewer. */
function editedPaths(): string[] {
  return screen
    .getAllByRole("button")
    .map((b) => b.textContent ?? "")
    .filter((text) => text.endsWith(".py"));
}

describe("Code — the per-edit diff stream", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("renders the real diff of each file as the agent edits it", async () => {
    await turnEditing([{ path: "src/a.py", patch: "@@ -1 +1 @@\n-old\n+new" }]);

    expect(await screen.findByText("src/a.py")).toBeInTheDocument();
    // The streamed patch body is rendered, not just the filename.
    expect(screen.getByText("+new")).toBeInTheDocument();
    expect(screen.getByText("-old")).toBeInTheDocument();
  });

  it("keeps the edits in the order they were streamed", async () => {
    await turnEditing([
      { path: "src/first.py", patch: "@@ -1 +1 @@\n+1" },
      { path: "src/second.py", patch: "@@ -1 +1 @@\n+2" },
      { path: "src/third.py", patch: "@@ -1 +1 @@\n+3" },
    ]);

    await screen.findByText("src/first.py");
    expect(editedPaths()).toEqual(["src/first.py", "src/second.py", "src/third.py"]);
  });

  it("keeps a re-edited path as its own later step rather than deduping it", async () => {
    // A real turn can overwrite an earlier edit — that second write is a distinct event, and
    // collapsing the two would hide a step that actually happened.
    await turnEditing([
      { path: "src/a.py", patch: "@@ -1 +1 @@\n+first" },
      { path: "src/b.py", patch: "@@ -1 +1 @@\n+other" },
      { path: "src/a.py", patch: "@@ -1 +1 @@\n+second" },
    ]);

    await screen.findByText("+second");
    expect(editedPaths()).toEqual(["src/a.py", "src/b.py", "src/a.py"]);
  });

  it("invents no edit section for a turn that edited nothing", async () => {
    await turnEditing([]);

    await screen.findByText("done"); // the turn finished
    expect(editedPaths()).toEqual([]);
  });
});
