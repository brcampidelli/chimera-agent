import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { listCodeUndoPoints, previewCodeUndoPoint, redoCodeUndo, restoreCodeUndoPoint } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function mount() {
  return renderWithProviders(
    <Conversation workspace="/proj" openFile={null} resumeSession="s1"
      posture={{ reach: "workspace" as never, approval: "ask" as never }} profile={"balanced" as never}
      onHandOff={() => {}} onBatch={() => {}} onEdited={() => {}} busyElsewhere={false}
      controls={null} onOpenFile={() => {}} />,
  );
}

const points = {
  session_id: "s1",
  cutoff: null,
  can_redo: true,
  turns: [
    { turn_id: "turn-1", created_at: 1_700_000_000, files: ["src/a.ts", "README.md"] },
    {
      turn_id: "turn-gone",
      created_at: 1_700_000_100,
      files: ["old.ts"],
      available: false,
      reason: "snapshot missing",
    },
  ],
};

describe("durable conversation undo", () => {
  beforeEach(() => {
    vi.mocked(listCodeUndoPoints).mockReset().mockResolvedValue(points);
    vi.mocked(restoreCodeUndoPoint).mockReset().mockResolvedValue({
      ok: true,
      restored: 1,
      kept: ["README.md"],
      left_new_files: [],
    });
    vi.mocked(previewCodeUndoPoint).mockReset().mockResolvedValue({
      ok: true,
      files: ["src/a.ts", "README.md"],
      conflicts: [],
    });
    vi.mocked(redoCodeUndo).mockReset().mockResolvedValue({ ok: true, restored: 1, kept: [] });
    localStorage.clear();
  });

  it("lists durable points and disables unavailable snapshots with their reason", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByTestId("undo-timeline-toggle"));
    expect(await screen.findByTestId("undo-point-turn-1")).toBeEnabled();
    expect(screen.getByTestId("undo-point-turn-gone")).toBeDisabled();
    expect(screen.getByTestId("undo-point-turn-gone")).toHaveAttribute("title", "snapshot missing");
    expect(listCodeUndoPoints).toHaveBeenCalledWith("s1");
  });

  it("previews the files, confirms, and restores to before the turn", async () => {
    vi.mocked(restoreCodeUndoPoint).mockResolvedValue({ ok: true, restored: 2, kept: [], left_new_files: [] });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByTestId("undo-timeline-toggle"));
    await user.click(await screen.findByTestId("undo-point-turn-1"));
    expect(await screen.findByText("The restore may change 2 file(s):")).toBeInTheDocument();
    expect(previewCodeUndoPoint).toHaveBeenCalledWith("s1", "turn-1");
    expect(screen.getByText("src/a.ts")).toBeInTheDocument();
    expect(screen.getByText("README.md")).toBeInTheDocument();
    expect(screen.queryByTestId("undo-conflicts")).not.toBeInTheDocument();
    expect(restoreCodeUndoPoint).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("undo-confirm"));
    await waitFor(() => expect(restoreCodeUndoPoint).toHaveBeenCalledWith("s1", "turn-1"));
    expect(await screen.findByTestId("undo-notice")).toHaveTextContent("This restore can be redone.");
  });

  it("names the files a later edit would protect before anything is written", async () => {
    vi.mocked(previewCodeUndoPoint).mockResolvedValue({
      ok: true,
      files: ["src/a.ts", "README.md"],
      conflicts: ["README.md"],
    });
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByTestId("undo-timeline-toggle"));
    await user.click(await screen.findByTestId("undo-point-turn-1"));
    expect(await screen.findByTestId("undo-conflicts")).toHaveTextContent(
      "Edited since, these files stay as they are: README.md",
    );
    expect(restoreCodeUndoPoint).not.toHaveBeenCalled();
  });

  it("reports what the server left alone after the restore", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByTestId("undo-timeline-toggle"));
    await user.click(await screen.findByTestId("undo-point-turn-1"));
    await user.click(await screen.findByTestId("undo-confirm"));
    expect(await screen.findByTestId("undo-notice")).toHaveTextContent("Left changed or new files untouched: README.md");
  });

  it("can redo the range restore", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByTestId("undo-timeline-toggle"));
    await user.click(await screen.findByTestId("undo-redo"));
    expect(redoCodeUndo).toHaveBeenCalledWith("s1");
    expect(await screen.findByTestId("undo-notice")).toHaveTextContent("Restored 1 file(s).");
  });

  it("shows unavailable explanation rather than allowing a missing point", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByTestId("undo-timeline-toggle"));
    expect(screen.getByTestId("undo-point-turn-gone")).toBeDisabled();
    expect(screen.getByTestId("undo-point-turn-gone")).toHaveAttribute("title", "snapshot missing");
    expect(restoreCodeUndoPoint).not.toHaveBeenCalled();
  });
});
