import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Memory } from "@/components/Memory";
import { I18nProvider } from "@/lib/i18n";

const getMemory = vi.fn();
const getMemoryLayers = vi.fn();
const editMemory = vi.fn();
const exportMemory = vi.fn();
const previewClaudeImport = vi.fn();
const applyClaudeImport = vi.fn();
const previewConsolidation = vi.fn();
const applyConsolidation = vi.fn();

vi.mock("@/lib/api", () => ({
  getMemory: (...a: unknown[]) => getMemory(...a),
  getMemoryLayers: (...a: unknown[]) => getMemoryLayers(...a),
  addMemory: vi.fn(),
  deleteMemory: vi.fn(),
  editMemory: (...a: unknown[]) => editMemory(...a),
  exportMemory: (...a: unknown[]) => exportMemory(...a),
  previewClaudeImport: (...a: unknown[]) => previewClaudeImport(...a),
  applyClaudeImport: (...a: unknown[]) => applyClaudeImport(...a),
  previewConsolidation: (...a: unknown[]) => previewConsolidation(...a),
  applyConsolidation: (...a: unknown[]) => applyConsolidation(...a),
}));

const tainted = {
  id: "t1",
  content: "the build uses poetry",
  kind: "semantic",
  provenance: "tainted",
  source: "claude",
  project: null,
};

function renderMemory(facts: unknown[] = [tainted]) {
  getMemory.mockResolvedValue(facts);
  getMemoryLayers.mockResolvedValue({ layers: [], total: 0, by_source: [] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <I18nProvider>
      <QueryClientProvider client={qc}>
        <Memory />
      </QueryClientProvider>
    </I18nProvider>,
  );
}

// jsdom has no blob URLs; the export tests install their own and these put things back.
const originalCreate = URL.createObjectURL;
const originalRevoke = URL.revokeObjectURL;

afterEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
});

/**
 * Study 29, P7.4 — the Memory screen can correct a fact, take the store out, bring Claude's notes in
 * and merge similar facts. The import and the merge write only what the person ticked after a
 * preview; nothing is ticked for them.
 */
describe("Memory — edit, export, import, merge", () => {
  it("edits a fact in place and says the unverified label stays", async () => {
    editMemory.mockResolvedValue({ ...tainted, content: "the build uses uv" });
    renderMemory();

    await screen.findByText(tainted.content);
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    expect(screen.getByText(/keeps the .unverified. label/i)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: /^edit$/i }), {
      target: { value: "the build uses uv" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(editMemory).toHaveBeenCalledWith("t1", "the build uses uv"));
  });

  it("opens a multi-line fact with its lines intact and nothing to save until it changes", async () => {
    const multi = { ...tainted, content: "first line\nsecond line" };
    renderMemory([multi]);

    await screen.findByText(/first line/);
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

    // An <input> would have flattened this to one line and enabled Save on an untouched fact.
    expect(screen.getByRole("textbox", { name: /^edit$/i })).toHaveValue("first line\nsecond line");
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("exports to a file on this computer", async () => {
    exportMemory.mockResolvedValue({
      format: "json",
      filename: "chimera-memory.json",
      media_type: "application/json",
      count: 1,
      content: "{}",
    });
    const made: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => {
      made.push(b);
      return "blob:memory";
    });
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = vi.fn();
    const clicked: string[] = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      });
    renderMemory();

    fireEvent.click(await screen.findByRole("button", { name: /export json/i }));

    await waitFor(() => expect(clicked).toEqual(["chimera-memory.json"]));
    expect(exportMemory).toHaveBeenCalledWith("json");
    expect(await made[0].text()).toBe("{}");
    click.mockRestore();
  });

  it("copies the export to the clipboard, and says so, when the download cannot start", async () => {
    exportMemory.mockResolvedValue({
      format: "markdown",
      filename: "chimera-memory.md",
      media_type: "text/markdown",
      count: 1,
      content: "# memory",
    });
    URL.createObjectURL = () => {
      throw new Error("no blob URLs here");
    };
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderMemory();

    fireEvent.click(await screen.findByRole("button", { name: /export markdown/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("# memory"));
    expect(await screen.findByText(/copied to the clipboard/i)).toBeInTheDocument();
  });

  it("imports from Claude only the facts that were ticked, and ticks none by itself", async () => {
    previewClaudeImport.mockResolvedValue({
      path: "/home/x/.claude",
      files: ["CLAUDE.md"],
      notes: [],
      candidates: [
        { content: "Answer in Portuguese", file: "CLAUDE.md", known: false },
        { content: "Keep commits small", file: "CLAUDE.md", known: false },
        { content: "Already here", file: "CLAUDE.md", known: true },
      ],
    });
    applyClaudeImport.mockResolvedValue({ written: 1, ignored: 0, counts: { ADD: 1, UPDATE: 0, NOOP: 0 } });
    renderMemory([]);

    fireEvent.click((await screen.findAllByRole("button", { name: /preview/i }))[0]);
    const first = await screen.findByRole("checkbox", { name: /Answer in Portuguese/ });
    const apply = screen.getByRole("button", { name: /import 0 selected/i });
    expect(apply).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Already here/ })).toBeDisabled();

    fireEvent.click(first);
    fireEvent.click(screen.getByRole("button", { name: /import 1 selected/i }));

    // The folder the server resolved for the PREVIEW, not whatever the field says now.
    await waitFor(() =>
      expect(applyClaudeImport).toHaveBeenCalledWith("/home/x/.claude", ["Answer in Portuguese"]),
    );
  });

  it("says where each imported fact would apply and leaves stray project notes out of select-all", async () => {
    previewClaudeImport.mockResolvedValue({
      path: "/home/x/.claude",
      files: ["CLAUDE.md"],
      notes: [],
      candidates: [
        { content: "Global rule", file: "CLAUDE.md", known: false, project: null, claude_project: "" },
        {
          content: "Repo uses hue 185",
          file: "projects/C--repo/memory/MEMORY.md",
          known: false,
          project: "/work/repo",
          claude_project: "C--repo",
        },
        {
          content: "Gone repo deploys Fridays",
          file: "projects/C--gone/memory/MEMORY.md",
          known: false,
          project: null,
          claude_project: "C--gone",
        },
      ],
    });
    renderMemory([]);

    fireEvent.click((await screen.findAllByRole("button", { name: /preview/i }))[0]);
    await screen.findByRole("checkbox", { name: /Global rule/ });
    expect(screen.getByText(/no registered folder matches/i)).toHaveTextContent("C--gone");
    expect(screen.getByTitle("/work/repo")).toHaveTextContent("repo");
    fireEvent.click(screen.getByRole("button", { name: /select all new/i }));

    expect(screen.getByRole("checkbox", { name: /Global rule/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Repo uses hue 185/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Gone repo deploys Fridays/ })).not.toBeChecked();
  });

  it("drops the preview when the folder changes, so an import cannot write from another folder", async () => {
    previewClaudeImport.mockResolvedValue({
      path: "/home/x/.claude",
      files: ["CLAUDE.md"],
      notes: [],
      candidates: [{ content: "Answer in Portuguese", file: "CLAUDE.md", known: false }],
    });
    renderMemory([]);

    fireEvent.click((await screen.findAllByRole("button", { name: /preview/i }))[0]);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Answer in Portuguese/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /import from claude/i }), {
      target: { value: "/somewhere/else" },
    });

    expect(screen.queryByRole("checkbox", { name: /Answer in Portuguese/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /import \d+ selected/i })).not.toBeInTheDocument();
    expect(applyClaudeImport).not.toHaveBeenCalled();
  });

  it("merges only the ticked group, and not at all without a model", async () => {
    const group = (ids: string[]) => ({
      kind: "semantic",
      project: null,
      unverified: true,
      items: ids.map((id) => ({ ...tainted, id, content: `fact ${id}` })),
    });
    previewConsolidation.mockResolvedValueOnce({ groups: [group(["a", "b"])], can_answer: false });
    renderMemory([]);

    const previews = await screen.findAllByRole("button", { name: /preview/i });
    fireEvent.click(previews[1]);
    const box = await screen.findByRole("checkbox", { name: /fact a/ });
    fireEvent.click(box);
    expect(screen.getByRole("button", { name: /merge 1 group/i })).toBeDisabled();
    expect(screen.getByText(/no model is configured/i)).toBeInTheDocument();
    // What the merged fact will be, said before the merge: unverified, because a member is.
    expect(screen.getByText(/the merged fact will be too/i)).toBeInTheDocument();

    previewConsolidation.mockResolvedValueOnce({
      groups: [group(["a", "b"]), group(["c", "d"])],
      can_answer: true,
    });
    applyConsolidation.mockResolvedValue({ merged: 1, skipped: 0, stale: 0, removed: 1, usd: 0 });
    fireEvent.click(previews[1]);
    const second = await screen.findByRole("checkbox", { name: /fact c/ });
    fireEvent.click(second);
    fireEvent.click(screen.getByRole("button", { name: /merge 1 group/i }));

    await waitFor(() => expect(applyConsolidation).toHaveBeenCalledWith([["c", "d"]]));
    const status = await screen.findByRole("status");
    expect(within(status).getByText(/merged 1 group/i)).toBeInTheDocument();
  });

  it("says a group the model answered blank was left alone, not merged", async () => {
    previewConsolidation.mockResolvedValueOnce({
      groups: [
        {
          kind: "semantic",
          project: null,
          unverified: true,
          items: ["a", "b"].map((id) => ({ ...tainted, id, content: `fact ${id}` })),
        },
      ],
      can_answer: true,
    });
    applyConsolidation.mockResolvedValue({ merged: 0, skipped: 1, stale: 0, removed: 0, usd: 0.001 });
    renderMemory([]);

    const previews = await screen.findAllByRole("button", { name: /preview/i });
    fireEvent.click(previews[1]);
    fireEvent.click(await screen.findByRole("checkbox", { name: /fact a/ }));
    fireEvent.click(screen.getByRole("button", { name: /merge 1 group/i }));

    // Before, the route said merged: 1 for this, and the screen read "Merged 1 group(s)".
    expect(await screen.findByText(/answered 1 group\(s\) with nothing/i)).toBeInTheDocument();
    expect(within(screen.getByRole("status")).getByText(/merged 0 group/i)).toBeInTheDocument();
  });
});
