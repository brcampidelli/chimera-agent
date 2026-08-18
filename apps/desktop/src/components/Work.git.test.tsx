import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Work } from "@/components/Work";
import { getFsTree, getGitStatus, getRuns, gitCommit, gitInit, streamRun } from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";
import type { GitFile } from "@/lib/types";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function file(over: Partial<GitFile> = {}): GitFile {
  return { path: "src/app.py", staged: false, untracked: false, x: " ", y: "M", ...over };
}

/** Render Work and open the tab this panel now lives on.
 *
 * The assertions below are unchanged from when these ran against the Code screen — only the host
 * moved. Keeping them verbatim is the point: if relocating a panel had changed what it DOES, these
 * would say so, and a rewritten test could not. */
async function openTab(name: RegExp) {
  renderWithProviders(<Work />);
  await userEvent.click(await screen.findByRole("tab", { name }));
}

describe("Work — the git panel", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
  });

  it("shows an honest empty state, and no commit UI, when the folder is not a git repo", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, branch: "" }));
    await openTab(/^Git$/);

    expect(
      await screen.findByText(
        "Not a git repo — nothing to commit against, and no way to undo what a run changes.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/commit message/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Commit/ })).not.toBeInTheDocument();
  });

  it("offers to initialise the repo instead of naming a command to run in a terminal", async () => {
    // The whole thesis of the app is that you do not need a terminal, and this panel used to end at
    // "run `git init` in this folder" — translated into ten languages, ten times an instruction to
    // leave. The button does the init AND a snapshot commit, so there is a point of return before
    // the agent is given write and shell access.
    const user = userEvent.setup();
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, branch: "" }));
    vi.mocked(gitInit).mockResolvedValue({ ok: true, commit: "abc1234", output: "", error: null });
    await openTab(/^Git$/);

    await user.click(await screen.findByRole("button", { name: /Initialise git here/ }));

    expect(gitInit).toHaveBeenCalledWith(null);
  });

  it("reports a failed init instead of a button that appears to do nothing", async () => {
    const user = userEvent.setup();
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, branch: "" }));
    vi.mocked(gitInit).mockResolvedValue({
      ok: false,
      commit: "",
      output: "",
      error: "already a git repo",
    });
    await openTab(/^Git$/);

    await user.click(await screen.findByRole("button", { name: /Initialise git here/ }));

    expect(
      await screen.findByText(
        "Couldn't initialise git here — is git installed, and the folder writable?",
      ),
    ).toBeInTheDocument();
  });

  it("says the tree is clean rather than showing an empty file list", async () => {
    await openTab(/^Git$/);

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
    await openTab(/^Git$/);

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
    await openTab(/^Git$/);

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
    await openTab(/^Git$/);

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
    await openTab(/^Git$/);

    await user.type(await screen.findByPlaceholderText(/commit message/), "fix it");
    await user.click(screen.getByRole("button", { name: /Commit \(1\)/ }));

    expect(await screen.findByText("Commit failed.")).toBeInTheDocument();
    expect(screen.queryByText(/Committed/)).not.toBeInTheDocument();
  });
});
