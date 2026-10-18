import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EditSidebar } from "@/components/editor/EditSidebar";
import { renderWithProviders } from "@/test/utils";

// The search itself is `SearchPanel.test.tsx`'s; here only what the sidebar does with a hit.
vi.mock("@/components/editor/SearchPanel", () => ({
  SearchPanel: ({ onOpen }: { onOpen: (path: string, line: number) => void }) => (
    <button type="button" onClick={() => onOpen("docs/notes.md", 42)}>
      hit on line 42
    </button>
  ),
}));
vi.mock("@/components/editor/FileTree", () => ({ FileTree: () => null }));

describe("EditSidebar — a search hit", () => {
  it("opens the file at the hit's line, not at its top", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onOpenAt = vi.fn();
    renderWithProviders(
      <EditSidebar workspace="/repo" onWorkspace={() => {}} activePath={null} onOpen={onOpen} onOpenAt={onOpenAt} />,
    );

    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(screen.getByRole("button", { name: "hit on line 42" }));
    expect(onOpenAt).toHaveBeenCalledWith("docs/notes.md", 42);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("still opens the file when no one can take a line", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    renderWithProviders(<EditSidebar workspace="/repo" onWorkspace={() => {}} activePath={null} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(screen.getByRole("button", { name: "hit on line 42" }));
    expect(onOpen).toHaveBeenCalledWith("docs/notes.md");
  });
});
