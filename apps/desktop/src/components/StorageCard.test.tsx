import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DiagnosticsCard } from "@/components/DiagnosticsCard";
import { StorageCard } from "@/components/StorageCard";
import type { AppDiagnostics, StorageReport } from "@/lib/types";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The Storage and Diagnostics cards (study 29, P5.3).
 *
 * Three promises the server already keeps, held on the screen too: a size the server could not
 * count reads "not measured" and never 0; removing anything takes a second press that says what is
 * left alone; and the copied diagnostics are the server's scrubbed text, nothing assembled here.
 */
vi.mock("@/lib/api", () => ({
  getStorage: vi.fn(),
  pruneWorktrees: vi.fn(),
  rotateLogs: vi.fn(),
  clearCache: vi.fn(),
  archiveCodeSession: vi.fn(),
  getAppDiagnostics: vi.fn(),
  getTroubleshoot: vi.fn(),
  openAppFolder: vi.fn(),
  resetAppData: vi.fn(),
}));

const api = await import("@/lib/api");

function category(key: string, bytes: number | null, note = "") {
  return { key, bytes, files: bytes === null ? null : 1, paths: [], note };
}

/** A worktree row as the server now sends it: with its age and the reason it still exists. */
function worktree(path: string, bytes: number | null, state: string, reason: string, extra = {}) {
  return {
    path,
    bytes,
    state,
    reason,
    age_days: 3,
    retention: "no_conversation",
    retention_due_at: null,
    retention_kept: "",
    session_id: "",
    title: "",
    ...extra,
  };
}

function report(over: Partial<StorageReport> = {}): StorageReport {
  return {
    home: "/data",
    worktree_dir: "/tmp",
    categories: [
      category("sessions", 2048),
      category("memory", null, "memory: PermissionError"),
      category("logs", 500),
      category("cache", 3072),
      category("worktrees", 4096),
    ],
    worktrees: [
      worktree("/tmp/chimera-wt-a", 4000, "orphan", "owner_gone"),
      worktree("/tmp/chimera-wt-b", 96, "live", "this_process"),
    ],
    worktrees_counted_bytes: 4096,
    worktrees_unmeasured: 0,
    worktrees_limit_gb: 50,
    worktrees_over_limit: false,
    archive_idle_days: 14,
    archive_candidates: [],
    disks: [{ path: "/data", total: 1024 ** 4, free: 1024 ** 3 }],
    rotatable_logs: ["traces.jsonl", "scheduler/cron_traces.jsonl"],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getStorage).mockResolvedValue(report());
  vi.mocked(api.getTroubleshoot).mockResolvedValue({
    available: true,
    reset_word: "RESET",
    last_reset: null,
  });
});

describe("the storage card, worktrees", () => {
  const idle = {
    session_id: "s1",
    title: "Fix the login",
    idle_days: 20,
    path: "/wt/chimera-wt-x",
    bytes: 1024 ** 3,
  };

  it("warns when the worktrees are over the limit and says why each one is still here", async () => {
    vi.mocked(api.getStorage).mockResolvedValue(
      report({
        worktrees_over_limit: true,
        worktrees_counted_bytes: 60 * 1024 ** 3,
        worktrees: [
          worktree("/wt/chimera-wt-x", 1024 ** 3, "kept", "durable", {
            retention: "conversation",
            title: "Fix the login",
          }),
          worktree("/wt/chimera-wt-y", 5, "kept", "durable", {
            retention: "archived",
            retention_kept: "dirty",
          }),
        ],
      }),
    );
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    expect(await screen.findByRole("status")).toHaveTextContent(/above your 50 GB limit/);
    await userEvent.click(screen.getByText(/Why each worktree is still here/));
    expect(screen.getByText(/chimera-wt-x/)).toHaveTextContent("its conversation “Fix the login” is open");
    expect(screen.getByText(/chimera-wt-y/)).toHaveTextContent("uncommitted changes");
  });

  it("says a folder that was not counted is not measured, and offers to keep counting", async () => {
    vi.mocked(api.getStorage).mockResolvedValue(
      report({
        worktrees_unmeasured: 1,
        worktrees: [worktree("/wt/chimera-wt-z", null, "kept", "durable")],
      }),
    );
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    expect(await screen.findByText(/1 worktree folders have not been counted/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/Why each worktree is still here/));
    expect(screen.getByText(/chimera-wt-z/)).toHaveTextContent("not measured");
    await userEvent.click(screen.getByRole("button", { name: "Keep counting" }));
    await waitFor(() => expect(api.getStorage).toHaveBeenCalledTimes(2));
  });

  it("archives an idle conversation only after the second press, and calls nothing that removes", async () => {
    vi.mocked(api.getStorage).mockResolvedValue(report({ archive_candidates: [idle] }));
    vi.mocked(api.archiveCodeSession).mockResolvedValue({ id: "s1", archived_at: 1 });
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Archive" }));
    expect(api.archiveCodeSession).not.toHaveBeenCalled();
    expect(screen.getByText(/nothing is deleted now/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Yes, do it" }));
    await waitFor(() => expect(api.archiveCodeSession).toHaveBeenCalledWith("s1"));
    expect(api.pruneWorktrees).not.toHaveBeenCalled();
    expect(api.rotateLogs).not.toHaveBeenCalled();
    expect(api.clearCache).not.toHaveBeenCalled();
    expect(await screen.findByText("Archived. Nothing was deleted.")).toBeInTheDocument();
  });

  it("cancelling the archive offer archives nothing", async () => {
    vi.mocked(api.getStorage).mockResolvedValue(report({ archive_candidates: [idle] }));
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Archive" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(api.archiveCodeSession).not.toHaveBeenCalled();
  });
});

describe("the storage card", () => {
  it("says not measured for a category the server could not count, never 0", async () => {
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    const memory = await screen.findByText("Memory");
    const row = memory.closest("div");
    expect(row).toHaveTextContent("not measured");
    expect(row).not.toHaveTextContent(/\b0\s?B\b/);
    expect(screen.getByText("Conversations").closest("div")).toHaveTextContent("2 kB");
  });

  it("removes nothing until the second press, which names what is left alone", async () => {
    vi.mocked(api.pruneWorktrees).mockResolvedValue({
      removed: 1,
      bytes_freed: 4000,
      kept: 0,
      live: 1,
      failed: 0,
    });
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Remove orphaned worktrees" }));
    expect(api.pruneWorktrees).not.toHaveBeenCalled();
    expect(screen.getByText(/Worktrees of runs still working/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Yes, do it" }));
    await waitFor(() => expect(api.pruneWorktrees).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("status")).toHaveTextContent("Removed 1");
  });

  it("cancelling the confirmation removes nothing", async () => {
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Rotate traces" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(api.rotateLogs).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Yes, do it" })).not.toBeInTheDocument();
  });

  it("offers no prune when nothing is orphaned", async () => {
    vi.mocked(api.getStorage).mockResolvedValue(
      report({
        worktrees: [worktree("/tmp/chimera-wt-b", 96, "kept", "no_owner")],
      }),
    );
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "Remove orphaned worktrees" })).toBeDisabled();
  });

  it("clears the cache only after a second press that names its size and what is untouched", async () => {
    vi.mocked(api.clearCache).mockResolvedValue({
      removed: 1,
      bytes_freed: 3072,
      failed: 0,
      restarting: true,
    });
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Clear cache and restart" }));
    expect(api.clearCache).not.toHaveBeenCalled();
    const warning = screen.getByText(/cached model responses/);
    expect(warning).toHaveTextContent("3 kB");
    expect(warning).toHaveTextContent(/Conversations, memory, settings and keys are not touched/);

    await userEvent.click(screen.getByRole("button", { name: "Yes, do it" }));
    await waitFor(() => expect(api.clearCache).toHaveBeenCalledTimes(1));
    expect(api.rotateLogs).not.toHaveBeenCalled();
    expect(await screen.findByRole("status")).toHaveTextContent("The backend is restarting.");
  });

  it("offers no cache clear when there is no cache", async () => {
    vi.mocked(api.getStorage).mockResolvedValue(
      report({ categories: [category("cache", 0), category("logs", 500)] }),
    );
    render(<StorageCard worktreeDir="" onSave={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "Clear cache and restart" })).toBeDisabled();
  });

  it("saves the worktree folder and says where the next one actually goes", async () => {
    const onSave = vi.fn();
    render(<StorageCard worktreeDir="" onSave={onSave} />);

    expect(await screen.findByText("The next worktree goes to: /tmp")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Worktree folder"), "D:\\wts");
    // This row's own Save: the card has two since the branch prefix joined it (study 29, P8.1).
    const row = screen.getByLabelText("Worktree folder").closest("div")!;
    await userEvent.click(within(row).getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({ CHIMERA_WORKTREE_DIR: "D:\\wts" });
  });

  it("saves the branch prefix, and offers no save while it is unchanged", async () => {
    const onSave = vi.fn();
    render(<StorageCard worktreeDir="" branchPrefix="chimera" onSave={onSave} />);

    const field = await screen.findByLabelText("Branch prefix");
    expect(field).toHaveValue("chimera");
    const row = field.closest("div")!;
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(field);
    await userEvent.type(field, "team");
    await userEvent.click(within(row).getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({ CHIMERA_BRANCH_PREFIX: "team" });
  });
});

describe("the diagnostics card", () => {
  const diag: AppDiagnostics = {
    backend_version: "0.64.1",
    install_id: "k3x9q2abcdefghijklmnopqrstuvwxyz",
    python: "3.12.1",
    platform: "Windows-11",
    home: "C:\\app\\data",
    workspace: "C:\\app\\workspace",
    worktree_dir: "C:\\Temp",
    crash: { path: "C:\\app\\backend-crash.txt", modified: "2026-10-01T10:00:00+00:00", text: "ValueError: boom" },
    report: "Chimera diagnostics\nbackend version: 0.64.1",
  };

  it("shows the versions, the paths and the last crash report", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue(diag);
    render(<DiagnosticsCard />);

    expect(await screen.findByText("0.64.1")).toBeInTheDocument();
    expect(screen.getByText("C:\\app\\data")).toBeInTheDocument();
    expect(screen.getByText(/Last crash report/)).toBeInTheDocument();
    expect(screen.getByText("ValueError: boom")).toBeInTheDocument();
  });

  it("copies exactly the server's scrubbed text", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue({ ...diag, crash: null });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<DiagnosticsCard />);

    expect(await screen.findByText("No crash report.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));

    expect(writeText).toHaveBeenCalledWith(diag.report);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("promises only what the scrub does, and asks for a read before posting", async () => {
    // Review finding: the card said credentials ARE removed, and the server removes known formats —
    // a secret with no name beside it and no recognisable shape survives any list. Whoever reads the
    // card decides whether to paste the text into a public issue on the strength of this sentence.
    vi.mocked(api.getAppDiagnostics).mockResolvedValue(diag);
    render(<DiagnosticsCard />);

    const claim = await screen.findByText(/credential formats are removed/);
    expect(claim).toHaveTextContent(/Known credential formats/);
    expect(claim).toHaveTextContent(/review the text before posting it publicly/);
  });

  it("copies the installation id on its own", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue({ ...diag, crash: null });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<DiagnosticsCard />);

    expect(await screen.findByText(diag.install_id)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy installation ID" }));

    expect(writeText).toHaveBeenCalledWith(diag.install_id);
  });

  it("asks for a folder by name", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue(diag);
    vi.mocked(api.openAppFolder).mockResolvedValue({ requested: true });
    render(<DiagnosticsCard />);

    await userEvent.click(await screen.findByRole("button", { name: "Open logs folder" }));
    await userEvent.click(screen.getByRole("button", { name: "Open data folder" }));

    expect(vi.mocked(api.openAppFolder).mock.calls).toEqual([["logs"], ["data"]]);
  });

  it("resets only once the exact word is typed, and says the data is moved, not deleted", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue(diag);
    vi.mocked(api.resetAppData).mockResolvedValue({ requested: true });
    render(<DiagnosticsCard />);

    await userEvent.click(await screen.findByRole("button", { name: "Reset app data…" }));
    expect(screen.getByText(/dated backup/)).toHaveTextContent(/Nothing is deleted/);
    const confirm = screen.getByRole("button", { name: "Move data to backup and restart" });
    expect(confirm).toBeDisabled();
    const field = screen.getByLabelText("Type RESET to confirm");
    await userEvent.type(field, "reset");
    expect(confirm).toBeDisabled();
    await userEvent.clear(field);
    await userEvent.type(field, "RESET");
    expect(api.resetAppData).not.toHaveBeenCalled();
    await userEvent.click(confirm);

    await waitFor(() => expect(api.resetAppData).toHaveBeenCalledWith("RESET"));
    expect(await screen.findByRole("status")).toHaveTextContent(/empty data folder/);
  });

  it("turns the shell actions off, and says why, without the desktop app", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue(diag);
    vi.mocked(api.getTroubleshoot).mockResolvedValue({
      available: false,
      reset_word: "RESET",
      last_reset: null,
    });
    render(<DiagnosticsCard />);

    expect(await screen.findByText(/need the desktop app/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open logs folder" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset app data…" })).toBeDisabled();
  });

  it("shows where the last reset put the data", async () => {
    vi.mocked(api.getAppDiagnostics).mockResolvedValue(diag);
    vi.mocked(api.getTroubleshoot).mockResolvedValue({
      available: true,
      reset_word: "RESET",
      last_reset: { moved: true, detail: "C:\app\data-backup-20261016-134507", at: 1 },
    });
    render(<DiagnosticsCard />);

    expect(await screen.findByText(/data-backup-20261016-134507/)).toHaveTextContent(
      "The last reset moved the data to:",
    );
  });
});
