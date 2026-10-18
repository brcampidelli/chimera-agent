import type { ReactElement } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChangesCard } from "@/components/code/side/ChangesCard";
import { ConnectorsCard } from "@/components/code/side/ConnectorsCard";
import { ContextCard } from "@/components/code/side/ContextCard";
import { EnvironmentCard } from "@/components/code/side/EnvironmentCard";
import { JobsPanel } from "@/components/JobsPanel";
import {
  getCodeSessionPullRequest,
  getGitStatus,
  connectMcpOAuth,
  getMcpServers,
  getPullRequestStatus,
  listCodeTurnQueue,
  listJobs,
  testMcpServer,
} from "@/lib/api";
import { ConversationSideProvider, type ConversationSide } from "@/lib/conversation-side";
import type { BackgroundJob, CodeTurnQueueItem, McpServer } from "@/lib/types";
import { gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getGitStatus: vi.fn(),
  getMcpServers: vi.fn(),
  testMcpServer: vi.fn(),
  connectMcpOAuth: vi.fn(),
  getCodeSessionPullRequest: vi.fn(),
  getPullRequestStatus: vi.fn(),
  listJobs: vi.fn(),
  listCodeTurnQueue: vi.fn(),
}));

/**
 * The open conversation's side cards (C5). Each card is drawn with the facts the Code screen and the
 * conversation publish, and with the API the shell already had; nothing here mounts the Code screen.
 */
const WS = "C:\\work\\shop";

function mount(card: ReactElement, side: Partial<ConversationSide> | null = {}) {
  return renderWithProviders(
    side === null ? card : (
      <ConversationSideProvider value={{ workspace: WS, sessionId: "s1", ...side }}>{card}</ConversationSideProvider>
    ),
  );
}

function server(over: Partial<McpServer> & { name: string }): McpServer {
  return { command: "npx", args: [], env_keys: [], disabled: false, last_test: null, manifest_held: null, ...over };
}

function job(over: Partial<BackgroundJob> & { id: string }): BackgroundJob {
  return {
    command: "npm test",
    cwd: WS,
    exit_code: null,
    extra: {},
    finished_at: null,
    log: "",
    max_runtime: 0,
    owner: "",
    pid: 1,
    reported: false,
    started_at: 1,
    state: "running",
    tail: "",
    ...over,
  };
}

function item(state: CodeTurnQueueItem["state"], id: string): CodeTurnQueueItem {
  return { id, client_id: id, created_at: 1, position: null, session_id: "s1", source: "terminal", state };
}

beforeEach(() => {
  localStorage.setItem("chimera.lang", "en");
  vi.mocked(getGitStatus).mockReset();
  vi.mocked(getMcpServers).mockReset();
  vi.mocked(testMcpServer).mockReset();
  vi.mocked(connectMcpOAuth).mockReset();
  vi.mocked(getCodeSessionPullRequest).mockReset();
  vi.mocked(getPullRequestStatus).mockReset();
  vi.mocked(listJobs).mockReset();
  vi.mocked(listCodeTurnQueue).mockReset();
});

describe("the Environment card", () => {
  it("labels a session worktree with its project even when git status only reports a generic worktree", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ branch: "feature/checkout", worktree: true }));
    mount(<EnvironmentCard />, {
      checkout: { kind: "worktree", project: "C:\\\\work\\\\project", projectName: "project" },
    });

    expect(await screen.findByText("feature/checkout")).toBeInTheDocument();
    expect(screen.getByText("worktree of project")).toHaveAttribute("title", "C:\\\\work\\\\project");
  });

  it("names the folder with its whole path in the title, the branch, the worktree and the posture sent", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ branch: "feat/cards", worktree: true }));
    mount(<EnvironmentCard />, { posture: { reach: "workspace_shell", approval: "suspicious" } });

    const folder = screen.getByText("shop");
    expect(folder).toHaveAttribute("title", WS);
    const branch = await screen.findByText("feat/cards");
    expect(branch).toBeInTheDocument();
    expect(branch).toHaveClass("break-words");
    expect(screen.getByText("worktree")).toBeInTheDocument();
    const postureText = "workspace + shell · approval if suspicious";
    expect(screen.getByText(postureText)).toBeInTheDocument();
    expect(screen.getByText(postureText)).toHaveAttribute("title", postureText);
    expect(getGitStatus).toHaveBeenCalledWith(WS);
  });

  it("says nothing about a worktree in the main checkout, and nothing about git outside a repo", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ branch: "main", worktree: false }));
    const { unmount } = mount(<EnvironmentCard />);
    expect(await screen.findByText("main")).toBeInTheDocument();
    expect(screen.queryByText("worktree")).not.toBeInTheDocument();
    unmount();

    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, branch: "" }));
    mount(<EnvironmentCard />);
    await waitFor(() => expect(getGitStatus).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Branch")).not.toBeInTheDocument();
    expect(screen.getByText("shop")).toBeInTheDocument();
  });

  it("draws nothing, and asks git nothing, while no Code screen has published a conversation", () => {
    const { container } = mount(<EnvironmentCard />, null);
    expect(container).toBeEmptyDOMElement();
    expect(getGitStatus).not.toHaveBeenCalled();
  });
});

describe("the Context card", () => {
  it("shows the running turn's frame as a percent and a bar, before the last receipt's peak", () => {
    mount(<ContextCard />, { live: { used: 30_500, window: 100_000 }, peak: { used: 90_000, window: 100_000 } });
    const bar = screen.getByRole("progressbar", { name: "Context 30%" });
    expect(bar).toHaveAttribute("aria-valuenow", "30");
    expect(screen.getByText("this turn")).toBeInTheDocument();
    expect(screen.getByText("30,500 of 100,000 tokens")).toBeInTheDocument();
  });

  it("falls back to the last receipt's peak, labelled as the past turn's", () => {
    mount(<ContextCard />, { live: null, peak: { used: 64_000, window: 128_000 } });
    expect(screen.getByRole("progressbar", { name: "Context 50%" })).toBeInTheDocument();
    expect(screen.getByText("peak of the last turn")).toBeInTheDocument();
  });

  it("shows tokens and no percent when no window is known", () => {
    mount(<ContextCard />, { live: null, peak: { used: 50_000, window: null } });
    expect(screen.getByText("Context 50,000 tokens")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("draws nothing in a dock when no turn has reported anything, and says so only where it cannot vanish", () => {
    const { container, unmount } = mount(<ContextCard />, { live: null, peak: null });
    expect(container).toBeEmptyDOMElement();
    unmount();
    mount(<ContextCard alone />, { live: null, peak: null });
    expect(screen.getByText("No turn has reported its context yet")).toBeInTheDocument();
  });
});

describe("the Connectors card", () => {
  const list = [
    server({ name: "github", last_test: { ok: true, tool_count: 12, tested_at: 1 } }),
    server({ name: "broken", last_test: { ok: false, tool_count: 0, tested_at: 1 } }),
    // Paused by the owner: a failed test from before is not a reason to start it again.
    server({ name: "paused", disabled: true, last_test: { ok: false, tool_count: 0, tested_at: 1 } }),
    server({ name: "held", manifest_held: { changes: [], digest: "d", seen_at: 1 } }),
    server({ name: "fresh" }),
  ];

  it("states each server's state and counts only the failed, switched-on ones for a new test", async () => {
    vi.mocked(getMcpServers).mockResolvedValue({ count: list.length, servers: list });
    mount(<ConnectorsCard />);

    expect(await screen.findByText("1 failed their last test")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("data-state"))).toEqual(["passed", "failed", "paused", "held", "untested"]);
    expect(within(rows[0]).getByText("last test passed · 12 tools")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Disabled")).toBeInTheDocument();
  });

  it("tests again only the failed server, then reads the list again", async () => {
    const user = userEvent.setup();
    vi.mocked(getMcpServers).mockResolvedValue({ count: list.length, servers: list });
    vi.mocked(testMcpServer).mockResolvedValue({ ok: true, tools: [], error: null, reaches_agent: null, reaches_agent_reason: null });
    mount(<ConnectorsCard />);

    await user.click(await screen.findByRole("button", { name: "Test again" }));
    await waitFor(() => expect(getMcpServers).toHaveBeenCalledTimes(2));
    expect(testMcpServer).toHaveBeenCalledTimes(1);
    expect(testMcpServer).toHaveBeenCalledWith("broken");
  });

  it("shows a configured server that was never tested with Test and a Settings link", async () => {
    vi.mocked(getMcpServers).mockResolvedValue({ count: 1, servers: [server({ name: "fresh" })] });
    mount(<ConnectorsCard />);
    expect(await screen.findByText("fresh")).toBeInTheDocument();
    expect(screen.getByText("not tested")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open settings for fresh" })).toHaveAttribute("href", "#/settings?tab=connectors");
  });

  it("shows OAuth connectivity separately and offers reauthentication when expired", async () => {
    vi.mocked(getMcpServers).mockResolvedValue({ count: 2, servers: [
      server({ name: "signed-in", oauth: { connected: true } }),
      server({ name: "expired", oauth: { connected: false } }),
    ] });
    mount(<ConnectorsCard />);

    const signedIn = await screen.findByText("signed-in");
    expect(within(signedIn.closest("li")!).getByText("connected")).toBeInTheDocument();
    const expired = screen.getByText("expired");
    expect(within(expired.closest("li")!).getByText("sign-in needed")).toBeInTheDocument();
    expect(within(expired.closest("li")!).getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows an in-flight OAuth sign-in and the screen-safe failure after refusal", async () => {
    localStorage.setItem("chimera.lang", "en");
    const user = userEvent.setup();
    vi.mocked(getMcpServers).mockResolvedValue({ count: 1, servers: [server({ name: "oauth", oauth: { connected: false } })] });
    let finish: ((value: { ok: boolean; error: string | null; oauth: { connected: boolean } }) => void) | undefined;
    vi.mocked(connectMcpOAuth).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    mount(<ConnectorsCard />);

    const signIn = await screen.findByRole("button", { name: "Sign in" });
    await user.click(signIn);
    expect(signIn).toBeDisabled();
    expect(await screen.findByText("Finish signing in in the browser window that opened…")).toBeInTheDocument();
    finish?.({ ok: false, oauth: { connected: false }, error: "discovery" });
    expect(await screen.findByText("The sign-in did not complete (discovery).")).toBeInTheDocument();
  });

  it("reconnects an expired OAuth server using the shared screen controls", async () => {
    const user = userEvent.setup();
    vi.mocked(getMcpServers).mockResolvedValue({ count: 1, servers: [server({ name: "oauth", oauth: { connected: false } })] });
    vi.mocked(connectMcpOAuth).mockResolvedValue({ ok: true, oauth: { connected: true }, error: null });
    mount(<ConnectorsCard />);
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(connectMcpOAuth).toHaveBeenCalledWith("oauth"));
  });

  it("draws nothing in a dock with no server configured, and says so only where it cannot vanish", async () => {
    vi.mocked(getMcpServers).mockResolvedValue({ count: 0, servers: [] });
    const { container, unmount } = mount(<ConnectorsCard />);
    await waitFor(() => expect(getMcpServers).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    mount(<ConnectorsCard alone />);
    expect(await screen.findByText("No MCP servers configured")).toBeInTheDocument();
  });

  it("offers no new test when nothing failed", async () => {
    vi.mocked(getMcpServers).mockResolvedValue({ count: 1, servers: [list[0]] });
    mount(<ConnectorsCard />);
    expect(await screen.findByText("github")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test again" })).not.toBeInTheDocument();
  });
});

describe("the Changes card", () => {
  it("shows the conversation's pull request with the C1 status line, and not the folder's files", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue({
      number: 12, url: "https://github.com/o/r/pull/12", fix_on_ci_failure: false, cleanup_on_merge: false,
    });
    vi.mocked(getPullRequestStatus).mockResolvedValue({
      state: "open", mergeable: "mergeable", passed: 6, failed: 0, pending: 11, failing_names: [], error: "",
    });
    mount(<ChangesCard />);

    expect(await screen.findByRole("link", { name: /Pull request #12/ })).toBeInTheDocument();
    expect(await screen.findByText("6/17 checks passed")).toBeInTheDocument();
    expect(getPullRequestStatus).toHaveBeenCalledWith(WS, 12);
    expect(getGitStatus).not.toHaveBeenCalled();
  });

  it("without a pull request, counts the folder's changed files and names the first five", async () => {
    vi.mocked(getCodeSessionPullRequest).mockResolvedValue({
      number: null, url: "", fix_on_ci_failure: false, cleanup_on_merge: false,
    });
    const files = Array.from({ length: 7 }, (_, i) => ({ path: `src/f${i}.ts`, x: " ", y: "M", staged: false, untracked: false }));
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files }));
    mount(<ChangesCard />);

    expect(await screen.findByText("7 changed files in this folder")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("and 2 more")).toBeInTheDocument();
  });

  it("draws nothing in a dock for a clean folder, says so only alone, and asks no PR of a conversation without an id", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files: [] }));
    const { container, unmount } = mount(<ChangesCard />, { sessionId: null });
    await waitFor(() => expect(getGitStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    mount(<ChangesCard alone />, { sessionId: null });
    expect(await screen.findByText("No uncommitted changes in this folder")).toBeInTheDocument();
    expect(getCodeSessionPullRequest).not.toHaveBeenCalled();
  });

  it("treats a folder that is not a repository the same way: nothing docked, the reason alone", async () => {
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ is_repo: false, files: [] }));
    const { container, unmount } = mount(<ChangesCard />, { sessionId: null });
    await waitFor(() => expect(getGitStatus).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    mount(<ChangesCard alone />, { sessionId: null });
    expect(await screen.findByText("Not a git repository")).toBeInTheDocument();
  });
});

/**
 * The conversation's task card (0.69) read the same jobs as the background panel beside it, so the right
 * panel showed them twice. It was folded into the background panel, which keeps what was its own (every
 * job, with Stop and its output) and gains the queue and the works the task card added.
 */
describe("the background panel, with a conversation on screen", () => {
  it("counts the running jobs, the queue and the works, and lists every job with its Stop", async () => {
    vi.mocked(listJobs).mockResolvedValue({
      jobs: [
        job({ id: "a", command: "npm run bench", cwd: `${WS}\\packages\\api` }),
        // Another folder: still listed, because this panel is the one place any job can be stopped.
        job({ id: "b", command: "cargo build", cwd: "C:\\work\\other" }),
        job({ id: "c", command: "npm test", state: "finished", exit_code: 0 }),
      ],
    });
    vi.mocked(listCodeTurnQueue).mockResolvedValue({ items: [item("running", "q1"), item("pending", "q2"), item("pending", "q3"), item("done", "q4")] });
    mount(<JobsPanel bare />, { worksActive: 1 });

    // Two running jobs, the turn being worked and one work: four running, two turns waiting.
    expect(await screen.findByText("4 running · 2 waiting")).toBeInTheDocument();
    expect(screen.getByText("npm run bench")).toBeInTheDocument();
    expect(screen.getByText("cargo build")).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop job a" })).toBeInTheDocument();
    expect(screen.getByText("Queued turns: 1 running, 2 waiting")).toBeInTheDocument();
    expect(screen.getByText("1 background works not finished")).toBeInTheDocument();
    expect(listCodeTurnQueue).toHaveBeenCalledWith("s1");
  });

  it("draws nothing with nothing running, and reads no queue for a conversation without an id", async () => {
    vi.mocked(listJobs).mockResolvedValue({ jobs: [] });
    const { container } = mount(<JobsPanel bare />, { sessionId: null });
    await waitFor(() => expect(listJobs).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(listCodeTurnQueue).not.toHaveBeenCalled();
  });

  it("says nothing runs where it cannot vanish: maximised, or in a window of its own", async () => {
    vi.mocked(listJobs).mockResolvedValue({ jobs: [] });
    mount(<JobsPanel bare alone />, { sessionId: null });
    expect(await screen.findByText("Nothing running in the background")).toBeInTheDocument();
  });

  it("shows a waiting turn even with no job, since the queue is something running in the background", async () => {
    vi.mocked(listJobs).mockResolvedValue({ jobs: [] });
    vi.mocked(listCodeTurnQueue).mockResolvedValue({ items: [item("pending", "q1")] });
    mount(<JobsPanel bare />);
    expect(await screen.findByText("Queued turns: 0 running, 1 waiting")).toBeInTheDocument();
    expect(screen.getByText("0 running · 1 waiting")).toBeInTheDocument();
  });
});
