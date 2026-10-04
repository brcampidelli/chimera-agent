import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PullRequestCard } from "@/components/code/PullRequestCard";
import { getPullRequestReadiness, openPullRequest } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import type { PullRequestReadiness } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getPullRequestReadiness: vi.fn(),
  openPullRequest: vi.fn(),
}));

const HEAD = "a".repeat(40);

function state(over: Partial<PullRequestReadiness> = {}): PullRequestReadiness {
  return {
    ready: true,
    reason: "",
    is_repo: true,
    branch: "feature",
    base: "main",
    head: HEAD,
    remote: "https://***@github.com/o/r.git",
    remote_head: "",
    ahead: 2,
    commits: ["abc1234 Add the parser", "def5678 Fix the lexer"],
    diffstat: " parser.py | 12 ++++++\n 1 file changed",
    uncommitted: 0,
    gh: true,
    gh_signed_in: true,
    ...over,
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nProvider>
        <PullRequestCard workspace="/repo" />
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("the Git panel's pull request card", () => {
  beforeEach(() => {
    vi.mocked(getPullRequestReadiness).mockReset();
    vi.mocked(openPullRequest).mockReset();
  });

  it("shows what the press publishes before it can be pressed, and sends the commit it showed", async () => {
    vi.mocked(getPullRequestReadiness).mockResolvedValue(state({ uncommitted: 3 }));
    vi.mocked(openPullRequest).mockResolvedValue({
      ok: true,
      url: "https://github.com/o/r/pull/9",
      output: "",
      error: null,
    });
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /open pull request/i }));

    expect(screen.getByText(/feature → main · https:\/\/\*\*\*@github\.com/)).toBeInTheDocument();
    expect(screen.getByText("def5678 Fix the lexer")).toBeInTheDocument();
    expect(screen.getByText(/parser\.py \| 12/)).toBeInTheDocument();
    expect(screen.getByText(/3 changed file\(s\).*NOT be included/)).toBeInTheDocument();
    // The newest commit's subject, without its hash, is the suggested title.
    const title = screen.getByLabelText("Title");
    expect(title).toHaveValue("Add the parser");
    await user.type(screen.getByLabelText("Description"), "Why it changed.");
    await user.click(screen.getByRole("button", { name: /push and open pull request/i }));

    await waitFor(() =>
      expect(openPullRequest).toHaveBeenCalledWith({
        workspace: "/repo",
        title: "Add the parser",
        body: "Why it changed.",
        head: HEAD,
        remote: "https://***@github.com/o/r.git",
        remote_head: "",
        draft: false,
      }),
    );
    expect(await screen.findByRole("link", { name: /pull\/9/ })).toHaveAttribute(
      "href",
      "https://github.com/o/r/pull/9",
    );
  });

  it("says when the push updates a branch that already exists on origin, and sends the commit it showed there", async () => {
    const OLD = "b".repeat(40);
    vi.mocked(getPullRequestReadiness).mockResolvedValue(state({ remote_head: OLD }));
    vi.mocked(openPullRequest).mockResolvedValue({ ok: true, url: "", output: "", error: null });
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /open pull request/i }));
    expect(
      screen.getByText(/UPDATES the branch feature that already exists on origin \(bbbbbbbbbbbb → aaaaaaaaaaaa\)/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /push and open pull request/i }));

    await waitFor(() =>
      expect(openPullRequest).toHaveBeenCalledWith(expect.objectContaining({ remote_head: OLD })),
    );
  });

  it("names a push URL that is another repository, instead of the generic line", async () => {
    vi.mocked(getPullRequestReadiness).mockResolvedValue(
      state({ ready: false, reason: "push_elsewhere" }),
    );
    mount();

    expect(await screen.findByText(/pushes to a different repository/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open pull request/i })).toBeNull();
  });

  it("says why a branch cannot be proposed, and offers no button", async () => {
    vi.mocked(getPullRequestReadiness).mockResolvedValue(
      state({ ready: false, reason: "default_branch" }),
    );
    mount();

    expect(await screen.findByText(/this is the default branch/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open pull request/i })).toBeNull();
  });

  it("renders nothing without origin or the GitHub CLI", async () => {
    vi.mocked(getPullRequestReadiness).mockResolvedValue(state({ ready: false, reason: "no_gh" }));
    const { container } = mount();

    await waitFor(() => expect(getPullRequestReadiness).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("reports a refusal from the server in words, and opens nothing", async () => {
    vi.mocked(getPullRequestReadiness).mockResolvedValue(state());
    vi.mocked(openPullRequest).mockResolvedValue({
      ok: false,
      url: "",
      output: "",
      error: "the branch moved after it was reviewed",
    });
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /open pull request/i }));
    await user.click(screen.getByRole("button", { name: /push and open pull request/i }));

    expect(await screen.findByText(/the branch moved after it was reviewed/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
