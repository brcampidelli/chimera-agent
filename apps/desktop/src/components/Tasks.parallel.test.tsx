import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Tasks } from "@/components/Tasks";
import { renderWithProviders as render } from "@/test/utils";

/**
 * Working several cards at once, from the board.
 *
 * The number is remembered because it is a property of this board and this machine, not of one
 * press — and the conflict notice stays after the run because those cards went green and not all
 * of their edits survived the merge, which is the one thing a finished board would otherwise hide.
 */
vi.mock("@/lib/api", () => ({
  addKanbanCard: vi.fn(),
  approveProject: vi.fn(),
  denyProject: vi.fn(),
  getAgentRegistry: vi.fn(),
  getKanban: vi.fn(),
  getProject: vi.fn(),
  getProjects: vi.fn(),
  moveKanbanCard: vi.fn(),
  removeKanbanCard: vi.fn(),
  startProject: vi.fn(),
  stepProject: vi.fn(),
  streamKanbanRun: vi.fn(),
}));

const api = await import("@/lib/api");

describe("dispatching the board in parallel", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(api.getKanban).mockResolvedValue({ backlog: [], doing: [], review: [], done: [] });
    vi.mocked(api.getProjects).mockResolvedValue([]);
    vi.mocked(api.getAgentRegistry).mockResolvedValue([]);
  });

  it("sends the chosen number of workers, and remembers it", async () => {
    vi.mocked(api.streamKanbanRun).mockImplementation(async (_req, h) => h.onDone?.({ worked: 0 }));
    const user = userEvent.setup();
    render(<Tasks />);

    const seletor = await screen.findByRole("combobox");
    await user.selectOptions(seletor, "4");
    await user.click(screen.getByRole("button", { name: /run/i }));

    await waitFor(() =>
      expect(api.streamKanbanRun).toHaveBeenCalledWith(
        expect.objectContaining({ workers: 4 }),
        expect.anything(),
        expect.anything(),
      ),
    );
    expect(localStorage.getItem("chimera.kanban.workers")).toBe("4");
  });

  it("defaults to one, which is the board that shipped", async () => {
    vi.mocked(api.streamKanbanRun).mockImplementation(async (_req, h) => h.onDone?.({ worked: 0 }));
    const user = userEvent.setup();
    render(<Tasks />);

    await user.click(await screen.findByRole("button", { name: /run/i }));
    await waitFor(() =>
      expect(api.streamKanbanRun).toHaveBeenCalledWith(
        expect.objectContaining({ workers: 1 }),
        expect.anything(),
        expect.anything(),
      ),
    );
  });

  it("keeps the conflict notice up after the run finishes", async () => {
    // Two cards succeeded and both changed the same file. The board is green; only this says that
    // one of those versions is gone.
    vi.mocked(api.streamKanbanRun).mockImplementation(async (_req, h) => {
      h.onConflict?.(["src/app.ts", "README.md"]);
      h.onDone?.({ worked: 2 });
    });
    const user = userEvent.setup();
    render(<Tasks />);

    await user.click(await screen.findByRole("button", { name: /run/i }));
    expect(await screen.findByText(/more than one card/i)).toBeInTheDocument();
    expect(screen.getByText(/src\/app\.ts/)).toBeInTheDocument();
  });
});
