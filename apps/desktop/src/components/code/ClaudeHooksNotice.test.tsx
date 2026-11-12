import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClaudeHooksNotice } from "@/components/code/ClaudeHooksNotice";
import { getClaudeHooks } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getClaudeHooks: vi.fn() }));

const SOURCE = { exists: true, enabled: true, hooks: 1, error: "", skipped: [] as string[] };
const hook = (origin: string, event = "PreToolUse") => ({
  origin,
  event,
  matcher: "Bash",
  command: "check.sh",
  timeout: 30,
  on_failure: "continue",
  source: "/p/.claude/settings.json",
});

/**
 * A guard that is written but not in force must not look like one that is: a PreToolUse hook that
 * cannot run here (no sandbox that isolates), or a repository's hooks nobody confirmed, each get a
 * persistent line naming their ORIGIN. When nothing is wrong, or the server does not know, nothing shows.
 */
describe("ClaudeHooksNotice", () => {
  beforeEach(() => vi.clearAllMocks());

  it("warns once per origin whose guard hooks cannot run, and says nothing for the origin that can", async () => {
    vi.mocked(getClaudeHooks).mockResolvedValue({
      workspace: "/p",
      runnable_repo: false,
      runnable_user: true,
      sources: [],
      hooks: [hook("repo"), hook("user")],
    } as never);
    renderWithProviders(<ClaudeHooksNotice workspace="/p" />);
    const lines = await screen.findAllByRole("status");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent(/project hooks that guard tool calls will not run on this machine/i);
  });

  it("says a repository's hooks are not loaded when they are new or changed", async () => {
    vi.mocked(getClaudeHooks).mockResolvedValue({
      workspace: "/p",
      runnable_repo: true,
      runnable_user: true,
      sources: [
        { ...SOURCE, origin: "repo", path: "/p/.claude/settings.json", trust: "changed" },
        { ...SOURCE, origin: "user", path: "/h/.claude/settings.json", trust: "" },
      ],
      hooks: [],
    } as never);
    renderWithProviders(<ClaudeHooksNotice workspace="/p" />);
    expect(await screen.findByRole("status")).toHaveTextContent(/changed since you confirmed it/i);
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("shows nothing when the answer is unknown (Docker is never probed) or all is well", async () => {
    vi.mocked(getClaudeHooks).mockResolvedValue({
      workspace: "/p",
      runnable_repo: null,
      runnable_user: null,
      sources: [{ ...SOURCE, origin: "repo", path: "/p/.claude/settings.json", trust: "trusted" }],
      hooks: [hook("repo")],
    } as never);
    renderWithProviders(<ClaudeHooksNotice workspace="/p" />);
    await vi.waitFor(() => expect(getClaudeHooks).toHaveBeenCalled());
    expect(screen.queryByRole("status")).toBeNull();
  });
});
