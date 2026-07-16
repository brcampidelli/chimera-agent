import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getRuns,
  streamRun,
  type RunEvent,
  type RunStreamHandlers,
} from "@/lib/api";
import { emptyTree, gitStatus, receipt } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function editFrame(path: string, patch: string): RunEvent {
  return { kind: "edit", text: "", path, patch };
}

/** Start a run whose stream emits the given frames, then finishes. The `edit` frames are the live
 *  play-by-play: one real per-file diff, streamed as the agent writes it. */
async function runWithEvents(events: RunEvent[]) {
  const user = userEvent.setup();
  vi.mocked(streamRun).mockImplementation((_req, handlers: RunStreamHandlers) => {
    for (const e of events) handlers.onEvent?.(e);
    return new Promise<void>(() => {}); // stays in flight, as it is mid-run
  });
  renderWithProviders(<Code />);

  await user.type(screen.getByPlaceholderText(/^Describe the change/), "make the test pass");
  await act(async () => {
    await user.click(screen.getAllByRole("button", { name: "Run" })[0]);
  });
  return user;
}

/** The live-edit rows, in DOM order: each summary is "N." + the edited path. */
function liveEditPaths(): string[] {
  const section = screen.getByText("Live edits").parentElement as HTMLElement;
  return [...section.querySelectorAll("summary")].map((s) => s.textContent ?? "");
}

describe("Code — the live per-edit diff stream", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([receipt()]);
  });

  it("renders the real diff of each file as the agent edits it", async () => {
    await runWithEvents([editFrame("src/a.py", "@@ -1 +1 @@\n-old\n+new")]);

    expect(await screen.findByText("Live edits")).toBeInTheDocument();
    expect(screen.getByText("src/a.py")).toBeInTheDocument();
    // The streamed patch body is rendered, not just the filename.
    expect(screen.getByText("+new")).toBeInTheDocument();
    expect(screen.getByText("-old")).toBeInTheDocument();
  });

  it("keeps the edits in the order they were streamed", async () => {
    await runWithEvents([
      editFrame("src/first.py", "@@ -1 +1 @@\n+1"),
      editFrame("src/second.py", "@@ -1 +1 @@\n+2"),
      editFrame("src/third.py", "@@ -1 +1 @@\n+3"),
    ]);

    await screen.findByText("Live edits");
    expect(liveEditPaths()).toEqual(["1.src/first.py", "2.src/second.py", "3.src/third.py"]);
  });

  it("keeps a re-edited path as its own later step rather than deduping it", async () => {
    // A real run can overwrite an earlier edit — that second write is a distinct event, and
    // collapsing the two would hide a step that actually happened.
    await runWithEvents([
      editFrame("src/a.py", "@@ -1 +1 @@\n+first pass"),
      editFrame("src/b.py", "@@ -1 +1 @@\n+other file"),
      editFrame("src/a.py", "@@ -1 +1 @@\n+second pass"),
    ]);

    await screen.findByText("Live edits");
    expect(liveEditPaths()).toEqual(["1.src/a.py", "2.src/b.py", "3.src/a.py"]);
    // Both versions of the re-edited file's patch are shown — the later does not replace the earlier.
    expect(screen.getByText("+first pass")).toBeInTheDocument();
    expect(screen.getByText("+second pass")).toBeInTheDocument();
  });

  it("invents no live-edits section for a run that streamed no edits", async () => {
    await runWithEvents([{ kind: "status", text: "planning" }]);

    // The run is streaming (its status line landed) — it just made no edits to show.
    expect(await screen.findByText("planning…")).toBeInTheDocument();
    expect(screen.queryByText("Live edits")).not.toBeInTheDocument();
  });

  it("ignores an edit frame carrying no real patch", async () => {
    // A frame without a diff has nothing honest to show — it must not render an empty edit row.
    await runWithEvents([{ kind: "edit", text: "", path: "src/a.py" }]);

    expect(screen.queryByText("Live edits")).not.toBeInTheDocument();
  });

  it("shows no live edits before a run has started", () => {
    vi.mocked(streamRun).mockImplementation(async () => {});
    renderWithProviders(<Code />);

    expect(screen.queryByText("Live edits")).not.toBeInTheDocument();
  });
});
