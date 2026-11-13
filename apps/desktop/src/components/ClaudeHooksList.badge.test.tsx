import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ClaudeHooksList } from "@/components/ClaudeHooksList";
import { getClaudeHooks } from "@/lib/api";
import type { ClaudeHooks } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getClaudeHooks: vi.fn(),
  trustClaudeHooks: vi.fn(),
}));

/**
 * A hook that cannot run on this machine is marked beside itself, with how many times it was
 * skipped, and one that runs is not (audit 2026-11, a07-12). Both facts come from the server
 * (`runs_here`, `skipped`); the list only shows them.
 */
function hooks(rows: Partial<ClaudeHooks["hooks"][number]>[]): ClaudeHooks {
  return {
    workspace: "/repo",
    runnable_repo: false,
    runnable_user: true,
    sources: [],
    pending: [],
    fingerprint: "",
    hooks: rows.map((row, index) => ({
      origin: "repo",
      event: "PreToolUse",
      matcher: "Bash",
      command: `check-${index}.sh`,
      timeout: 60,
      on_failure: "continue",
      source: "/repo/.claude/settings.json",
      runs_here: null,
      skipped: 0,
      ...row,
    })),
  };
}

describe("ClaudeHooksList — a hook that will not run here", () => {
  beforeEach(() => vi.clearAllMocks());

  it("marks a hook that cannot run and says how often it was skipped", async () => {
    vi.mocked(getClaudeHooks).mockResolvedValue(hooks([{ runs_here: false, skipped: 4 }]));
    renderWithProviders(<ClaudeHooksList repoOn userOn />);
    expect(await screen.findByText("loaded, does not run on this machine")).toBeInTheDocument();
    expect(screen.getByText(/skipped 4 times since the app started/)).toBeInTheDocument();
  });

  it("shows neither for a hook that runs", async () => {
    vi.mocked(getClaudeHooks).mockResolvedValue(
      hooks([{ origin: "user", runs_here: true, skipped: 0, command: "runs.sh" }]),
    );
    renderWithProviders(<ClaudeHooksList repoOn userOn />);
    expect(await screen.findByText("runs.sh")).toBeInTheDocument();
    expect(screen.queryByText("loaded, does not run on this machine")).not.toBeInTheDocument();
    expect(screen.queryByText(/skipped .* times/)).not.toBeInTheDocument();
  });
});
