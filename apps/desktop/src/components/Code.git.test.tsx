import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getRuns, gitCommit, streamRun } from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";
import type { GitFile } from "@/lib/types";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function file(over: Partial<GitFile> = {}): GitFile {
  return { path: "src/app.py", staged: false, untracked: false, x: " ", y: "M", ...over };
}

describe("Code — the git panel", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
  });

  it("shows an honest empty state, and no commit UI, when the folder is not a git repo", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, branch: "" }));
    renderWithProviders(<Code />);

    expect(
      await screen.findByText("Not a git repo — run `git init` in this folder to enable the panel."),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/commit message/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Commit/ })).not.toBeInTheDocument();
  });

  it("says the tree is clean rather than showing an empty file list", async () => {
    renderWithProviders(<Code />);

    expect(await screen.findByText("Working tree clean — no changes to commit.")).toBeInTheDocument();
  });

  it("groups the real changed files by staged / modified / untracked", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(
      gitStatus({
        files: [
          file({ path: "src/staged.py", staged: true, x: "M", y: " " }),
          file({ path: "src/dirty.py" }),
          file({ path: "src/new.py", untracked: true, x: "?", y: "?" }),
        ],
      }),
    );
    renderWithProviders(<Code />);

    expect(await screen.findByText("Staged")).toBeInTheDocument();
    expect(screen.getByText("Modified")).toBeInTheDocument();
    expect(screen.getByText("Untracked")).toBeInTheDocument();
    expect(screen.getByText("src/staged.py")).toBeInTheDocument();
    expect(screen.getByText("src/dirty.py")).toBeInTheDocument();
    expect(screen.getByText("src/new.py")).toBeInTheDocument();
    expect(screen.getByText("branch: main")).toBeInTheDocument();
  });

  it("commits only the explicitly selected paths, never everything", async () => {
    const user = userEvent.setup();
    vi.mocked(gitCommit).mockResolvedValue({ ok: true, commit: "abc1234", error: null, output: "" });
    vi.mocked(getGitStatus).mockResolvedValue(
      gitStatus({ files: [file({ path: "src/a.py" }), file({ path: "src/b.py" })] }),
    );
    renderWithProviders(<Code />);

    // Both modified files default-checked; untick one so the commit is genuinely a subset.
    const boxes = await screen.findAllByRole("checkbox");
    await user.click(boxes[1]);
    await user.type(screen.getByPlaceholderText(/commit message/), "fix a");
    await user.click(screen.getByRole("button", { name: /Commit \(1\)/ }));

    expect(gitCommit).toHaveBeenCalledWith(null, "fix a", ["src/a.py"]);
    expect(await screen.findByText(/abc1234/)).toBeInTheDocument();
  });

  it("refuses to commit without a message", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files: [file()] }));
    renderWithProviders(<Code />);

    expect(await screen.findByRole("button", { name: /Commit \(1\)/ })).toBeDisabled();
    expect(gitCommit).not.toHaveBeenCalled();
  });

  it("reports a failed commit instead of silently claiming success", async () => {
    const user = userEvent.setup();
    vi.mocked(gitCommit).mockResolvedValue({
      ok: false,
      commit: "",
      error: "nothing to commit",
      output: "",
    });
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files: [file()] }));
    renderWithProviders(<Code />);

    await user.type(await screen.findByPlaceholderText(/commit message/), "fix it");
    await user.click(screen.getByRole("button", { name: /Commit \(1\)/ }));

    expect(await screen.findByText("Commit failed.")).toBeInTheDocument();
    expect(screen.queryByText(/Committed/)).not.toBeInTheDocument();
  });
});
