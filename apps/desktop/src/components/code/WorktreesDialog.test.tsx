import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorktreesDialog } from "@/components/code/WorktreesDialog";
import { listProjectWorktrees, pruneProjectWorktrees } from "@/lib/api";
import type { ProjectWorktree } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  listProjectWorktrees: vi.fn(),
  pruneProjectWorktrees: vi.fn(),
}));

/**
 * The project's worktree cleanup (study 03, PR6).
 *
 * Two things are pinned. A worktree made outside Chimera is listed with its reason and offers no
 * checkbox — the owner did not let the app remove those. And removal is two steps: nothing is sent
 * with `dry_run: false` until a preview answered, and only the paths that preview said would go.
 */

function row(path: string, extra: Partial<ProjectWorktree>): ProjectWorktree {
  return {
    path,
    branch: "",
    main: false,
    exists: true,
    locked: false,
    owned: true,
    in_use: false,
    merged: true,
    clean: true,
    untracked: [],
    untracked_count: 0,
    upstream: "",
    ahead: null,
    removable: false,
    kept: "",
    conversations: [],
    ...extra,
  };
}

const LIST = {
  project: "C:\\loja",
  main: "C:\\loja",
  worktrees: [
    row("C:\\loja", { main: true, branch: "main", kept: "main_checkout", owned: false }),
    row("C:\\tmp\\chimera-wt-done", {
      branch: "chimera/attempt-done",
      removable: true,
      conversations: [{ id: "c1", title: "Carrinho", archived: false }],
    }),
    row("C:\\dev\\w-mine", { branch: "w-mine", owned: false, kept: "not_chimera_worktree" }),
    row("C:\\tmp\\chimera-wt-dirty", { branch: "chimera/attempt-x", kept: "dirty" }),
  ],
};

function render() {
  renderWithProviders(<WorktreesDialog project="C:\loja" name="loja" onClose={vi.fn()} />);
}

describe("WorktreesDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjectWorktrees).mockResolvedValue(LIST);
  });

  it("lists every worktree with its reason and offers a checkbox only for removable ones", async () => {
    render();
    expect(await screen.findByText("w-mine")).toBeInTheDocument();
    expect(screen.getByText(/created outside Chimera|criado fora do Chimera/i)).toBeInTheDocument();
    expect(screen.getByText(/uncommitted or untracked|não commitadas/i)).toBeInTheDocument();
    // The main folder is not a row: it is the project itself.
    expect(screen.queryByText("loja")).not.toBeInTheDocument();
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(1);
    expect(boxes[0]).toHaveAccessibleName(/chimera-wt-done/);
  });

  it("removes only after a preview, and only what the preview said would go", async () => {
    const user = userEvent.setup();
    vi.mocked(pruneProjectWorktrees).mockImplementation(async (_p, paths, dryRun) => ({
      dry_run: dryRun ?? true,
      results: paths.map((path) => ({
        path,
        branch: "chimera/attempt-done",
        kept: "",
        removed: !dryRun,
        would_remove: Boolean(dryRun),
        branch_deleted: !dryRun,
        archived: dryRun ? [] : ["c1"],
      })),
    }));
    render();

    const remove = await screen.findByRole("button", { name: /^(Remove|Remover) 0$/i });
    expect(remove).toBeDisabled();
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^(Preview|Pré-visualizar)$/i }));
    await waitFor(() =>
      expect(pruneProjectWorktrees).toHaveBeenCalledWith(
        "C:\\loja",
        ["C:\\tmp\\chimera-wt-done"],
        true,
      ),
    );
    await user.click(await screen.findByRole("button", { name: /^(Remove|Remover) 1$/i }));
    await waitFor(() =>
      expect(pruneProjectWorktrees).toHaveBeenLastCalledWith(
        "C:\\loja",
        ["C:\\tmp\\chimera-wt-done"],
        false,
      ),
    );
    expect(await screen.findByText(/(Removed|removidas).*1/i)).toBeInTheDocument();
  });
});
