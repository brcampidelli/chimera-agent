import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Tasks } from "@/components/Tasks";
import { getKanban, getProjects } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addKanbanCard: vi.fn(),
  approveProject: vi.fn(),
  denyProject: vi.fn(),
  getAgentRegistry: vi.fn(async () => []),
  getKanban: vi.fn(),
  getProject: vi.fn(),
  getProjects: vi.fn(async () => []),
  moveKanbanCard: vi.fn(),
  removeKanbanCard: vi.fn(),
  startProject: vi.fn(),
  stepProject: vi.fn(),
  streamKanbanRun: vi.fn(),
}));

function card(over: Record<string, unknown> = {}) {
  return {
    id: "c1",
    title: "adicionar meta viewport",
    action: "adicionar meta viewport",
    column: "done",
    success: true,
    risk: null,
    depends_on: [],
    lane: "solve",
    verify: null,
    result: "",
    ...over,
  };
}

/**
 * What the lane answered.
 *
 * `TaskCardOut.result` has been on the wire since the field existed — "What the lane answered, once
 * it has run" — and this screen never rendered it. So a board could work every card it had and show
 * only that they changed column: the outcome of a run that cost money arrived in the response and
 * was discarded on arrival, and the only way to read it was the CLI.
 */
describe("a worked card shows what came back", () => {
  beforeEach(() => {
    vi.mocked(getProjects).mockReset().mockResolvedValue([]);
    vi.mocked(getKanban).mockReset();
  });

  it("shows the answer, folded", async () => {
    vi.mocked(getKanban).mockResolvedValue({
      backlog: [],
      doing: [],
      review: [],
      done: [card({ result: "Adicionei a meta viewport no index.html." })],
      blocked: [],
    } as never);
    renderWithProviders(<Tasks />);

    // Folded: a board stays a board, and an answer can be long.
    const resumo = await screen.findByText(/what it answered/i);
    await userEvent.click(resumo);

    expect(screen.getByText("Adicionei a meta viewport no index.html.")).toBeTruthy();
  });

  it("says nothing on a card that has not run", async () => {
    // The control. Rendering the block unconditionally would put an empty disclosure on every
    // card in the backlog, which reads as "it ran and answered nothing".
    vi.mocked(getKanban).mockResolvedValue({
      backlog: [card({ column: "backlog", success: null })],
      doing: [],
      review: [],
      done: [],
      blocked: [],
    } as never);
    renderWithProviders(<Tasks />);

    await screen.findByText("adicionar meta viewport");
    expect(screen.queryByText(/what it answered/i)).toBeNull();
  });
});
