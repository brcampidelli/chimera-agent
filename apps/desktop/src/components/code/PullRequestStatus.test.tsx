import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PullRequestCard } from "@/components/code/PullRequestCard";
import { pollInterval } from "@/components/code/PullRequestStatus";
import {
  cleanupCodeSessionPullRequest,
  getCodeSessionPullRequest,
  getPullRequestReadiness,
  getPullRequestStatus,
  openPullRequest,
  setCodeSessionPullRequestOptions,
} from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { takeOfferedTurn } from "@/lib/offered-turn";
import type { CodeSessionPullRequest, PullRequestReadiness, PullRequestStatus } from "@/lib/types";

// Only these exist: a turn-sending function is deliberately absent, so a card that tried to start a
// turn on its own would call `undefined` and fail the test that clicked it.
vi.mock("@/lib/api", () => ({
  cleanupCodeSessionPullRequest: vi.fn(),
  getCodeSessionPullRequest: vi.fn(),
  getPullRequestReadiness: vi.fn(),
  getPullRequestStatus: vi.fn(),
  openPullRequest: vi.fn(),
  setCodeSessionPullRequestOptions: vi.fn(),
}));

const URL = "https://github.com/o/r/pull/42";

function record(over: Partial<CodeSessionPullRequest> = {}): CodeSessionPullRequest {
  return { number: 42, url: URL, fix_on_ci_failure: false, cleanup_on_merge: false, ...over };
}

function status(over: Partial<PullRequestStatus> = {}): PullRequestStatus {
  return {
    state: "open",
    mergeable: "mergeable",
    passed: 6,
    failed: 2,
    pending: 9,
    failing_names: ["tests (ubuntu)", "lint"],
    error: "",
    ...over,
  };
}

const NOTHING_TO_PUSH: PullRequestReadiness = {
  ready: false,
  reason: "nothing_ahead",
  is_repo: true,
  branch: "feature",
  base: "main",
  head: "a".repeat(40),
  remote: "",
  remote_head: "",
  ahead: 0,
  commits: [],
  diffstat: "",
  uncommitted: 0,
  gh: true,
  gh_signed_in: true,
};

function mount(sessionId: string | null = "s1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <PullRequestCard workspace="/repo" sessionId={sessionId} />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("the pull request a conversation opened", () => {
  beforeEach(() => {
    for (const fn of [
      cleanupCodeSessionPullRequest,
      getCodeSessionPullRequest,
      getPullRequestReadiness,
      getPullRequestStatus,
      openPullRequest,
      setCodeSessionPullRequestOptions,
    ]) {
      vi.mocked(fn).mockReset();
    }
    vi.mocked(getPullRequestReadiness).mockResolvedValue(NOTHING_TO_PUSH);
    localStorage.clear();
    window.location.hash = "#/work?tab=git";
  });
  afterEach(() => localStorage.clear());

  it("is read from the conversation's record, so it is there when the conversation reopens", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record());
    vi.mocked(getPullRequestStatus).mockResolvedValue(status());
    mount();

    expect(await screen.findByText("6/17 checks passed")).toBeInTheDocument();
    expect(getCodeSessionPullRequest).toHaveBeenCalledWith("s1");
    expect(getPullRequestStatus).toHaveBeenCalledWith("/repo", 42);
    expect(screen.getByRole("link", { name: /Pull request #42/ })).toHaveAttribute("href", URL);
    expect(screen.getByText("tests (ubuntu)")).toBeInTheDocument();
    expect(screen.getByText(/can be merged/)).toBeInTheDocument();
    // The dot's name is a translated sentence, never the server's raw token.
    expect(screen.getByRole("img", { name: "Pull request checks: some checks failed" })).toBeInTheDocument();
  });

  it("names the state in the owner's language, aria-label included", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record());
    vi.mocked(getPullRequestStatus).mockResolvedValue(status({ state: "merged", failed: 0, pending: 0, failing_names: [] }));
    localStorage.setItem("chimera.lang", "pt");
    mount();

    const dot = await screen.findByRole("img", { name: /mesclado/ });
    expect(dot.getAttribute("aria-label")).not.toMatch(/merged/);
  });

  it("shows nothing for a conversation with no PR recorded", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ number: null, url: "" }));
    mount();

    await waitFor(() => expect(getCodeSessionPullRequest).toHaveBeenCalled());
    expect(getPullRequestStatus).not.toHaveBeenCalled();
    expect(screen.queryByText(/Pull request #/)).toBeNull();
  });

  it("offers no fix turn while the option is off, however red the CI is", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ fix_on_ci_failure: false }));
    vi.mocked(getPullRequestStatus).mockResolvedValue(status());
    mount();

    await screen.findByText("6/17 checks passed");
    expect(screen.queryByRole("button", { name: "Prepare a fix turn" })).toBeNull();
  });

  it("with the option on, only OFFERS the turn: it lands in the conversation's draft and nothing is sent", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ fix_on_ci_failure: true }));
    vi.mocked(getPullRequestStatus).mockResolvedValue(status());
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: "Prepare a fix turn" }));

    const offered = takeOfferedTurn("s1");
    expect(offered).toContain("#42");
    expect(offered).toContain("`tests (ubuntu)`, `lint`");
    // The agent is asked to read the log itself, so the log arrives as a governed tool result.
    expect(offered).toContain("gh run view --log-failed");
    expect(window.location.hash).toMatch(/^#\/code/);
    expect(openPullRequest).not.toHaveBeenCalled();
    expect(cleanupCodeSessionPullRequest).not.toHaveBeenCalled();
  });

  it("does not offer a fix when nothing failed", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ fix_on_ci_failure: true }));
    vi.mocked(getPullRequestStatus).mockResolvedValue(status({ failed: 0, failing_names: [] }));
    mount();

    await screen.findByText("6/15 checks passed");
    expect(screen.queryByRole("button", { name: "Prepare a fix turn" })).toBeNull();
  });

  it("switches an option through the owner route and shows what the server stored", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record());
    vi.mocked(getPullRequestStatus).mockResolvedValue(status());
    vi.mocked(setCodeSessionPullRequestOptions).mockResolvedValue(record({ cleanup_on_merge: true }));
    const user = userEvent.setup();
    mount();

    const box = await screen.findByRole("checkbox", { name: /archive this conversation/ });
    expect(box).not.toBeChecked(); // off by default
    await user.click(box);

    expect(setCodeSessionPullRequestOptions).toHaveBeenCalledWith("s1", { cleanup_on_merge: true });
    await waitFor(() => expect(box).toBeChecked());
  });

  it("asks for the cleanup once when it sees the PR merged with the option on, and says why it kept", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ cleanup_on_merge: true }));
    vi.mocked(getPullRequestStatus).mockResolvedValue(status({ state: "merged", failed: 0, pending: 0, failing_names: [] }));
    vi.mocked(cleanupCodeSessionPullRequest).mockResolvedValue({
      archived: false, removed_worktree: false, deleted_branch: false, kept: "unpushed",
    });
    mount();

    expect(await screen.findByText(/nothing was removed: it has commits that were not pushed/)).toBeInTheDocument();
    expect(cleanupCodeSessionPullRequest).toHaveBeenCalledTimes(1);
  });

  it("never asks for the cleanup with the option off, or before the merge", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ cleanup_on_merge: false }));
    vi.mocked(getPullRequestStatus).mockResolvedValue(status({ state: "merged", failed: 0, pending: 0 }));
    const first = mount();
    await screen.findByRole("img", { name: /merged/ });
    first.unmount();

    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ cleanup_on_merge: true }));
    vi.mocked(getPullRequestStatus).mockResolvedValue(status());
    mount();
    await screen.findByText("6/17 checks passed");

    expect(cleanupCodeSessionPullRequest).not.toHaveBeenCalled();
  });

  it("sends the conversation with the PR it opens, then re-reads the record", async () => {
    vi.mocked(getPullRequestReadiness).mockResolvedValue({
      ...NOTHING_TO_PUSH, ready: true, reason: "", ahead: 1, commits: ["abc1234 Add it"],
    });
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue(record({ number: null, url: "" }));
    vi.mocked(openPullRequest).mockResolvedValue({ ok: true, url: URL, output: "", error: null });
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /open pull request/i }));
    await user.click(screen.getByRole("button", { name: /push and open pull request/i }));

    await waitFor(() => expect(openPullRequest).toHaveBeenCalledWith(expect.objectContaining({ session_id: "s1" })));
    await waitFor(() => expect(getCodeSessionPullRequest).toHaveBeenCalledTimes(2));
  });
});

describe("when the card re-reads a pull request", () => {
  it("polls only an open PR with checks still running, and never with the window hidden", () => {
    expect(pollInterval(status({ pending: 3 }), false)).toBe(180_000);
    expect(pollInterval(status({ pending: 3 }), true)).toBe(false);
    expect(pollInterval(status({ pending: 0 }), false)).toBe(false);
    expect(pollInterval(status({ state: "merged", pending: 3 }), false)).toBe(false);
    expect(pollInterval(status({ state: "closed", pending: 3 }), false)).toBe(false);
    expect(pollInterval(status({ error: "Could not read pull request from GitHub" }), false)).toBe(false);
    expect(pollInterval(undefined, false)).toBe(false);
  });
});
