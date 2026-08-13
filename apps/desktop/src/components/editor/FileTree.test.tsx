import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FileTree } from "@/components/editor/FileTree";
import { getFsTree } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getFsTree: vi.fn() }));

/**
 * The tree is lazy by design: one directory per request, capped server-side. These tests hold it to
 * that — that it does not walk the workspace on mount, that a folder nobody opened is never
 * fetched, and that the two ways a listing can be incomplete (capped, unreadable) are both said out
 * loud rather than shown as an empty folder.
 */

type Node = { name: string; path: string; is_dir: boolean };

function tree(path: string, entries: Node[], capped = false) {
  return { workspace: "/w", path, entries, capped };
}

const ROOT: Node[] = [
  { name: "src", path: "src", is_dir: true },
  { name: "README.md", path: "README.md", is_dir: false },
];

beforeEach(() => {
  vi.mocked(getFsTree).mockImplementation((_w, p = "") =>
    Promise.resolve(
      p === "src"
        ? tree("src", [{ name: "app.py", path: "src/app.py", is_dir: false }])
        : tree("", ROOT),
    ),
  );
});

describe("FileTree", () => {
  it("lists the workspace root and nothing else", async () => {
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    expect(await screen.findByRole("button", { name: /README\.md/ })).toBeInTheDocument();
    // One request, for the root. A tree that walked everything on mount would make opening a large
    // repository cost a full traversal before the first file could be clicked.
    expect(getFsTree).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /app\.py/ })).toBeNull();
  });

  it("fetches a folder only when it is opened", async () => {
    const user = userEvent.setup();
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    await user.click(await screen.findByRole("button", { name: /src/ }));

    expect(await screen.findByRole("button", { name: /app\.py/ })).toBeInTheDocument();
    expect(getFsTree).toHaveBeenCalledWith("/w", "src");
  });

  it("opens a file rather than expanding it", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={onOpen} />);

    await user.click(await screen.findByRole("button", { name: /README\.md/ }));

    expect(onOpen).toHaveBeenCalledWith("README.md");
  });

  it("marks the file you are editing", async () => {
    renderWithProviders(<FileTree workspace="/w" activePath="README.md" onOpen={() => {}} />);
    // Without this, a tree of thirty files gives no answer to "which one am I in".
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /README\.md/ })).toHaveAttribute(
        "aria-current",
        "true",
      ),
    );
  });

  it("says a listing was cut short instead of looking complete", async () => {
    // A capped folder that shows nothing about being capped is a folder that LIES about its size —
    // and the file you are looking for is the one that got cut.
    vi.mocked(getFsTree).mockResolvedValue(tree("", ROOT, true));
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    expect(await screen.findByText(/showing the first entries only/i)).toBeInTheDocument();
  });

  it("reports an unreadable folder without blanking the tree", async () => {
    vi.mocked(getFsTree).mockRejectedValue(new Error("permission denied"));
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  });

  it("distinguishes an empty folder from a broken one", async () => {
    vi.mocked(getFsTree).mockResolvedValue(tree("", []));
    renderWithProviders(<FileTree workspace="/w" activePath={null} onOpen={() => {}} />);

    expect(await screen.findByText(/empty folder/i)).toBeInTheDocument();
  });
});
