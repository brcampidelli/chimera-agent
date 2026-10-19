import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FileTree } from "@/components/editor/FileTree";
import { getFsTree, getGitStatus } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getFsTree: vi.fn(), getGitStatus: vi.fn() }));

/**
 * The tree says what git says: a letter on a changed file, a dot on the folders above it, and a
 * glyph by file type. Read from the same status the Changes card reads, so the two cannot disagree.
 */

const ROOT = [
  { name: "src", path: "src", is_dir: true },
  { name: "docs", path: "docs", is_dir: true },
  { name: "notes.txt", path: "notes.txt", is_dir: false },
  { name: "package-lock.json", path: "package-lock.json", is_dir: false },
  { name: "README.md", path: "README.md", is_dir: false },
];

function status(files: Array<{ path: string; x: string; y: string; untracked?: boolean }>) {
  return {
    is_repo: true,
    branch: "main",
    worktree: false,
    files: files.map((f) => ({ staged: f.x !== " " && !f.untracked, untracked: false, ...f })),
  };
}

beforeEach(() => {
  vi.mocked(getFsTree).mockImplementation((_w, p = "") =>
    Promise.resolve(
      p === "src"
        ? { workspace: "/w", path: "src", capped: false, entries: [{ name: "app.py", path: "src/app.py", is_dir: false }] }
        : { workspace: "/w", path: "", capped: false, entries: ROOT },
    ),
  );
  vi.mocked(getGitStatus).mockResolvedValue(
    status([
      { path: "src/app.py", x: " ", y: "M" },
      { path: "notes.txt", x: "?", y: "?", untracked: true },
      { path: "docs/gone.md", x: " ", y: "D" },
    ]),
  );
});

describe("FileTree — git marks", () => {
  it("puts a letter on a changed file and a dot on the folders that contain one", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    const notes = await screen.findByRole("button", { name: /notes\.txt/ });
    expect(await within(notes).findByRole("img", { name: "Untracked" })).toHaveTextContent("U");

    // The folder carries the dot, not the letter, and the strongest thing inside it.
    const src = screen.getByRole("button", { name: /^src/ });
    expect(within(src).getByRole("img", { name: "Modified" })).toHaveAttribute("data-git-mark", "M");
    expect(within(src).getByRole("img", { name: "Modified" })).toBeEmptyDOMElement();
    // A deleted file is not in the tree, but its folder still says something changed.
    const docs = screen.getByRole("button", { name: /^docs/ });
    expect(within(docs).getByRole("img", { name: "Deleted" })).toHaveAttribute("data-git-mark", "D");

    await user.click(src);
    const app = await screen.findByRole("button", { name: /app\.py/ });
    expect(within(app).getByRole("img", { name: "Modified" })).toHaveTextContent("M");

    // A file git has nothing to say about has no mark.
    expect(within(screen.getByRole("button", { name: /README\.md/ })).queryByRole("img", { name: /Modified|Added|Untracked|Deleted/ })).toBeNull();
  });

  it("shows no marks outside a repository, and the tree still works", async () => {
    vi.mocked(getGitStatus).mockResolvedValue({ is_repo: false, branch: "", worktree: false, files: [] });
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    expect(await screen.findByRole("button", { name: /README\.md/ })).toBeInTheDocument();
    expect(screen.queryAllByRole("img", { name: /Modified|Added|Untracked|Deleted/ })).toHaveLength(0);
  });

  it("gives a folder and a lockfile their own glyph, and opens the folder icon when expanded", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);
    await screen.findByRole("button", { name: /README\.md/ });

    // A file row holds one glyph (no chevron), so the first svg is its type icon. The lockfile is a
    // lock and the readme a document: different shapes, not one `File` for everything.
    const lockClass = screen.getByRole("button", { name: /package-lock/ }).querySelector("svg")?.getAttribute("class") ?? "";
    const readmeClass = screen.getByRole("button", { name: /README/ }).querySelector("svg")?.getAttribute("class") ?? "";
    expect(lockClass).toContain("lucide-lock");
    expect(readmeClass).toContain("lucide-file-text");

    expect(container.querySelector("svg.lucide-folder")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: /^src/ }));
    expect(container.querySelector("svg.lucide-folder-open")).not.toBeNull();
  });
});
